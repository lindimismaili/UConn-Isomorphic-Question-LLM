from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from openai import OpenAI

try:
    from .schemas import NewQuestionItem, SourceQuestionItem
except ImportError:
    from schemas import NewQuestionItem, SourceQuestionItem


def load_agents_config(config_path: Path, logger: Any) -> dict[str, Any]:
    """Load agent configuration from agents.json."""

    if not config_path.exists():
        logger.warning("Agents config not found: %s", config_path)
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load agents config: %s", exc)
        return {}


def load_prompt(prompts_dir: Path, logger: Any, name: str) -> str:
    """Load a prompt from the prompts directory."""

    prompt_candidates = [prompts_dir / f"{name}.txt"]
    if name == "latex":
        prompt_candidates.insert(0, prompts_dir / "latex_1.txt")

    for prompt_path in prompt_candidates:
        if not prompt_path.exists():
            continue
        try:
            return prompt_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to load prompt %s from %s: %s", name, prompt_path, exc)
            return ""

    logger.warning("Prompt file not found for %s. Checked: %s", name, prompt_candidates)
    return ""


def upload_file_to_openai(client: OpenAI, file_path: Path, logger: Any) -> str:
    """Upload a file to OpenAI and return the file ID."""

    with open(file_path, "rb") as f:
        response = client.files.create(file=f, purpose="assistants")
    logger.info("Uploaded file %s -> %s", file_path.name, response.id)
    return response.id


def reconcile_verified_questions(
    extracted_questions: list[SourceQuestionItem],
    verified_questions: list[SourceQuestionItem],
) -> list[SourceQuestionItem]:
    """Ensure no extracted source question is dropped by verifier output."""

    extracted_by_id: dict[str, SourceQuestionItem] = {}
    for q in extracted_questions:
        if q.options:
            q.question_type = "multiple_choice"
        extracted_by_id[q.source_id] = q

    verified_by_id = {q.source_id: q for q in verified_questions}
    reconciled: list[SourceQuestionItem] = []
    for q in extracted_questions:
        verified_q = verified_by_id.get(q.source_id)
        if verified_q is None:
            fallback = extracted_by_id[q.source_id].model_copy(deep=True)
            if fallback.question_type == "multiple_choice":
                for opt in fallback.options:
                    if not opt.explanation:
                        opt.explanation = "Not verified; preserved to prevent question loss."
                fallback.has_explicit_answer = bool(
                    any(opt.is_correct for opt in fallback.options)
                )
            for part in fallback.parts:
                if part.options and not part.question_type:
                    part.question_type = "multiple_choice"
            reconciled.append(fallback)
            continue
        if verified_q.options:
            verified_q.question_type = "multiple_choice"
        for part in verified_q.parts:
            if part.options and not part.question_type:
                part.question_type = "multiple_choice"
        reconciled.append(verified_q)
    return reconciled


def missing_generation_source_ids(
    source_questions: list[SourceQuestionItem],
    generated_questions: list[NewQuestionItem],
    per_source: int,
) -> list[str]:
    generated_count_by_source: dict[str, int] = {}
    for q in generated_questions:
        generated_count_by_source[q.source_id] = generated_count_by_source.get(q.source_id, 0) + 1
    missing: list[str] = []
    for src in source_questions:
        current = generated_count_by_source.get(src.source_id, 0)
        expected = 1 if src.has_figure else per_source
        if current < expected:
            missing.append(src.source_id)
    return missing


def build_json_message(
    payload: dict[str, Any],
    file_refs: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """
    Build a single user message where the textual part is strict JSON.

    This keeps cross-agent handoff data structured and machine-readable.
    """

    content: list[dict[str, Any]] = []
    if file_refs:
        content.extend(file_refs)
    content.append(
        {
            "type": "input_text",
            "text": json.dumps(payload, indent=2, ensure_ascii=False),
        }
    )
    return [{"role": "user", "content": content}]
