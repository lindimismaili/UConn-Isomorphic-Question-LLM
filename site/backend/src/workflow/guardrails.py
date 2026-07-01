"""Workflow guardrails: fail-closed input + content safety for the pipeline.

Layered defense (block on DETECTION; infra errors are configurable via
``guardrail_fail_closed()``):

  G1  OpenAI Moderation (``omni-moderation-latest``) on the raw instruction and
      on the extracted source text — free, deterministic.
  G2  A jailbreak / prompt-injection classifier on the raw instruction, run by
      the engine BEFORE the container is created, so an attack never spins one up.
  G3  An academic / physics on-topic + is-a-question classifier on the extracter
      output — the authoritative "is this genuine question content" check.

Config lives in ``agents.json`` ``_config.guardrails`` (read through the
registry); classifier instructions live in
``data/prompts/{input,content}_guardrail.txt``. No agent keys or model ids are
hardcoded here. The two ``check_*`` coroutine signatures are the load-bearing
contract the engine calls — keep them stable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from agents import Agent, ModelSettings, Runner
from agents.exceptions import ModelRefusalError
from agents.logger import logger
from openai.types.shared.reasoning import Reasoning
from pydantic import BaseModel

from ..helpers import load_prompt
from ..paths import PROMPTS_DIR
from ..registry import (
    guardrail_block_off_topic,
    guardrail_fail_closed,
    guardrail_max_questions_sampled,
    guardrail_model,
    guardrail_moderation_model,
    guardrail_physics_min_confidence,
    guardrail_reasoning,
    guardrails_enabled,
)

ToThread = Callable[..., Awaitable[Any]]
EmitLog = Callable[[str], None]

_MODERATION_MAX_CHARS = 20000
_INPUT_GUARDRAIL_PROMPT = "input_guardrail"
_CONTENT_GUARDRAIL_PROMPT = "content_guardrail"
_VALID_REASONING = {"low", "medium", "high", "auto"}
_VALID_INJECTION_CATEGORIES = {"jailbreak", "prompt_injection", "off_goal"}


@dataclass
class GuardrailDecision:
    """Result of a guardrail check.

    ``allowed`` is the only field the engine branches on. When False the engine
    finalizes the run as ``blocked`` and records ``category``/``reason``/``stage``.
    """

    allowed: bool
    category: str = ""     # "" | "moderation" | "jailbreak" | "prompt_injection" | "off_goal" | "off_topic" | "not_a_question"
    reason: str = ""
    stage: str = ""        # "input" | "content"
    detail: dict = field(default_factory=dict)


class _InjectionCheck(BaseModel):
    """Structured output of the input (jailbreak / prompt-injection) classifier."""

    is_blocked: bool
    category: str = "none"   # "jailbreak" | "prompt_injection" | "off_goal" | "none"
    reason: str = ""


class _AcademicCheck(BaseModel):
    """Structured output of the content (academic / physics) classifier."""

    is_academic_questions: bool
    physics_oriented: bool
    confidence: float = 0.0
    reason: str = ""


def _to_plain(value: Any) -> Any:
    """Best-effort convert an SDK object into JSON-serializable data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            return str(value)
    if isinstance(value, dict):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(v) for v in value]
    return str(value)


def _reasoning_settings() -> ModelSettings:
    """ModelSettings for the guardrail classifier agents."""
    level = guardrail_reasoning()
    reasoning = Reasoning(effort=level) if level in _VALID_REASONING else None
    return ModelSettings(reasoning=reasoning)


def _build_guardrail_agent(name: str, prompt_key: str, output_type: type[BaseModel]) -> Agent:
    """Build a lightweight classifier agent from a prompt file + registry model."""
    return Agent(
        name=name,
        instructions=load_prompt(PROMPTS_DIR, logger, prompt_key),
        model=guardrail_model(),
        output_type=output_type,
        model_settings=_reasoning_settings(),
    )


