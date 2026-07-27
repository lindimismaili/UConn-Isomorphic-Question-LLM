"""Workflow guardrails: fail-closed input + content safety for the pipeline.

Layered defense (block on DETECTION; infra errors are configurable via
``guardrail_fail_closed()``):

  G1  OpenAI Moderation (``omni-moderation-latest``) on the raw instruction and
      on the extracted source text through the dedicated moderation endpoint.
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
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from agents import Agent, ModelSettings, RunConfig, Runner
from agents.exceptions import ModelRefusalError
from agents.logger import logger
from openai.types.shared.reasoning import Reasoning
from pydantic import BaseModel, ConfigDict, Field

from ..helpers import load_prompt
from ..paths import PROMPTS_DIR
from ..provider_timeouts import (
    ProviderDeadlineExceeded,
    await_cancellation_safe_blocking_call,
    await_with_deadline,
    guardrail_max_classifier_requests,
    guardrail_stage_timeout_seconds,
    guardrail_timeout_seconds,
)
from ..registry import (
    guardrail_block_off_topic,
    guardrail_fail_closed,
    guardrail_max_output_tokens,
    guardrail_max_questions_sampled,
    guardrail_model,
    guardrail_moderation_model,
    guardrail_physics_min_confidence,
    guardrail_reasoning,
    guardrails_enabled,
)
from ..safety import (
    MAX_INSTRUCTION_CHARS,
    safe_exception_category,
    safe_exception_message,
    validate_instruction_text,
)
from .agents import validate_workflow_assets

ToThread = Callable[..., Awaitable[Any]]
EmitLog = Callable[[str], None]

_MODERATION_MAX_CHARS = 20000
_MODERATION_BATCH_SIZE = 16
_MAX_GUARDRAIL_TEXT_CHARS = 1_000_000
# Backward-compatible private alias for focused guardrail tests and diagnostics;
# the authoritative value lives at the shared API/CLI safety boundary.
_MAX_INSTRUCTION_CHARS = MAX_INSTRUCTION_CHARS
_MAX_UNTRUSTED_PAYLOAD_CHARS = 100_000
_MAX_CONTENT_QUESTIONS = 200
_MAX_OPTIONS_PER_QUESTION = 64
_MAX_PARTS_PER_QUESTION = 32
_MAX_OPTIONS_PER_PART = 64
_MAX_CONTENT_COLLECTION_ITEMS = 256
_MAX_CONTENT_NODES = 30_000
_MAX_CONTENT_STRING_FIELDS = 10_000
_MAX_CONTENT_TEXT_BYTES = 256 * 1024
_CONTENT_CLASSIFIER_MAX_REQUEST_BYTES = 32 * 1024
_CONTENT_CLASSIFIER_TEXT_FRAGMENT_BYTES = 8 * 1024
_CONTENT_CLASSIFIER_FRAGMENT_OVERLAP_BYTES = 512
# These ceilings exceed ordinary exam/question shapes while bounding malicious
# or malformed model output before it can amplify classifier calls.
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

    model_config = ConfigDict(extra="forbid")

    is_blocked: bool
    category: str = "none"   # "jailbreak" | "prompt_injection" | "off_goal" | "none"
    reason: str = Field(default="", max_length=500)


class _AcademicCheck(BaseModel):
    """Structured output of the content (academic / physics) classifier."""

    model_config = ConfigDict(extra="forbid")

    is_academic_questions: bool
    physics_oriented: bool
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    has_prompt_injection: bool = False
    injection_reason: str = Field(default="", max_length=500)
    reason: str = Field(default="", max_length=500)


class _ContentBoundsError(ValueError):
    """A structural or byte limit that makes full content screening unsafe."""

    def __init__(self, limit: str):
        super().__init__(limit)
        self.limit = limit


class _ClassifierBudgetExceeded(ValueError):
    """A hard classifier call or wall-clock budget that must never fail open."""

    safe_category = "GuardrailClassifierBudgetExceeded"

    def __init__(self, limit: str):
        super().__init__(limit)
        self.limit = limit


@dataclass
class _ClassifierBudget:
    """Aggregate request and wall-clock budget for one classifier phase."""

    wall_clock_seconds: float
    request_timeout_seconds: float
    max_requests: int
    started_at: float | None = None
    calls: int = 0

    def __post_init__(self) -> None:
        if self.started_at is None:
            self.started_at = time.monotonic()

    async def run(
        self,
        awaitable_factory: Callable[[], Awaitable[Any]],
        *,
        operation: str,
    ) -> Any:
        if self.calls >= self.max_requests:
            raise _ClassifierBudgetExceeded("classifier_request_budget")

        assert self.started_at is not None
        remaining = self.wall_clock_seconds - (time.monotonic() - self.started_at)
        if remaining <= 0:
            raise _ClassifierBudgetExceeded("classifier_wall_clock_budget")

        aggregate_limited = remaining < self.request_timeout_seconds
        timeout_seconds = min(remaining, self.request_timeout_seconds)
        self.calls += 1
        try:
            return await await_with_deadline(
                awaitable_factory(),
                operation=operation,
                timeout_seconds=timeout_seconds,
            )
        except ProviderDeadlineExceeded as exc:
            limit = (
                "classifier_wall_clock_budget"
                if aggregate_limited
                else "classifier_request_timeout"
            )
            raise _ClassifierBudgetExceeded(limit) from exc


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
    return ModelSettings(
        max_tokens=guardrail_max_output_tokens(),
        reasoning=reasoning,
        store=False,
    )


def _tracing_disabled() -> bool:
    """Keep untrusted source text out of SDK traces unless explicitly enabled."""
    raw = os.getenv("OPENAI_AGENTS_DISABLE_TRACING")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _run_config() -> RunConfig:
    return RunConfig(
        tracing_disabled=_tracing_disabled(),
        trace_include_sensitive_data=False,
    )


def _build_guardrail_agent(name: str, prompt_key: str, output_type: type[BaseModel]) -> Agent:
    """Build a lightweight classifier agent from a prompt file + registry model."""
    return Agent(
        name=name,
        instructions=load_prompt(PROMPTS_DIR, logger, prompt_key),
        model=guardrail_model(),
        output_type=output_type,
        model_settings=_reasoning_settings(),
    )


def _moderate_text_sync(
    client: Any,
    text: str,
    timeout_seconds: float,
) -> Optional[dict]:
    """Moderate every bounded chunk and return the first flagged result."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    if len(cleaned) > _MAX_GUARDRAIL_TEXT_CHARS:
        raise ValueError(
            f"guardrail input exceeds {_MAX_GUARDRAIL_TEXT_CHARS} characters"
        )

    chunks = [
        cleaned[offset : offset + _MODERATION_MAX_CHARS]
        for offset in range(0, len(cleaned), _MODERATION_MAX_CHARS)
    ]
    deadline = time.monotonic() + timeout_seconds
    for batch_start in range(0, len(chunks), _MODERATION_BATCH_SIZE):
        batch = chunks[batch_start : batch_start + _MODERATION_BATCH_SIZE]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderDeadlineExceeded(
                operation="Moderation guardrail",
                timeout_seconds=timeout_seconds,
            )
        resp = client.moderations.create(
            model=guardrail_moderation_model(),
            input=batch,
            timeout=remaining,
        )
        for batch_index, result in enumerate(resp.results):
            if result.flagged:
                return {
                    "flagged": True,
                    "chunk": batch_start + batch_index + 1,
                    "chunks_scanned": len(chunks),
                    "categories": _to_plain(getattr(result, "categories", None)),
                    "category_scores": _to_plain(
                        getattr(result, "category_scores", None)
                    ),
                }
    return {"flagged": False, "chunks_scanned": len(chunks)}


