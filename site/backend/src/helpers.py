"""Shared backend helpers for loading agent data and reconciling workflow output."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from openai import OpenAI

from .schemas import ExtracterOutput, NewQuestionItem, NormalizerOutput, SourceQuestionItem

OPENAI_FILE_EXPIRES_MIN_SECONDS = 60 * 60
OPENAI_FILE_EXPIRES_MAX_SECONDS = 30 * 24 * 60 * 60
OPENAI_FILE_EXPIRES_DEFAULT_SECONDS = 24 * 60 * 60


def openai_file_expires_after() -> dict[str, int | str]:
    """Return the bounded provider-side expiry policy for uploaded files."""
    raw = (
        os.getenv(
            "OPENAI_FILE_EXPIRES_SECONDS",
            str(OPENAI_FILE_EXPIRES_DEFAULT_SECONDS),
        )
        or ""
    ).strip()
    try:
        seconds = int(raw)
    except ValueError as exc:
        raise ValueError(
            "OPENAI_FILE_EXPIRES_SECONDS must be an integer from "
            f"{OPENAI_FILE_EXPIRES_MIN_SECONDS} to {OPENAI_FILE_EXPIRES_MAX_SECONDS}"
        ) from exc
    if not OPENAI_FILE_EXPIRES_MIN_SECONDS <= seconds <= OPENAI_FILE_EXPIRES_MAX_SECONDS:
        raise ValueError(
            "OPENAI_FILE_EXPIRES_SECONDS must be an integer from "
            f"{OPENAI_FILE_EXPIRES_MIN_SECONDS} to {OPENAI_FILE_EXPIRES_MAX_SECONDS}"
        )
    return {"anchor": "created_at", "seconds": seconds}


def resolve_prompt_path(prompts_dir: Path, name: str) -> Path | None:
    """Return the prompt file path for a prompt name, or None if missing."""
    candidate = prompts_dir / f"{name}.txt"
    return candidate if candidate.is_file() else None


def load_prompt(prompts_dir: Path, logger: Any, name: str) -> str:
    """Load a required, non-empty prompt template from disk.

    Prompt files are executable workflow configuration. Missing, unreadable, or
    empty files therefore fail fast instead of silently creating an agent with
    blank instructions. ``logger`` remains in the signature for compatibility
    with existing callers.
    """
    _ = logger
    prompt_path = resolve_prompt_path(prompts_dir, name)
    if prompt_path is None:
        raise FileNotFoundError(f"Required prompt file not found: {prompts_dir / f'{name}.txt'}")
    try:
        prompt = prompt_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Required prompt file is unreadable: {prompt_path}") from exc
    if not prompt.strip():
        raise ValueError(f"Required prompt file is empty: {prompt_path}")
    return prompt


def _runtime_prompt_placeholders(template: str) -> set[str]:
    """Return runtime placeholders while ignoring simple LaTeX command arguments."""
    placeholders: set[str] = set()
    for match in re.finditer(r"{([a-z_][a-z0-9_]*)}", template):
        line_start = template.rfind("\n", 0, match.start()) + 1
        line_prefix = template[line_start : match.start()]
        if re.search(r"\\[A-Za-z]+(?:\{[^{}\n]*\})*$", line_prefix):
            continue
        placeholders.add(match.group(1))
    return placeholders


def render_prompt(prompts_dir: Path, logger: Any, name: str, **variables: Any) -> str:
    """Load a prompt template and substitute its `{key}` placeholders.

    Plain string replacement (not str.format) so braces in prompt prose,
    shell snippets, or LaTeX never need escaping. Placeholder discovery ignores
    simple LaTeX command arguments (for example ``\\begin{document}``). Every
    discovered runtime placeholder must have a value, and every supplied value
    must correspond to a placeholder.
    """
    template = load_prompt(prompts_dir, logger, name)
    placeholders = _runtime_prompt_placeholders(template)
    supplied = set(variables)
    missing_values = sorted(placeholders - supplied)
    missing_placeholders = sorted(supplied - placeholders)
    if missing_values:
        names = ", ".join(f"{{{key}}}" for key in missing_values)
        raise ValueError(f"Prompt {name} has unresolved required placeholder(s): {names}")
    if missing_placeholders:
        names = ", ".join(f"{{{key}}}" for key in missing_placeholders)
        raise ValueError(f"Prompt {name} is missing required placeholder(s): {names}")

    rendered = template
    for key, value in variables.items():
        rendered = rendered.replace("{" + key + "}", str(value))
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
        response = client.files.create(
            file=f,
            # This workflow uses the Responses API and explicitly owned Code
            # Interpreter containers, not the legacy Assistants API.
            purpose="user_data",
            expires_after=openai_file_expires_after(),
        )
    logger.info("Uploaded a temporary provider file")
    return response.id


def _clean_metadata_value(value: Any) -> str:
    """Normalize optional metadata to a stripped string."""
    if value is None:
        return ""
    return str(value).strip()


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