def _moderate_text_sync(client: Any, text: str) -> Optional[dict]:
    """Run OpenAI moderation synchronously; return a detail dict (or None if empty)."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    resp = client.moderations.create(
        model=guardrail_moderation_model(),
        input=cleaned[:_MODERATION_MAX_CHARS],
    )
    result = resp.results[0]
    return {
        "flagged": bool(result.flagged),
        "categories": _to_plain(getattr(result, "categories", None)),
        "category_scores": _to_plain(getattr(result, "category_scores", None)),
    }


async def _run_moderation(client: Any, text: str, to_thread: ToThread) -> Optional[dict]:
    """Run moderation off the event loop; annotate infra failures as ``__error__``."""
    try:
        return await to_thread(_moderate_text_sync, client, text)
    except Exception as exc:
        logger.warning("Moderation guardrail infra error: %s", exc)
        return {"__error__": str(exc)}


def _sample_questions(extracter_output: Any) -> list[dict]:
    """Compact, capped sample of extracted questions for the content classifier."""
    questions = getattr(extracter_output, "questions", None) or []
    limit = guardrail_max_questions_sampled()
    sample: list[dict] = []
    for question in questions[:limit]:
        stem = getattr(question, "stem", "") or ""
        sample.append(
            {
                "stem": stem[:600],
                "question_type": getattr(question, "question_type", "") or "",
                "num_options": len(getattr(question, "options", []) or []),
            }
        )
    return sample


async def check_input_guardrails(
    *,
    client: Any,
    prompt: str,
    file_names: list[str],
    to_thread: ToThread,
    emit_log: Optional[EmitLog] = None,
) -> GuardrailDecision:
    """Screen the raw user instruction before the container is created.

    G1 moderation + G2 jailbreak/prompt-injection classifier. Blocks on detection;
    infra errors fail open unless ``guardrail_fail_closed()``.
    """
    if not guardrails_enabled():
        return GuardrailDecision(allowed=True, stage="input")

    prompt_text = (prompt or "").strip()

    # G1 — moderation on the raw instruction.
    mod = await _run_moderation(client, prompt_text, to_thread)
    if mod:
        if mod.get("__error__"):
            if guardrail_fail_closed():
                return GuardrailDecision(
                    allowed=False, category="moderation", stage="input", detail=mod,
                    reason="Moderation service unavailable; blocking as a precaution.",
                )
        elif mod.get("flagged"):
            return GuardrailDecision(
                allowed=False, category="moderation", stage="input", detail=mod,
                reason="Your request was flagged by content moderation.",
            )

    # G2 — jailbreak / prompt-injection classifier (only when there is an instruction).
    if prompt_text:
        check: Optional[_InjectionCheck] = None
        try:
            agent = _build_guardrail_agent("Input Guardrail", _INPUT_GUARDRAIL_PROMPT, _InjectionCheck)
            payload = json.dumps({"user_instruction": prompt_text, "uploaded_files": file_names or []})
            result = await Runner.run(agent, input=payload, max_turns=2)
            check = result.final_output
        except ModelRefusalError as exc:
            # A refusal while classifying the input is itself a strong signal.
            return GuardrailDecision(
                allowed=False, category="jailbreak", stage="input",
                reason="Your request could not be safely processed.",
                detail={"refusal": getattr(exc, "refusal", str(exc))},
            )
        except Exception as exc:
            logger.warning("Input guardrail classifier error: %s", exc)
            if guardrail_fail_closed():
                return GuardrailDecision(
                    allowed=False, category="jailbreak", stage="input",
                    reason="Input safety check unavailable; blocking as a precaution.",
                    detail={"error": str(exc)},
                )
        if check is not None and check.is_blocked:
            category = check.category if check.category in _VALID_INJECTION_CATEGORIES else "jailbreak"
            return GuardrailDecision(
                allowed=False, category=category, stage="input", detail=check.model_dump(),
                reason=check.reason or "Your request was blocked because it tries to misuse the tool.",
            )

    return GuardrailDecision(allowed=True, stage="input")


async def check_content_guardrails(
    *,
    client: Any,
    extracter_output: Any,
    normalizer_output: Any,
    to_thread: ToThread,
    emit_log: Optional[EmitLog] = None,
) -> GuardrailDecision:
    """Confirm the extracted material is genuine academic (physics) question content.

    G1 moderation on extracted text + G3 academic/physics classifier. Blocks on
    detection; infra errors fail open unless ``guardrail_fail_closed()``.
    """
    if not guardrails_enabled():
        return GuardrailDecision(allowed=True, stage="content")

    questions = getattr(extracter_output, "questions", None) or []
    if not questions:
        return GuardrailDecision(
            allowed=False, category="not_a_question", stage="content",
            reason="No academic questions could be found in the uploaded material.",
        )

    # G1 — moderation on extracted source text.
    combined = "\n".join(
        (getattr(q, "source_raw", "") or getattr(q, "stem", "") or "") for q in questions
    )
    mod = await _run_moderation(client, combined, to_thread)
    if mod:
        if mod.get("__error__"):
            if guardrail_fail_closed():
                return GuardrailDecision(
                    allowed=False, category="moderation", stage="content", detail=mod,
                    reason="Moderation service unavailable; blocking as a precaution.",
                )
        elif mod.get("flagged"):
            return GuardrailDecision(
                allowed=False, category="moderation", stage="content", detail=mod,
                reason="The uploaded material was flagged by content moderation.",
            )

    # G3 — academic / physics on-topic classifier.
    try:
        agent = _build_guardrail_agent("Content Guardrail", _CONTENT_GUARDRAIL_PROMPT, _AcademicCheck)
        payload = json.dumps({"extracted_questions": _sample_questions(extracter_output)})
        result = await Runner.run(agent, input=payload, max_turns=2)
        check: _AcademicCheck = result.final_output
    except ModelRefusalError as exc:
        return GuardrailDecision(
            allowed=False, category="not_a_question", stage="content",
            reason="The uploaded material could not be safely processed.",
            detail={"refusal": getattr(exc, "refusal", str(exc))},
        )
    except Exception as exc:
        logger.warning("Content guardrail classifier error: %s", exc)
        if guardrail_fail_closed():
            return GuardrailDecision(
                allowed=False, category="not_a_question", stage="content",
                reason="Content safety check unavailable; blocking as a precaution.",
                detail={"error": str(exc)},
            )
        return GuardrailDecision(allowed=True, stage="content")

    if not check.is_academic_questions:
        return GuardrailDecision(
            allowed=False, category="not_a_question", stage="content", detail=check.model_dump(),
            reason=check.reason or "The uploaded material is not a set of academic questions.",
        )
    if (
        guardrail_block_off_topic()
        and not check.physics_oriented
        and check.confidence >= guardrail_physics_min_confidence()
    ):
        return GuardrailDecision(
            allowed=False, category="off_topic", stage="content", detail=check.model_dump(),
            reason=check.reason or "The uploaded questions are not physics-oriented.",
        )

    return GuardrailDecision(allowed=True, stage="content")
