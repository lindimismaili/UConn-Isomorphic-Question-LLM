"""Shared backend helpers for loading agent data and reconciling workflow output."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from openai import OpenAI

try:
    from .schemas import ExtracterOutput, NewQuestionItem, NormalizerOutput, SourceQuestionItem
except ImportError:
    from schemas import ExtracterOutput, NewQuestionItem, NormalizerOutput, SourceQuestionItem


VALID_THEMES = {
    "life_science",
    "engineering",
    "everyday_activity",
    "physics_abstract",
}


def load_agents_config(config_path: Path, logger: Any) -> dict[str, Any]:
    """Load the agent configuration JSON.

    Args:
        config_path: Path to the `agents.json` file.
        logger: Logger used to report missing or malformed configuration.

    Returns:
        Parsed configuration mapping, or an empty dict when the file is missing
        or cannot be decoded.
    """

    if not config_path.exists():
        logger.warning("Agents config not found: %s", config_path)
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load agents config: %s", exc)
        return {}


def load_prompt(prompts_dir: Path, logger: Any, name: str) -> str:
    """Load a named prompt template from disk.

    Args:
        prompts_dir: Directory containing prompt text files.
        logger: Logger used to report missing or unreadable prompt files.
        name: Prompt base name, without the `.txt` suffix.

    Returns:
        Prompt text, or an empty string when no usable file is found.
    """

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
    """Upload a local file for later attachment to OpenAI tool calls.

    Args:
        client: OpenAI client used to create the file record.
        file_path: Path to the file that should be uploaded.
        logger: Logger used to record the returned file ID.

    Returns:
        The OpenAI file identifier.
    """

    with open(file_path, "rb") as f:
        response = client.files.create(file=f, purpose="assistants")
    logger.info("Uploaded file %s -> %s", file_path.name, response.id)
    return response.id


def _clean_metadata_value(value: Any) -> str:
    """Normalize optional metadata to a stripped string."""
    if value is None:
        return ""
    return str(value).strip()


def dominant_question_theme(questions: list[SourceQuestionItem]) -> str:
    """Return the most common valid theme across the extracted questions."""

    ordered_themes = [
        q.theme.strip()
        for q in questions
        if q.theme and q.theme.strip() in VALID_THEMES
    ]
    if not ordered_themes:
        return ""
    counts = Counter(ordered_themes)
    return max(counts, key=lambda theme: (counts[theme], -ordered_themes.index(theme)))


def apply_requested_difficulty(
    questions: list[SourceQuestionItem] | list[NewQuestionItem],
    difficulty: str,
) -> None:
    """Apply a requested difficulty value to every question in place."""

    requested_difficulty = _clean_metadata_value(difficulty)
    for question in questions:
        question.difficulty = requested_difficulty


def merge_extracted_metadata_into_config(
    config: NormalizerOutput,
    extraction: ExtracterOutput,
) -> None:
    """Merge extractor-discovered metadata into the workflow configuration.

    The function only fills blank configuration values and keeps the user's
    requested difficulty setting authoritative.
    """

    config.difficulty = _clean_metadata_value(config.difficulty)
    config.title = _clean_metadata_value(config.title)
    config.theme = _clean_metadata_value(config.theme)

    extracted_title = _clean_metadata_value(extraction.title)
    if not config.title and extracted_title:
        config.title = extracted_title

    extracted_theme = _clean_metadata_value(extraction.theme)
    if extracted_theme not in VALID_THEMES:
        extracted_theme = dominant_question_theme(extraction.questions)
    if not config.theme and extracted_theme:
        config.theme = extracted_theme

    apply_requested_difficulty(extraction.questions, config.difficulty)


def reconcile_verified_questions(
    extracted_questions: list[SourceQuestionItem],
    verified_questions: list[SourceQuestionItem],
) -> list[SourceQuestionItem]:
    """Keep verified output aligned with the originally extracted questions."""

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
    """Return source IDs whose generated question count is below expectation."""
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

    Args:
        payload: JSON-serializable payload to embed in the text portion.
        file_refs: Optional file attachment descriptors to prepend to the message.

    Returns:
        A single user message in the OpenAI Agents message format.
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
