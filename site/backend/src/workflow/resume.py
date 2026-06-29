"""Workflow resume/replay helpers: agent ordering, output reuse, input coercion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from agents import Agent

from ..artifacts import WORKFLOW_AGENT_SEQUENCE, load_agent_output_payload
from ..helpers import build_json_message
from ..schemas import (
    AGENT_OUTPUT_TYPES,
    ExtracterOutput,
    GeneratorOutput,
    LaTeXOutput,
    NormalizerOutput,
    VerifierOutput,
)


def agent_sequence_index(agent_key: str) -> int:
    """Return the workflow position for an agent key."""
    if agent_key not in WORKFLOW_AGENT_SEQUENCE:
        raise ValueError(f"Unknown agent key: {agent_key}")
    return WORKFLOW_AGENT_SEQUENCE.index(agent_key)


def should_run_agent(candidate_key: str, start_agent: str) -> bool:
    """Return True when the candidate is at or after the requested start agent."""
    return agent_sequence_index(candidate_key) >= agent_sequence_index(start_agent)


def validate_resume_agent_output(agent_key: str, payload: Any) -> Any:
    """Validate a cached agent payload against the agent's output schema."""
    if payload is None:
        raise ValueError(f"Missing cached output for {agent_key}")
    output_type = AGENT_OUTPUT_TYPES.get(agent_key)
    if output_type is None:
        return payload
    return output_type.model_validate(payload)


def load_resume_agent_output(resume_source_dir: Path, agent_key: str) -> Any:
    """Load and validate cached output for a resumed workflow step."""
    payload = load_agent_output_payload(resume_source_dir, agent_key)
    return validate_resume_agent_output(agent_key, payload)


def agent_reasoning_level(agent: Agent) -> Optional[str]:
    """Return the configured reasoning effort for an agent, if any."""
    if agent.model_settings and agent.model_settings.reasoning:
        return agent.model_settings.reasoning.effort
    return None


def _message_input_texts(value: Any) -> list[str]:
    """Extract input_text strings from an Agents SDK message payload."""
    texts: list[str] = []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "input_text":
                    raw_text = item.get("text")
                    if isinstance(raw_text, str):
                        texts.append(raw_text)
        return texts
    if isinstance(value, list):
        for item in value:
            texts.extend(_message_input_texts(item))
    return texts


def extract_agent_input_payload(value: Any) -> dict[str, Any]:
    """Extract the structured JSON payload embedded in an uploaded agent input."""
    if isinstance(value, dict):
        if "input" in value and isinstance(value["input"], (dict, list, str)):
            return extract_agent_input_payload(value["input"])
        structured_keys = (
            "task", "configuration", "normalizer_config", "user_input", "defaults",
            "rules", "source_questions", "verified_questions", "generated_questions",
            "isomorphic_questions", "questions", "document", "requirements",
            "final_result", "result", "rubric", "evaluation_items", "latex_output",
            "latex_content", "latex_file", "pdf_file",
        )
        if any(key in value for key in structured_keys):
            return value

    for text in _message_input_texts(value):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def runner_input_from_upload(value: Any) -> Any:
    """Normalize uploaded JSON objects to the message format expected by agents."""
    if isinstance(value, dict):
        return build_json_message(value)
    return value