async def _run_moderation(client: Any, text: str, to_thread: ToThread) -> Optional[dict]:
    """Run moderation off the event loop; annotate infra failures as ``__error__``."""
    try:
        timeout_seconds = guardrail_timeout_seconds()
        return await await_with_deadline(
            await_cancellation_safe_blocking_call(
                to_thread,
                _moderate_text_sync,
                client,
                text,
                timeout_seconds,
            ),
            operation="Moderation guardrail",
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        error_type = safe_exception_category(exc)
        logger.warning("Moderation guardrail infra error (%s)", error_type)
        return {
            "__error__": safe_exception_message(
                exc,
                operation="Moderation guardrail",
            ),
            "error_type": error_type,
        }


def _excerpt(value: Any, limit: int) -> str:
    """Retain both ends of untrusted text so suffix injections are not hidden."""
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    half = max(1, (limit - 15) // 2)
    return f"{text[:half]}\n...[trimmed]...\n{text[-half:]}"


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _bounded_collection(
    value: Any,
    *,
    limit: int,
    limit_name: str,
) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise _ContentBoundsError("invalid_question_structure")
    if len(value) > limit:
        raise _ContentBoundsError(limit_name)
    return list(value)


def _validate_content_cardinality(questions: Any) -> list[Any]:
    """Reject pathological shapes instead of silently omitting nested content."""
    bounded_questions = _bounded_collection(
        questions,
        limit=_MAX_CONTENT_QUESTIONS,
        limit_name="question_count",
    )
    for question in bounded_questions:
        _bounded_collection(
            _field(question, "options", []),
            limit=_MAX_OPTIONS_PER_QUESTION,
            limit_name="options_per_question",
        )
        parts = _bounded_collection(
            _field(question, "parts", []),
            limit=_MAX_PARTS_PER_QUESTION,
            limit_name="parts_per_question",
        )
        for part in parts:
            _bounded_collection(
                _field(part, "options", []),
                limit=_MAX_OPTIONS_PER_PART,
                limit_name="options_per_part",
            )
    return bounded_questions


def _collect_text_records(
    value: Any,
    *,
    path: str,
    records: list[tuple[str, str]],
    budget: dict[str, int],
    active_ids: set[int],
    depth: int = 0,
) -> None:
    """Collect every string value with explicit depth/cardinality/byte limits."""
    if depth > 12:
        raise _ContentBoundsError("question_nesting_depth")
    budget["nodes"] += 1
    if budget["nodes"] > _MAX_CONTENT_NODES:
        raise _ContentBoundsError("question_node_count")
    if value is None:
        return
    if isinstance(value, str):
        try:
            encoded_bytes = len(value.encode("utf-8"))
        except UnicodeError as exc:
            raise _ContentBoundsError("invalid_question_text") from exc
        budget["strings"] += 1
        budget["bytes"] += encoded_bytes
        if budget["strings"] > _MAX_CONTENT_STRING_FIELDS:
            raise _ContentBoundsError("question_string_count")
        if budget["bytes"] > _MAX_CONTENT_TEXT_BYTES:
            raise _ContentBoundsError("question_text_bytes")
        if value.strip():
            records.append((path, value))
        return

    if hasattr(value, "model_dump"):
        try:
            value = value.model_dump(mode="python")
        except Exception as exc:
            raise _ContentBoundsError("invalid_question_structure") from exc
    elif not isinstance(value, (dict, list, tuple)) and hasattr(value, "__dict__"):
        try:
            value = vars(value)
        except TypeError as exc:
            raise _ContentBoundsError("invalid_question_structure") from exc

    if not isinstance(value, (dict, list, tuple)):
        return
    value_id = id(value)
    if value_id in active_ids:
        raise _ContentBoundsError("cyclic_question_structure")
    if len(value) > _MAX_CONTENT_COLLECTION_ITEMS:
        raise _ContentBoundsError("question_collection_items")

    active_ids.add(value_id)
    try:
        if isinstance(value, dict):
            for index, (key, nested) in enumerate(value.items()):
                if not isinstance(key, str) or len(key) > 64:
                    raise _ContentBoundsError("invalid_question_field")
                segment = key if key.replace("_", "").isalnum() else f"field[{index}]"
                _collect_text_records(
                    nested,
                    path=f"{path}.{segment}",
                    records=records,
                    budget=budget,
                    active_ids=active_ids,
                    depth=depth + 1,
                )
        else:
            for index, nested in enumerate(value):
                _collect_text_records(
                    nested,
                    path=f"{path}[{index}]",
                    records=records,
                    budget=budget,
                    active_ids=active_ids,
                    depth=depth + 1,
                )
    finally:
        active_ids.remove(value_id)


def _content_text_records(
    extracter_output: Any,
) -> tuple[list[Any], list[tuple[str, str]]]:
    """Return bounded questions and every string in the complete extraction."""
    bounded_questions = _validate_content_cardinality(
        _field(extracter_output, "questions", None) or []
    )
    records: list[tuple[str, str]] = []
    budget = {"nodes": 0, "strings": 0, "bytes": 0}
    _collect_text_records(
        extracter_output,
        path="extracter_output",
        records=records,
        budget=budget,
        active_ids=set(),
    )
    # Separators are sent to moderation too, so account for them in the same
    # hard budget rather than allowing field-count amplification.
    if budget["bytes"] + max(0, len(records) - 1) > _MAX_CONTENT_TEXT_BYTES:
        raise _ContentBoundsError("question_text_bytes")
    return bounded_questions, records


def _utf8_fragments(text: str) -> list[str]:
    """Split text without dropping characters; overlap guards boundary phrases."""
    fragments: list[str] = []
    start = 0
    while start < len(text):
        end = start
        used_bytes = 0
        while end < len(text):
            char_bytes = len(text[end].encode("utf-8"))
            if used_bytes + char_bytes > _CONTENT_CLASSIFIER_TEXT_FRAGMENT_BYTES:
                break
            used_bytes += char_bytes
            end += 1
        if end == start:
            raise _ContentBoundsError("classifier_text_fragment")
        fragments.append(text[start:end])
        if end >= len(text):
            break

        overlap_start = end
        overlap_bytes = 0
        while overlap_start > start:
            candidate_bytes = len(text[overlap_start - 1].encode("utf-8"))
            if overlap_bytes + candidate_bytes > _CONTENT_CLASSIFIER_FRAGMENT_OVERLAP_BYTES:
                break
            overlap_start -= 1
            overlap_bytes += candidate_bytes
        # A one-character fragment cannot overlap without preventing progress.
        start = overlap_start if overlap_start > start else end
    return fragments


def _injection_scan_items(records: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Represent every string byte in bounded, overlapping classifier fragments."""
    items: list[dict[str, Any]] = []
    for path, text in records:
        fragments = _utf8_fragments(text)
        for index, fragment in enumerate(fragments, start=1):
            items.append(
                {
                    "field": path,
                    "fragment": index,
                    "fragments": len(fragments),
                    "text": fragment,
                }
            )
    return items


def _academic_question_payload(question: Any) -> dict[str, Any]:
    """Compact context for topic classification; injection coverage is separate."""
    options = _bounded_collection(
        _field(question, "options", []),
        limit=_MAX_OPTIONS_PER_QUESTION,
        limit_name="options_per_question",
    )
    parts = _bounded_collection(
        _field(question, "parts", []),
        limit=_MAX_PARTS_PER_QUESTION,
        limit_name="parts_per_question",
    )
    return {
        "source_id": _excerpt(_field(question, "source_id", ""), 80),
        "source_raw": _excerpt(_field(question, "source_raw", ""), 1_600),
        "stem": _excerpt(_field(question, "stem", ""), 1_600),
        "question_type": _excerpt(_field(question, "question_type", ""), 80),
        "answer": _excerpt(_field(question, "answer", ""), 240),
        "group_context": _excerpt(_field(question, "group_context", ""), 600),
        "option_count": len(options),
        "option_text": [
            _excerpt(_field(option, "text", ""), 160) for option in options
        ],
        "part_count": len(parts),
        "parts": [
            {
                "stem": _excerpt(_field(part, "stem", ""), 300),
                "question_type": _excerpt(
                    _field(part, "question_type", ""),
                    80,
                ),
                "option_count": len(
                    _bounded_collection(
                        _field(part, "options", []),
                        limit=_MAX_OPTIONS_PER_PART,
                        limit_name="options_per_part",
                    )
                ),
            }
            for part in parts
        ],
    }


def _serialize_classifier_payload(
    *,
    scan_mode: str,
    item_key: str,
    items: list[dict[str, Any]],
    batch: int,
    total_batches: int,
) -> str:
    return json.dumps(
        {
            "scan_mode": scan_mode,
            "batch": batch,
            "total_batches": total_batches,
            item_key: items,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _pack_classifier_payloads(
    items: list[dict[str, Any]],
    *,
    scan_mode: str,
    item_key: str,
    max_items: Optional[int] = None,
) -> list[str]:
    """Pack requests by serialized UTF-8 bytes, never by lossy text truncation."""
    if not items:
        return []
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for item in items:
        candidate = [*current, item]
        provisional = _serialize_classifier_payload(
            scan_mode=scan_mode,
            item_key=item_key,
            items=candidate,
            batch=999_999,
            total_batches=999_999,
        )
        within_count = max_items is None or len(candidate) <= max_items
        if (
            within_count
            and len(provisional.encode("utf-8"))
            <= _CONTENT_CLASSIFIER_MAX_REQUEST_BYTES
        ):
            current = candidate
            continue
        if not current:
            raise _ContentBoundsError("classifier_request_bytes")
        groups.append(current)
        current = [item]
        singleton = _serialize_classifier_payload(
            scan_mode=scan_mode,
            item_key=item_key,
            items=current,
            batch=999_999,
            total_batches=999_999,
        )
        if len(singleton.encode("utf-8")) > _CONTENT_CLASSIFIER_MAX_REQUEST_BYTES:
            raise _ContentBoundsError("classifier_request_bytes")
    if current:
        groups.append(current)

    payloads = [
        _serialize_classifier_payload(
            scan_mode=scan_mode,
            item_key=item_key,
            items=group,
            batch=index,
            total_batches=len(groups),
        )
        for index, group in enumerate(groups, start=1)
    ]
    if any(
        len(payload.encode("utf-8")) > _CONTENT_CLASSIFIER_MAX_REQUEST_BYTES
        for payload in payloads
    ):
        raise _ContentBoundsError("classifier_request_bytes")
    return payloads


def _input_text_records(value: Any) -> list[tuple[str, str]]:
    """Collect every uploaded agent-input string or fail on unsafe structure."""
    records: list[tuple[str, str]] = []
    _collect_text_records(
        value,
        path="uploaded_agent_input",
        records=records,
        budget={"nodes": 0, "strings": 0, "bytes": 0},
        active_ids=set(),
    )
    return records


async def check_input_guardrails(
    *,
    client: Any,
    prompt: str,
    file_names: list[str],
    untrusted_payload: Any = None,
    to_thread: ToThread,
    emit_log: Optional[EmitLog] = None,
    run_config: RunConfig | None = None,
) -> GuardrailDecision:
    """Screen the raw user instruction before the container is created.

    G1 moderation + G2 jailbreak/prompt-injection classifier. Blocks on detection;
    infra errors fail open unless ``guardrail_fail_closed()``.
    """
    # Validate every local workflow instruction/template before the first paid
    # moderation or model request. Configuration errors propagate as workflow
    # errors rather than degrading to blank instructions.
    validate_workflow_assets()

    try:
        prompt_text = validate_instruction_text(prompt)
    except ValueError:
        return GuardrailDecision(
            allowed=False,
            category="off_goal",
            stage="input",
            reason="The instruction is too long to screen safely.",
        )

    try:
        payload_records = _input_text_records(untrusted_payload)
    except _ContentBoundsError as exc:
        return GuardrailDecision(
            allowed=False,
            category="off_goal",
            stage="input",
            reason="The uploaded agent input is too large or complex to screen safely.",
            detail={"limit": exc.limit},
        )
    payload_text = "\n".join(text.strip() for _path, text in payload_records)
    if len(payload_text) > _MAX_UNTRUSTED_PAYLOAD_CHARS:
        return GuardrailDecision(
            allowed=False,
            category="off_goal",
            stage="input",
            reason="The uploaded agent input is too large to screen safely.",
        )

    input_records = []
    if prompt_text:
        input_records.append(("user_instruction", prompt_text))
    input_records.extend(
        (f"uploaded_files[{index}]", str(file_name))
        for index, file_name in enumerate(file_names or [])
        if str(file_name).strip()
    )
    input_records.extend(payload_records)
    try:
        classifier_payloads = _pack_classifier_payloads(
            _injection_scan_items(input_records),
            scan_mode="input_prompt_injection_full_coverage",
            item_key="untrusted_input_fields",
        )
        classifier_request_limit = guardrail_max_classifier_requests()
        if len(classifier_payloads) > classifier_request_limit:
            raise _ContentBoundsError("input_classifier_request_count")
    except _ContentBoundsError as exc:
        return GuardrailDecision(
            allowed=False,
            category="off_goal",
            stage="input",
            reason="The request is too large or complex to screen safely.",
            detail={"limit": exc.limit},
        )

    # These bounds are also cost/amplification controls, so they remain active
    # when semantic/model guardrails are intentionally disabled.
    if not guardrails_enabled():
        return GuardrailDecision(allowed=True, stage="input")

    input_agent = None
    classifier_stage_timeout = 0.0
    classifier_request_timeout = 0.0
    if classifier_payloads:
        # Agent construction is local and may validate model configuration.
        # Keep it ahead of moderation so configuration failures incur no spend.
        classifier_stage_timeout = guardrail_stage_timeout_seconds()
        classifier_request_timeout = guardrail_timeout_seconds()
        input_agent = _build_guardrail_agent(
            "Input Guardrail",
            _INPUT_GUARDRAIL_PROMPT,
            _InjectionCheck,
        )

    # G1 — moderation on the raw instruction and every uploaded structured field.
    moderation_text = "\n".join(
        value for value in (prompt_text, payload_text) if value
    )
    mod = await _run_moderation(client, moderation_text, to_thread)
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

    # G2 — classify the instruction and every structured agent-input text field
    # before a tool-enabled stage receives it.
    if classifier_payloads:
        classifier_run_config = run_config or _run_config()
        classifier_budget = _ClassifierBudget(
            wall_clock_seconds=classifier_stage_timeout,
            request_timeout_seconds=classifier_request_timeout,
            max_requests=classifier_request_limit,
        )
        check: Optional[_InjectionCheck] = None
        for chunk_index, payload in enumerate(classifier_payloads, start=1):
            try:
                result = await classifier_budget.run(
                    lambda payload=payload: Runner.run(
                        input_agent,
                        input=payload,
                        max_turns=2,
                        run_config=classifier_run_config,
                    ),
                    operation="Input guardrail classifier",
                )
                check = result.final_output
            except _ClassifierBudgetExceeded as exc:
                return GuardrailDecision(
                    allowed=False,
                    category="jailbreak",
                    stage="input",
                    reason=(
                        "Input safety check exceeded its configured classifier "
                        "budget; blocking as a precaution."
                    ),
                    detail={"limit": exc.limit, "chunk": chunk_index},
                )
            except ModelRefusalError as exc:
                # A refusal while classifying the input is itself a strong signal.
                return GuardrailDecision(
                    allowed=False,
                    category="jailbreak",
                    stage="input",
                    reason="Your request could not be safely processed.",
                    detail={
                        "error": safe_exception_message(
                            exc,
                            operation="Input guardrail classifier",
                        ),
                        "error_type": safe_exception_category(exc),
                    },
                )
            except Exception as exc:
                error_type = safe_exception_category(exc)
                logger.warning(
                    "Input guardrail classifier error (%s)",
                    error_type,
                )
                if guardrail_fail_closed():
                    return GuardrailDecision(
                        allowed=False,
                        category="jailbreak",
                        stage="input",
                        reason="Input safety check unavailable; blocking as a precaution.",
                        detail={
                            "error": safe_exception_message(
                                exc,
                                operation="Input guardrail classifier",
                            ),
                            "error_type": error_type,
                            "chunk": chunk_index,
                        },
                    )
                continue
            if check is not None and check.is_blocked:
                category = (
                    check.category
                    if check.category in _VALID_INJECTION_CATEGORIES
                    else "jailbreak"
                )
                return GuardrailDecision(
                    allowed=False,
                    category=category,
                    stage="input",
                    detail={**check.model_dump(), "chunk": chunk_index},
                    reason=check.reason
                    or "Your request was blocked because it tries to misuse the tool.",
                )

    return GuardrailDecision(allowed=True, stage="input")


async def _run_content_classifier(
    *,
    agent: Agent,
    budget: _ClassifierBudget,
    payload: str,
    scan: str,
    batch: int,
    run_config: RunConfig,
) -> tuple[Optional[_AcademicCheck], Optional[GuardrailDecision]]:
    """Run one bounded content request with uniform fail-closed handling."""
    try:
        result = await budget.run(
            lambda: Runner.run(
                agent,
                input=payload,
                max_turns=2,
                run_config=run_config,
            ),
            operation="Content guardrail classifier",
        )
        return result.final_output, None
    except _ClassifierBudgetExceeded as exc:
        return None, GuardrailDecision(
            allowed=False,
            category="not_a_question",
            stage="content",
            reason=(
                "Content safety check exceeded its configured classifier "
                "budget; blocking as a precaution."
            ),
            detail={"limit": exc.limit, "scan": scan, "batch": batch},
        )
    except ModelRefusalError as exc:
        return None, GuardrailDecision(
            allowed=False,
            category="not_a_question",
            stage="content",
            reason="The uploaded material could not be safely processed.",
            detail={
                "error": safe_exception_message(
                    exc,
                    operation="Content guardrail classifier",
                ),
                "error_type": safe_exception_category(exc),
                "scan": scan,
                "batch": batch,
            },
        )
    except Exception as exc:
        error_type = safe_exception_category(exc)
        logger.warning(
            "Content guardrail classifier error (%s)",
            error_type,
        )
        if guardrail_fail_closed():
            return None, GuardrailDecision(
                allowed=False,
                category="not_a_question",
                stage="content",
                reason="Content safety check unavailable; blocking as a precaution.",
                detail={
                    "error": safe_exception_message(
                        exc,
                        operation="Content guardrail classifier",
                    ),
                    "error_type": error_type,
                    "scan": scan,
                    "batch": batch,
                },
            )
        return None, None


async def check_content_guardrails(
    *,
    client: Any,
    extracter_output: Any,
    normalizer_output: Any,
    to_thread: ToThread,
    emit_log: Optional[EmitLog] = None,
    run_config: RunConfig | None = None,
) -> GuardrailDecision:
    """Confirm the extracted material is genuine academic (physics) question content.

    G1 moderation on extracted text + G3 academic/physics classifier. Blocks on
    detection; infra errors fail open unless ``guardrail_fail_closed()``.
    """
    try:
        questions, text_records = _content_text_records(
            extracter_output
        )
    except _ContentBoundsError as exc:
        return GuardrailDecision(
            allowed=False,
            category="not_a_question",
            stage="content",
            reason="The extracted material is too large or complex to screen safely.",
            detail={"limit": exc.limit},
        )

    # Structural/byte bounds are also cost-amplification controls, so they stay
    # active when semantic/model guardrails are intentionally disabled.
    if not guardrails_enabled():
        return GuardrailDecision(allowed=True, stage="content")

    if not questions:
        return GuardrailDecision(
            allowed=False, category="not_a_question", stage="content",
            reason="No academic questions could be found in the uploaded material.",
        )

    # Preflight the complete classifier fan-out and local agent configuration
    # before moderation spends anything.
    try:
        injection_payloads = _pack_classifier_payloads(
            _injection_scan_items(text_records),
            scan_mode="prompt_injection_full_coverage",
            item_key="untrusted_extracter_fields",
        )
        academic_payloads = _pack_classifier_payloads(
            [_academic_question_payload(question) for question in questions],
            scan_mode="academic_document",
            item_key="extracted_questions",
            max_items=guardrail_max_questions_sampled(),
        )
        classifier_request_limit = guardrail_max_classifier_requests()
        if (
            len(injection_payloads) + len(academic_payloads)
            > classifier_request_limit
        ):
            raise _ContentBoundsError("classifier_request_count")
    except _ContentBoundsError as exc:
        return GuardrailDecision(
            allowed=False,
            category="not_a_question",
            stage="content",
            reason="The extracted material is too large or complex to screen safely.",
            detail={"limit": exc.limit},
        )

    classifier_stage_timeout = guardrail_stage_timeout_seconds()
    classifier_request_timeout = guardrail_timeout_seconds()
    content_agent = _build_guardrail_agent(
        "Content Guardrail",
        _CONTENT_GUARDRAIL_PROMPT,
        _AcademicCheck,
    )

    # G1 — moderation on extracted source text.
    combined = "\n".join(text for _path, text in text_records)
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

    classifier_budget = _ClassifierBudget(
        wall_clock_seconds=classifier_stage_timeout,
        request_timeout_seconds=classifier_request_timeout,
        max_requests=classifier_request_limit,
    )
    classifier_run_config = run_config or _run_config()

    # G3a — classify every ExtracterOutput string for prompt injection. This
    # includes document metadata and is separate from the compact, question-only
    # academic summary so every nested and long-field fragment is inspected.
    for batch_index, payload in enumerate(injection_payloads, start=1):
        check, failure = await _run_content_classifier(
            agent=content_agent,
            budget=classifier_budget,
            payload=payload,
            scan="prompt_injection_full_coverage",
            batch=batch_index,
            run_config=classifier_run_config,
        )
        if failure is not None:
            return failure
        if check is None:
            continue

        if check.has_prompt_injection:
            return GuardrailDecision(
                allowed=False,
                category="prompt_injection",
                stage="content",
                detail={
                    **check.model_dump(),
                    "scan": "prompt_injection_full_coverage",
                    "batch": batch_index,
                },
                reason=check.injection_reason
                or "The uploaded material contains instructions aimed at the AI.",
            )

    # G3b — classify bounded question summaries for academic/physics relevance.
    for batch_index, payload in enumerate(academic_payloads, start=1):
        check, failure = await _run_content_classifier(
            agent=content_agent,
            budget=classifier_budget,
            payload=payload,
            scan="academic_document",
            batch=batch_index,
            run_config=classifier_run_config,
        )
        if failure is not None:
            return failure
        if check is None:
            continue

        if check.has_prompt_injection:
            return GuardrailDecision(
                allowed=False,
                category="prompt_injection",
                stage="content",
                detail={
                    **check.model_dump(),
                    "scan": "academic_document",
                    "batch": batch_index,
                },
                reason=check.injection_reason
                or "The uploaded material contains instructions aimed at the AI.",
            )
        if not check.is_academic_questions:
            return GuardrailDecision(
                allowed=False,
                category="not_a_question",
                stage="content",
                detail=check.model_dump(),
                reason=check.reason
                or "The uploaded material is not a set of academic questions.",
            )
        if (
            guardrail_block_off_topic()
            and not check.physics_oriented
            and check.confidence >= guardrail_physics_min_confidence()
        ):
            return GuardrailDecision(
                allowed=False,
                category="off_topic",
                stage="content",
                detail=check.model_dump(),
                reason=check.reason
                or "The uploaded questions are not physics-oriented.",
            )

    return GuardrailDecision(allowed=True, stage="content")
