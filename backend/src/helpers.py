"""Shared backend helpers for loading agent data and reconciling workflow output."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from openai import OpenAI

from .schemas import ExtracterOutput, NewQuestionItem, NormalizerOutput, SourceQuestionItem

# 1-3 lowercase snake_case words as produced by the LLM theme labeler.
_THEME_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z][a-z0-9]*){0,2}$")


def _is_valid_theme(value: str) -> bool:
    return bool(value and _THEME_RE.match(value))


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


def resolve_prompt_path(prompts_dir: Path, name: str) -> Path | None:
    """Return the prompt file path for a prompt name, or None if missing."""
    candidate = prompts_dir / f"{name}.txt"
    return candidate if candidate.exists() else None


def load_prompt(prompts_dir: Path, logger: Any, name: str) -> str:
    """Load a named prompt template from disk.

    Returns the prompt text, or an empty string when no usable file is found.
    """
    prompt_path = resolve_prompt_path(prompts_dir, name)
    if prompt_path is None:
        logger.warning("Prompt file not found for %s in %s", name, prompts_dir)
        return ""
    try:
        return prompt_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to load prompt %s from %s: %s", name, prompt_path, exc)
        return ""


def render_prompt(prompts_dir: Path, logger: Any, name: str, **variables: Any) -> str:
    """Load a prompt template and substitute its `{key}` placeholders.

    Plain string replacement (not str.format) so braces in prompt prose,
    shell snippets, or LaTeX never need escaping. Unknown placeholders are
    left as-is and logged so a typo in a template is visible.
    """
    template = load_prompt(prompts_dir, logger, name)
    rendered = template
    for key, value in variables.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    leftover = re.findall(r"{[a-z_][a-z0-9_]*}", rendered)
    if leftover:
        logger.warning("Prompt %s has unsubstituted placeholders: %s", name, sorted(set(leftover)))
    return rendered.strip()


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
    """Return the most common LLM-assigned theme across the extracted questions."""
    ordered_themes = [
        q.theme.strip()
        for q in questions
        if q.theme and _is_valid_theme(q.theme.strip())
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
    if not _is_valid_theme(extracted_theme):
        extracted_theme = dominant_question_theme(extraction.questions)
    if not config.theme and extracted_theme:
        config.theme = extracted_theme

    apply_requested_difficulty(extraction.questions, config.difficulty)


def normalize_question_types(questions: list[SourceQuestionItem]) -> None:
    """Set MC question_type on items that have options but no type, in place.

    Verifier output sometimes leaves question_type blank on items that carry
    options; downstream code (validators, latexifier, evaluator) expects the
    type to be set. This function does only that lightweight inference — it
    does NOT fabricate placeholder content for omitted questions, so omissions
    surface as real validation failures the repair pass can act on.
    """
    for q in questions:
        if q.options:
            q.question_type = "multiple_choice"
        for part in q.parts:
            if part.options and not part.question_type:
                part.question_type = "multiple_choice"


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