def coerce_normalizer_from_agent_input(payload: dict[str, Any]) -> NormalizerOutput:
    """Build NormalizerOutput context from a downstream agent input payload."""
    result = payload.get("final_result") or payload.get("result") or {}
    if isinstance(result, dict):
        config = result.get("config")
        if isinstance(config, dict):
            return NormalizerOutput.model_validate(config)

    for key in ("normalizer_config", "configuration", "config"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            return NormalizerOutput.model_validate(candidate)

    document = payload.get("document")
    if isinstance(document, dict):
        return NormalizerOutput(
            title=str(document.get("title") or ""),
            theme=str(document.get("theme") or ""),
        )

    return NormalizerOutput()


def _extract_question_container(candidate: Any) -> dict[str, Any]:
    """Normalize a question list or question container into an Extracter-like dict."""
    if isinstance(candidate, dict):
        if isinstance(candidate.get("questions"), list):
            return candidate
        if isinstance(candidate.get("source_questions"), list):
            return {"questions": candidate.get("source_questions", [])}
    if isinstance(candidate, list):
        return {"questions": candidate}
    return {"questions": []}


def coerce_extracter_from_agent_input(payload: dict[str, Any]) -> ExtracterOutput:
    """Build ExtracterOutput context from an uploaded downstream input."""
    result = payload.get("final_result") or payload.get("result") or {}
    candidate = result.get("source_questions") if isinstance(result, dict) else None
    if candidate is None:
        candidate = payload.get("source_questions")
    if candidate is None:
        candidate = payload
    data = _extract_question_container(candidate)
    if "filename" not in data:
        data = {"filename": "", **data}
    return ExtracterOutput.model_validate(data)


def coerce_verifier_from_agent_input(
    payload: dict[str, Any],
    extracter_output: ExtracterOutput,
) -> VerifierOutput:
    """Build VerifierOutput context from an uploaded generator or latex input."""
    result = payload.get("final_result") or payload.get("result") or {}
    candidate = result.get("verified_questions") if isinstance(result, dict) else None
    if candidate is None:
        candidate = payload.get("verified_questions")
    if candidate is None:
        candidate = payload.get("source_questions")
    if candidate is not None:
        data = _extract_question_container(candidate)
        return VerifierOutput.model_validate({"questions": data.get("questions", [])})
    return VerifierOutput(questions=extracter_output.questions)


def coerce_generator_from_agent_input(payload: dict[str, Any]) -> GeneratorOutput:
    """Build GeneratorOutput context from uploaded downstream agent input."""
    result = payload.get("final_result") or payload.get("result") or {}
    raw_questions = result.get("generated_questions") if isinstance(result, dict) else None
    if raw_questions is None:
        raw_questions = payload.get("generated_questions")
    if raw_questions is None:
        raw_questions = payload.get("isomorphic_questions")
    if raw_questions is not None:
        return GeneratorOutput.model_validate({"isomorphic_questions": raw_questions})

    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list):
        raw_questions = []

    items: list[dict[str, Any]] = []
    for index, raw_question in enumerate(raw_questions, start=1):
        if not isinstance(raw_question, dict):
            continue
        item = dict(raw_question)
        item.setdefault("id", index)
        item.setdefault("source_id", str(item.get("source_id") or index))
        item.setdefault("stem", "")
        item.setdefault("options", [])
        item.setdefault("difficulty", "")
        item.setdefault("tags", [])
        item.setdefault("generation_notes", "Provided via uploaded Latexifier input.")
        item.setdefault("question_type", "multiple_choice" if item.get("options") else "open_ended")
        item.setdefault("skipped", False)
        item.setdefault("skip_reason", "")
        item.setdefault("answer", "")
        item.setdefault("parts", [])
        items.append(item)

    return GeneratorOutput.model_validate({"isomorphic_questions": items})


def coerce_latex_from_agent_input(payload: dict[str, Any]) -> LaTeXOutput:
    """Build LaTeXOutput context from uploaded evaluation-stage input."""
    for key in ("latex_output", "latex"):
        candidate = payload.get(key)
        if isinstance(candidate, dict) and "latex_content" in candidate:
            return LaTeXOutput.model_validate(candidate)

    result = payload.get("final_result") or payload.get("result") or {}
    latex_content = payload.get("latex_content")
    filename = payload.get("filename") or payload.get("latex_file")
    pdf_filename = payload.get("pdf_filename") or payload.get("pdf_file")
    if isinstance(result, dict):
        latex_content = latex_content or result.get("latex_content")
        filename = filename or result.get("latex_file")
        pdf_filename = pdf_filename or result.get("pdf_file")

    if not isinstance(latex_content, str) or not latex_content.strip():
        raise ValueError(
            "Evaluation input must include latex_content or latex_output.latex_content. "
            "Use a current evaluation agent input artifact, or upload a LaTeX output "
            "artifact and start from the Latexifier."
        )

    return LaTeXOutput(
        latex_content=latex_content,
        filename=str(filename or "isomorphic_questions.tex"),
        pdf_filename=str(pdf_filename or ""),
    )


def agent_input_has_generated_questions(payload: dict[str, Any]) -> bool:
    """Return True when uploaded evaluation context includes generated questions."""
    result = payload.get("final_result") or payload.get("result") or {}
    raw_questions = result.get("generated_questions") if isinstance(result, dict) else None
    if raw_questions is None:
        raw_questions = payload.get("generated_questions")
    if raw_questions is None:
        raw_questions = payload.get("isomorphic_questions")
    return isinstance(raw_questions, list) and len(raw_questions) > 0
