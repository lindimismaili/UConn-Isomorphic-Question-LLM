"""
OpenAI Evals integration for generated isomorphic question outputs.

This module evaluates generated questions against rubric criteria and writes
an evaluation report to the run output directory.

Grader configuration knobs:

- Grader model source is ``agents.json`` ``evaluation.model`` (via ``rubric_model()``).
  Set the environment variable ``GRADER_MODEL_OVERRIDE`` to grade with a different
  model family than the generator — this mitigates generator/judge self-preference
  without touching ``agents.json`` / ``registry.py``. When unset, ``rubric_model()``
  is used.
- Reproducibility: every ``score_model`` grader is seeded with ``GRADER_SEED`` so
  re-runs of the same batch are comparable (surfaced in the report as ``grader_seed``).
- Reasoning effort is resolved per criterion from the rubric criterion's own
  ``reasoning`` field, falling back to the global ``rubric_reasoning_effort()``
  (surfaced as ``reasoning_effort_by_criterion``).
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from openai import OpenAI

from .artifacts import to_jsonable
from .paths import EVALUATION_FIXTURES_DIR, EVALUATOR_PROMPT_PATH, REPO_ROOT, RUBRIC_PATH
from .registry import evaluation_model as _evaluation_model
from .safety import utc_now_iso

logger = logging.getLogger(__name__)

PASSING_SCORE_THRESHOLD = 1.0
EVALUATION_OUTPUTS_DIRNAME = "evaluation_outputs"
EVALUATION_REPORT_FILENAME = "evaluation_report.json"
EVALUATION_DATASET_FILENAME = "evaluation_dataset.jsonl"
EVALUATION_ITEMS_FILENAME = "evaluation_agents.jsonl"
EVALUATION_SPREADSHEET_FILENAME = "evaluation_spreadsheet.csv"
EVALUATION_RUBRIC_FILENAME = "rubric.json"

# Fixed seed passed to every score_model grader's sampling_params so repeated
# runs of the same batch are reproducible; drift in grades then reflects a
# rubric/prompt change rather than sampling noise. Surfaced as report["grader_seed"].
GRADER_SEED = 7

# Environment variable name for a cross-family judge override. When set, its value
# is used as the grader model instead of rubric_model() (agents.json evaluation.model),
# so a different judge family can mitigate generator/judge self-preference.
GRADER_MODEL_OVERRIDE_ENV = "GRADER_MODEL_OVERRIDE"

# Fixed failure-reason taxonomy. When a grader returns 0 (fail) it is instructed to
# begin the reasoning's final conclusion with exactly one bracketed tag from this
# vocabulary (e.g. "[WRONG_ANSWER] ..."), making failures machine-mineable by a
# downstream meta-evaluator. Importable by other modules; does NOT change the
# numeric 0/1/-1 score contract.
FAILURE_TAGS: tuple[str, ...] = (
    "CONCEPT_DRIFT",
    "MISSING_CONVERSION",
    "NUMERIC_INCONSISTENT",
    "WRONG_ANSWER",
    "DISTRACTOR_ROLE_LOST",
    "GENERIC_DISTRACTOR",
    "CLERICAL",
    "UNDERSPECIFIED",
    "OTHER",
)


def _grader_model_override() -> Optional[str]:
    """Return the GRADER_MODEL_OVERRIDE env value (stripped) if set, else None."""
    value = os.environ.get(GRADER_MODEL_OVERRIDE_ENV, "")
    value = value.strip() if isinstance(value, str) else ""
    return value or None


@lru_cache(maxsize=1)
def general_grader_instructions() -> str:
    """Evaluator system prompt prefix, loaded on first call."""
    if not EVALUATOR_PROMPT_PATH.exists():
        raise FileNotFoundError(f"Evaluator prompt not found: {EVALUATOR_PROMPT_PATH}")
    return EVALUATOR_PROMPT_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _rubric_config() -> dict[str, Any]:
    """Rubric criteria + grader model + reasoning level, loaded on first call.

    Model is sourced from agents.json (evaluation.model); rubric.json provides
    criteria and optional reasoning override only.
    """
    if not RUBRIC_PATH.exists():
        raise FileNotFoundError(f"Rubric file not found: {RUBRIC_PATH}")
    data = json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Rubric file {RUBRIC_PATH} must be a JSON object")
    criteria = data.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise ValueError(f"Rubric file {RUBRIC_PATH} must define a non-empty 'criteria' list")
    model = _evaluation_model()
    reasoning = data.get("reasoning") or "low"
    return {"criteria": criteria, "model": model, "reasoning": reasoning}


def rubric_criteria() -> list[dict[str, Any]]:
    return _rubric_config()["criteria"]


def rubric_model() -> str:
    """Grader model: GRADER_MODEL_OVERRIDE env if set, else agents.json evaluation.model."""
    return _grader_model_override() or _rubric_config()["model"]


def rubric_reasoning_effort() -> str:
    """Global (fallback) grader reasoning effort from rubric.json (default 'low')."""
    return _rubric_config()["reasoning"]


def criterion_reasoning_effort(criterion: dict[str, Any]) -> str:
    """Reasoning effort for one criterion: its own ``reasoning`` field, else the global default."""
    own = criterion.get("reasoning")
    if isinstance(own, str) and own.strip():
        return own.strip()
    return rubric_reasoning_effort()


def reasoning_effort_by_criterion() -> dict[str, str]:
    """Map every question-level criterion name to its resolved reasoning effort."""
    return {
        criterion["name"]: criterion_reasoning_effort(criterion)
        for criterion in question_level_criteria()
        if criterion.get("name")
    }


def rubric_criteria_by_name() -> dict[str, dict[str, Any]]:
    return {
        criterion["name"]: criterion
        for criterion in rubric_criteria()
        if criterion.get("name")
    }


def question_level_criteria() -> list[dict[str, Any]]:
    return [criterion for criterion in rubric_criteria() if criterion.get("name")]


@lru_cache(maxsize=1)
def rubric_payload() -> dict[str, Any]:
    """Return the full rubric.json contents with model from agents.json (or a stub if absent)."""
    model = _evaluation_model()
    if not RUBRIC_PATH.exists():
        return {"model": model, "criteria": []}
    try:
        data = json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"model": model, "criteria": []}
        return {**data, "model": model}
    except Exception:
        return {"model": model, "criteria": []}


def _to_jsonl(items: list[dict[str, Any]]) -> str:
    """Serialize evaluation items to JSONL text."""
    return "\n".join(json.dumps({"item": item}, ensure_ascii=False) for item in items)


def _evaluation_relative_path(path: Path) -> str:
    """Return the report-relative path used in persisted evaluation metadata."""
    return f"{EVALUATION_OUTPUTS_DIRNAME}/{path.name}"


def _safe_relative_display_path(path: Path) -> str:
    """Return a stable, human-readable path label for the UI."""
    uploads_dir = (REPO_ROOT / "uploads").resolve()
    try:
        relative_upload_path = path.resolve().relative_to(uploads_dir)
    except ValueError:
        relative_upload_path = None
    if relative_upload_path is not None:
        return str(Path("uploads") / relative_upload_path.name)

    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return path.name


def _categorize_source_file(path: Path) -> str:
    """Classify a source file as a user input or a bundled test fixture."""
    try:
        path.resolve().relative_to(EVALUATION_FIXTURES_DIR.resolve())
        return "test"
    except ValueError:
        return "input"


def _build_run_input_metadata(source_files: list[str]) -> list[dict[str, str]]:
    """Summarize the source files used for the current run."""
    entries: list[dict[str, str]] = []
    seen: set[str] = set()

    for raw_path in source_files:
        if not raw_path:
            continue
        path = Path(str(raw_path))
        identifier = str(path.resolve()) if path.exists() else str(path)
        if identifier in seen:
            continue
        seen.add(identifier)
        entries.append(
            {
                "name": path.name,
                "kind": _categorize_source_file(path),
                "display_path": _safe_relative_display_path(path),
            }
        )

    return entries


def _coerce_eval_passed(value: Any) -> Optional[bool]:
    """Normalize eval pass/fail values to booleans when possible."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"pass", "passed", "true"}:
            return True
        if normalized in {"fail", "failed", "false"}:
            return False
    return None


def _extract_eval_result_maps(record: dict[str, Any]) -> tuple[dict[str, float], dict[str, bool]]:
    """Extract criterion score and pass maps from one eval output record."""
    grades: dict[str, float] = {}
    passes: dict[str, bool] = {}

    for result in record.get("results", []) or []:
        if not isinstance(result, dict):
            continue
        raw_name = str(result.get("name") or result.get("criterion") or "")
        criterion_name = _normalize_criterion_name(raw_name) or raw_name
        if not criterion_name:
            continue

        score = result.get("score")
        if isinstance(score, (int, float)):
            grades[criterion_name] = float(score)

        passed = _coerce_eval_passed(
            result.get("passed")
            if "passed" in result
            else result.get("pass")
            if "pass" in result
            else result.get("status")
        )
        if passed is not None:
            passes[criterion_name] = passed

    return grades, passes


def _build_spreadsheet_rows(
    dataset_items: list[dict[str, Any]],
    output_items: list[dict[str, Any]],
    threshold: float,
) -> list[dict[str, Any]]:
    """Build flat rows for spreadsheet export."""
    rows: list[dict[str, Any]] = []
    criteria = [criterion["name"] for criterion in question_level_criteria()]

    if output_items:
        records: list[dict[str, Any]] = output_items
    else:
        records = [{"datasource_item": item, "results": []} for item in dataset_items]

    for index, record in enumerate(records, start=1):
        item = record.get("datasource_item") or record.get("item") or {}
        if not isinstance(item, dict):
            item = {}
        grades, passes = _extract_eval_result_maps(record)
        na_names = {
            name
            for name in criteria
            if isinstance(grades.get(name), (int, float)) and float(grades[name]) < 0
        }
        known_results = [
            name for name in criteria
            if (name in grades or name in passes) and name not in na_names
        ]
        failed_names = [
            name
            for name in criteria
            if name not in na_names and (
                passes.get(name) is False
                or (
                    name not in passes
                    and isinstance(grades.get(name), (int, float))
                    and float(grades[name]) < threshold
                )
            )
        ]
        passed_names = [
            name
            for name in criteria
            if name in known_results and name not in failed_names
        ]

        row: dict[str, Any] = {
            "row": index,
            "source_id": item.get("source_id", ""),
            "status": (
                "failed"
                if failed_names
                else "passed"
                if known_results
                else "unknown"
            ),
            "passed_criteria_count": len(passed_names),
            "failed_criteria_count": len(failed_names),
            "failed_criteria": " | ".join(failed_names),
            "source_question_text": item.get("source_question_text", ""),
            "generated_question_text": item.get("generated_question_text", ""),
        }

        for criterion_name in criteria:
            score = grades.get(criterion_name)
            if criterion_name in na_names:
                row[f"{criterion_name}_score"] = "NA"
                row[f"{criterion_name}_passed"] = "NA"
            else:
                row[f"{criterion_name}_score"] = (
                    round(score, 2) if isinstance(score, (int, float)) else ""
                )
                if criterion_name in passes:
                    row[f"{criterion_name}_passed"] = "TRUE" if passes[criterion_name] else "FALSE"
                else:
                    row[f"{criterion_name}_passed"] = ""

        rows.append(row)

    return rows


def _write_evaluation_spreadsheet(
    path: Path,
    dataset_items: list[dict[str, Any]],
    output_items: list[dict[str, Any]],
    threshold: float,
) -> bool:
    """Write a spreadsheet-friendly CSV export for the eval modal."""
    rows = _build_spreadsheet_rows(dataset_items, output_items, threshold)
    if not rows:
        return False

    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return True


def _write_rubric_snapshot(path: Path) -> Optional[str]:
    """Copy the rubric file used for the run into the evaluation output folder."""
    try:
        path.write_text(RUBRIC_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        logger.exception("Failed to write rubric snapshot to %s", path)
        return None
    return _evaluation_relative_path(path)


def _attach_report_file_metadata(
    report: dict[str, Any],
    *,
    report_path: Path,
    dataset_path: Optional[Path],
    output_items_path: Optional[Path],
    spreadsheet_path: Optional[Path],
    rubric_file: str,
    source_files: list[str],
) -> dict[str, Any]:
    """Attach UI-facing file references to an evaluation report."""
    testing_files = [
        {
            "name": _evaluation_relative_path(report_path),
            "label": "Evaluation report",
            "kind": "report",
        }
    ]
    if dataset_path and dataset_path.exists():
        testing_files.append(
            {
                "name": _evaluation_relative_path(dataset_path),
                "label": "Evaluation dataset",
                "kind": "dataset",
            }
        )
    if output_items_path and output_items_path.exists():
        testing_files.append(
            {
                "name": _evaluation_relative_path(output_items_path),
                "label": "Evaluation agent outputs",
                "kind": "items",
            }
        )
    if spreadsheet_path and spreadsheet_path.exists():
        testing_files.append(
            {
                "name": _evaluation_relative_path(spreadsheet_path),
                "label": "Spreadsheet export",
                "kind": "spreadsheet",
            }
        )

    report["run_inputs"] = _build_run_input_metadata(source_files)
    report["testing_files"] = testing_files
    report["rubric_file"] = rubric_file or ""
    report["rubric_source_path"] = "data/rubric.json"
    if spreadsheet_path and spreadsheet_path.exists():
        report["spreadsheet_file"] = _evaluation_relative_path(spreadsheet_path)
    return report


def _build_item_schema() -> dict[str, Any]:
    """Return the item schema used when creating the OpenAI eval dataset.

    ``source_distractor_logic`` is an optional per-item summary of the source
    option ``distractor_logic`` labels, surfaced so the grader can judge the
    ``distractor_structure_preservation`` criterion against the declared roles
    rather than re-inferring them from option text. Empty string for legacy items.
    """
    return {
        "type": "object",
        "properties": {
            "source_id": {"type": "string"},
            "source_question_text": {"type": "string"},
            "generated_question_text": {"type": "string"},
            "source_distractor_logic": {"type": "string"},
        },
        "required": [
            "source_id",
            "source_question_text",
            "generated_question_text",
            "source_distractor_logic",
        ],
        "additionalProperties": False,
    }


def _format_guidance_lines(value: Any) -> list[str]:
    """Normalize guidance text into a list of non-empty lines."""
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        lines: list[str] = []
        for item in value:
            if isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    lines.append(stripped)
        return lines
    return []


def _format_criterion_prompt(criterion: dict[str, Any]) -> str:
    """Render a rubric criterion into a grader prompt fragment."""
    question = str(criterion.get("question", "")).strip()
    lines = [f"Criterion: {question}" if question else "Criterion:"]

    guidance = _format_guidance_lines(criterion.get("guidance"))
    if guidance:
        lines.append("\nCriterion-specific guidance:")
        lines.extend(f"- {line}" for line in guidance)

    scoring_guide = _format_guidance_lines(criterion.get("scoring_guide"))
    if scoring_guide:
        lines.append("\nScoring guide:")
        lines.extend(f"- {line}" for line in scoring_guide)
    else:
        lines.append(
            "\nScoring guide: return 1 (pass) if the criterion is satisfied per the "
            "guidance above; return 0 (fail) otherwise."
        )

    return "\n".join(lines)


def _normalize_whitespace(value: str) -> str:
    """Collapse trailing whitespace while preserving intentional line breaks."""
    return "\n".join(line.rstrip() for line in value.strip().splitlines()).strip()


def _format_option_lines(options: list[dict[str, Any]]) -> list[str]:
    """Render answer options into plain text lines.

    When an option declares a ``distractor_logic`` label, it is appended as
    ``distractor_logic: <label>`` so the grader can judge the
    ``distractor_structure_preservation`` criterion against the declared solver
    role. Legacy options without the label render unchanged.
    """
    lines: list[str] = []
    for option in options:
        key = str(option.get("key", "")).strip()
        text = str(option.get("text", "")).strip()
        correctness = "correct" if bool(option.get("is_correct")) else "incorrect"
        explanation = str(option.get("explanation", "")).strip()
        distractor_logic = str(option.get("distractor_logic", "")).strip()
        if not key and not text:
            continue
        prefix = f"{key}. " if key else "- "
        line = f"{prefix}{text} [{correctness}]"
        if explanation:
            line = f"{line} Explanation: {explanation}"
        if distractor_logic:
            line = f"{line} distractor_logic: {distractor_logic}"
        lines.append(line)
    return lines


def _format_part_lines(parts: list[dict[str, Any]]) -> list[str]:
    """Render multi-part question sections into plain text lines."""
    lines: list[str] = []
    for part in parts:
        label = str(part.get("part_label", "")).strip()
        stem = str(part.get("stem", "")).strip()
        question_type = str(part.get("question_type", "")).strip()
        header = f"Part {label}: {stem}" if label else stem
        if header:
            lines.append(header)
        if question_type == "multiple_choice":
            lines.extend(_format_option_lines(part.get("options", [])))
            answer = str(part.get("answer", "")).strip()
            if answer:
                lines.append(f"Part answer: {answer}")
        else:
            answer = str(part.get("answer", "")).strip()
            if answer:
                lines.append(f"Part answer: {answer}")
    return lines


def _render_question_text(question: dict[str, Any]) -> str:
    """Render one question record into comparable plain text."""
    stem = str(question.get("stem", "")).strip()
    question_type = str(question.get("question_type", "")).strip()
    skipped = bool(question.get("skipped"))

    lines: list[str] = []
    # Shared-context group preamble: include it so grouped members are judged with
    # their full context instead of looking incomplete on their own.
    group_context = str(question.get("group_context", "")).strip()
    if group_context:
        lines.append(f"Shared context: {group_context}")
    if stem:
        lines.append(stem)

    if skipped:
        skip_reason = str(question.get("skip_reason", "")).strip()
        if skip_reason:
            lines.append(f"Skipped: {skip_reason}")
        return _normalize_whitespace("\n".join(lines))

    if question_type == "multiple_choice" or question.get("options"):
        lines.extend(_format_option_lines(question.get("options", [])))
        answer = str(question.get("answer", "")).strip()
        if answer:
            lines.append(f"Answer: {answer}")
    elif question_type == "multi_part" or question.get("parts"):
        lines.extend(_format_part_lines(question.get("parts", [])))
    else:
        answer = str(question.get("answer", "")).strip()
        if answer:
            lines.append(f"Answer: {answer}")

    return _normalize_whitespace("\n".join(lines))


def _collect_distractor_logic_labels(question: dict[str, Any]) -> list[str]:
    """Collect non-empty ``distractor_logic`` labels from a question's options/parts."""
    labels: list[str] = []

    def _from_options(options: Any) -> None:
        if not isinstance(options, list):
            return
        for option in options:
            if not isinstance(option, dict):
                continue
            label = str(option.get("distractor_logic", "")).strip()
            if label:
                labels.append(label)

    _from_options(question.get("options"))
    parts = question.get("parts")
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, dict):
                _from_options(part.get("options"))
    return labels


def _summarize_source_distractor_logic(question: dict[str, Any]) -> str:
    """Return a compact, one-line summary of the source distractor_logic roles.

    Empty string when the source declares no labels (legacy items), which keeps
    the grader robust when the metadata is absent.
    """
    labels = _collect_distractor_logic_labels(question)
    if not labels:
        return ""
    return "; ".join(labels)


def _render_source_question_text(question: dict[str, Any]) -> str:
    """Render the source question text for eval comparisons."""
    source_raw = str(question.get("source_raw", "")).strip()
    if source_raw:
        return _normalize_whitespace(source_raw)
    return _render_question_text(question)


def _render_generated_question_text(question: dict[str, Any]) -> str:
    """Render the generated question text for eval comparisons."""
    return _render_question_text(question)


def _failure_tag_instruction() -> str:
    """Instruction telling the grader to tag every failure with one FAILURE_TAG."""
    tag_list = ", ".join(FAILURE_TAGS)
    return (
        "When you return 0 (fail), the FINAL conclusion of your reasoning must begin "
        "with exactly one bracketed failure tag from this fixed list, followed by a "
        f"short justification: {tag_list}. For example: '[WRONG_ANSWER] the marked "
        "correct option is not actually correct.' Use exactly one tag; choose OTHER "
        "only when none of the specific tags fit. Do not add a tag when you return 1 "
        "(pass) or -1 (N/A). This tag does not change the numeric score."
    )


def _build_testing_criteria(model: str | None = None) -> list[dict[str, Any]]:
    """Build the criterion definitions used when creating an eval.

    Each grader is seeded with :data:`GRADER_SEED` for reproducibility and uses the
    per-criterion reasoning effort (``criterion_reasoning_effort``), falling back to
    the global rubric ``reasoning``. Failing scores are tagged from
    :data:`FAILURE_TAGS`.
    """
    model = model or rubric_model()
    criteria: list[dict[str, Any]] = []
    failure_tag_instruction = _failure_tag_instruction()
    for criterion in question_level_criteria():
        na_allowed = bool(criterion.get("na_allowed"))
        score_range = [-1, 1] if na_allowed else [0, 1]
        if na_allowed:
            return_instruction = (
                "Return exactly -1 if the criterion is Not Applicable for this source "
                "(per the N/A rule in the rubric guidance), 1 if the criterion is satisfied (pass), "
                "or 0 if not (fail). Do not return any other value."
            )
        else:
            return_instruction = (
                "Return exactly 1 if the criterion is satisfied (pass) or 0 if not (fail). "
                "Do not return any other value."
            )
        criteria.append(
            {
                "name": criterion["name"],
                "type": "score_model",
                "model": model,
                "sampling_params": {
                    "reasoning_effort": criterion_reasoning_effort(criterion),
                    "seed": GRADER_SEED,
                },
                "pass_threshold": PASSING_SCORE_THRESHOLD,
                "range": score_range,
                "input": [
                    {
                        "role": "system",
                        "content": (
                            f"{general_grader_instructions()}\n"
                            f"{_format_criterion_prompt(criterion)}\n\n"
                            f"{return_instruction} "
                            "Apply the rules in the rubric guidance literally, "
                            "and judge only the provided source and generated question text.\n\n"
                            f"{failure_tag_instruction}"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "Source question:\n{{ item.source_question_text }}\n\n"
                            "Source distractor_logic roles (may be empty for legacy items):\n"
                            "{{ item.source_distractor_logic }}\n\n"
                            "Generated question:\n{{ item.generated_question_text }}"
                        ),
                    },
                ],
            }
        )
    return criteria


def _build_run_data_source(eval_data_file_id: str) -> dict[str, Any]:
    """Build the data source descriptor for an eval run."""
    return {
        "type": "jsonl",
        "source": {"type": "file_id", "id": eval_data_file_id},
    }


def _normalize_criterion_name(result_name: str) -> Optional[str]:
    """Map an eval result name back to a rubric criterion name when possible."""
    by_name = rubric_criteria_by_name()
    if result_name in by_name:
        return result_name
    for criterion_name in by_name:
        if result_name.startswith(f"{criterion_name}-"):
            return criterion_name
    return None


def parse_sample_steps(sample: Any) -> list[dict[str, str]]:
    """Parse a grader ``sample`` into a list of ``{conclusion, description}`` steps.

    Mirrors the frontend ``parseSampleSteps`` (modals/eval/evalData.js): the grader
    reasoning lives at ``sample.output[i].content`` as a JSON string
    ``{result, steps: [{conclusion, description}]}``. Finds the first output element
    whose ``content`` is a string, parses it, and returns its ``steps``. When the
    content is non-JSON, returns a single ``{"conclusion": "", "description": <text>}``
    step. Robust to ``sample`` being a dict, an SDK object, or missing entirely.

    Exported for the downstream meta-evaluator to mine failure reasons from stored
    ``evaluation_agents.jsonl`` records without re-implementing the parse.
    """
    jsonable = to_jsonable(sample) if sample is not None else None
    if not isinstance(jsonable, dict):
        return []
    outputs = jsonable.get("output")
    if not isinstance(outputs, list):
        return []

    output_content: Optional[str] = None
    for entry in outputs:
        if isinstance(entry, dict) and isinstance(entry.get("content"), str):
            output_content = entry["content"]
            break
    if not output_content:
        return []

    try:
        parsed = json.loads(output_content)
    except (ValueError, TypeError):
        return [{"conclusion": "", "description": output_content}]

    if isinstance(parsed, dict) and isinstance(parsed.get("steps"), list):
        return [
            {
                "conclusion": str(step.get("conclusion", "")) if isinstance(step, dict) else "",
                "description": str(step.get("description", "")) if isinstance(step, dict) else "",
            }
            for step in parsed["steps"]
        ]
    return []


def _extract_failure_tag(sample: Any) -> Optional[str]:
    """Return the bracketed FAILURE_TAG from a grader sample's final conclusion, if any.

    The grader is instructed to begin its final conclusion with exactly one
    ``[TAG]`` from :data:`FAILURE_TAGS` when returning 0 (fail). We scan the parsed
    steps' text (conclusion + description) for the first recognized bracketed tag.
    Returns the canonical tag string or None when absent/unrecognized.
    """
    steps = parse_sample_steps(sample)
    if not steps:
        return None
    valid = set(FAILURE_TAGS)
    # Prefer the last step's conclusion (the "final conclusion"), then fall back to
    # any step text, since graders vary in where they place the summary tag.
    ordered_texts: list[str] = []
    for step in reversed(steps):
        ordered_texts.append(step.get("conclusion", ""))
        ordered_texts.append(step.get("description", ""))
    for text in ordered_texts:
        if not text:
            continue
        for match in re.findall(r"\[([A-Z_]+)\]", text):
            if match in valid:
                return match
    return None


def _collect_output_items(client: OpenAI, eval_id: str, run_id: str) -> list[Any]:
    """Collect all output items from a completed eval run."""
    first_page = client.evals.runs.output_items.list(
        eval_id=eval_id,
        run_id=run_id,
        limit=100,
        order="asc",
    )
    items: list[Any] = []
    for page in first_page.iter_pages():
        items.extend(getattr(page, "data", []))
    return items


def _write_output_items_jsonl(path: Path, output_items: list[Any]) -> None:
    """Write eval output items to a JSONL file."""
    lines = [
        json.dumps(to_jsonable(output_item), ensure_ascii=False, default=str)
        for output_item in output_items
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _aggregate_question_scores(output_items: list[Any]) -> tuple[dict[str, Any], list[str]]:
    """Aggregate per-criterion scores and return any unmatched result names.

    N/A scores (-1) are tracked separately as ``na_count`` rather than silently
    dropped, so consumers can distinguish "all items judged N/A" from "no items
    received a pass".
    """
    criteria = question_level_criteria()
    criterion_scores: dict[str, list[float]] = {
        criterion["name"]: [] for criterion in criteria
    }
    criterion_na: dict[str, int] = {
        criterion["name"]: 0 for criterion in criteria
    }
    # Per-criterion tally of the bracketed FAILURE_TAGS mined from grader reasoning
    # on failing (score 0) items, for the downstream meta-evaluator.
    criterion_failure_tags: dict[str, dict[str, int]] = {
        criterion["name"]: {} for criterion in criteria
    }
    unmatched_results: list[str] = []

    for output_item in output_items:
        for result in getattr(output_item, "results", []) or []:
            raw_name = str(getattr(result, "name", "") or "")
            criterion_name = _normalize_criterion_name(raw_name)
            if not criterion_name:
                if raw_name:
                    unmatched_results.append(raw_name)
                continue

            score = getattr(result, "score", None)
            if isinstance(score, (int, float)):
                value = float(score)
                if value < 0:
                    criterion_na[criterion_name] = criterion_na.get(criterion_name, 0) + 1
                else:
                    criterion_scores[criterion_name].append(value)
                    if value < 1.0:
                        tag = _extract_failure_tag(getattr(result, "sample", None))
                        tag = tag or "OTHER"
                        tags = criterion_failure_tags.setdefault(criterion_name, {})
                        tags[tag] = tags.get(tag, 0) + 1

    summary: dict[str, Any] = {}
    missing_scores: list[str] = []
    for criterion in criteria:
        name = criterion["name"]
        values = criterion_scores[name]
        na_count = criterion_na.get(name, 0)
        average_score = round(sum(values) / len(values), 4) if values else None
        if average_score is None and na_count == 0:
            missing_scores.append(name)
        items_passed = sum(1 for value in values if value >= 1.0)
        total_observed = len(values) + na_count
        summary[name] = {
            "category": criterion.get("category", ""),
            "question": criterion.get("question", ""),
            "score": average_score,
            "pass_rate": average_score,
            "items_passed": items_passed,
            "items_scored": len(values),
            "na_count": na_count,
            "na_rate": round(na_count / total_observed, 4) if total_observed else None,
            "failure_tag_counts": dict(criterion_failure_tags.get(name, {})),
        }

    return summary, sorted(set(unmatched_results + missing_scores))


def _compute_overall_score(question_level_scores: dict[str, Any]) -> Optional[float]:
    """Average of question-level criterion scores."""
    numeric_scores = [
        entry["score"]
        for entry in question_level_scores.values()
        if isinstance(entry.get("score"), (int, float))
    ]
    if not numeric_scores:
        return None
    return round(sum(numeric_scores) / len(numeric_scores), 2)


def _build_base_report(
    *,
    status: str,
    reason: str = "",
    grader_model: str,
    dataset_path: Optional[Path],
    items_evaluated: int,
) -> dict[str, Any]:
    """Build the common evaluation report skeleton."""
    report: dict[str, Any] = {
        "status": status,
        "created_at": utc_now_iso(),
        "rubric": rubric_criteria(),
        "grader_model": grader_model,
        "grader_seed": GRADER_SEED,
        "reasoning_effort_by_criterion": reasoning_effort_by_criterion(),
        "items_evaluated": items_evaluated,
        "dataset_file": _evaluation_relative_path(dataset_path) if dataset_path else "",
        "criterion_scores": {},
        "overall_score": _compute_overall_score({}),
        "passing_score_threshold": PASSING_SCORE_THRESHOLD,
    }
    if reason:
        report["reason"] = reason
    return report


def run_openai_output_evaluation(
    client: OpenAI,
    final_result: dict[str, Any],
    outputs_dir: Path,
    source_files: Optional[list[str]] = None,
    grader_model: Optional[str] = None,
    contract_report: Optional[dict[str, Any]] = None,
    poll_interval_seconds: float = 3.0,
    max_wait_seconds: int = 900,
) -> dict[str, Any]:
    """Evaluate generated questions using OpenAI Evals and save evaluation_report.json."""
    evaluation_outputs_dir = outputs_dir / EVALUATION_OUTPUTS_DIRNAME
    evaluation_outputs_dir.mkdir(parents=True, exist_ok=True)
    report_path = evaluation_outputs_dir / EVALUATION_REPORT_FILENAME
    dataset_path = evaluation_outputs_dir / EVALUATION_DATASET_FILENAME
    output_items_path = evaluation_outputs_dir / EVALUATION_ITEMS_FILENAME
    spreadsheet_path = evaluation_outputs_dir / EVALUATION_SPREADSHEET_FILENAME
    rubric_snapshot_path = evaluation_outputs_dir / EVALUATION_RUBRIC_FILENAME
    grader_model = grader_model or rubric_model()
    rubric_file = _write_rubric_snapshot(rubric_snapshot_path) or ""
    source_files = source_files or []

    generated_questions = final_result.get("generated_questions", [])
    deterministic_checks = {
        "generation_contract": contract_report or {},
        "figure_skips": {
            "status": "passed",
            "checked_source_ids": [],
            "errors": [],
        },
    }
    if not generated_questions:
        report = _build_base_report(
            status="skipped",
            reason="No generated questions to evaluate",
            grader_model=grader_model,
            dataset_path=None,
            items_evaluated=0,
        )
        report["deterministic_checks"] = deterministic_checks
        report = _attach_report_file_metadata(
            report,
            report_path=report_path,
            dataset_path=None,
            output_items_path=None,
            spreadsheet_path=None,
            rubric_file=rubric_file,
            source_files=source_files,
        )
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    source_candidates = (
        final_result.get("verified_questions")
        or final_result.get("source_questions")
        or []
    )
    source_by_id = {q.get("source_id"): q for q in source_candidates if q.get("source_id")}
    generated_by_source: dict[str, list[dict[str, Any]]] = {}
    for generated in generated_questions:
        source_id = str(generated.get("source_id") or "")
        if source_id:
            generated_by_source.setdefault(source_id, []).append(generated)

    if contract_report and isinstance(contract_report, dict):
        figure_ids = [
            str(s.get("source_id") or "")
            for s in source_candidates
            if isinstance(s, dict) and s.get("has_figure")
        ]
        deterministic_checks["figure_skips"] = {
            "status": contract_report.get("status", "passed"),
            "checked_source_ids": figure_ids,
            "errors": [
                err for err in contract_report.get("errors", [])
                if "figure" in err.lower() or "skipped" in err.lower()
            ],
        }
    else:
        figure_skip_errors: list[str] = []
        checked_figure_ids: list[str] = []
        for source in source_candidates:
            if not isinstance(source, dict) or not source.get("has_figure"):
                continue
            source_id = str(source.get("source_id") or "")
            checked_figure_ids.append(source_id)
            items_for_source = generated_by_source.get(source_id, [])
            if len(items_for_source) != 1:
                figure_skip_errors.append(f"{source_id}: expected one skipped placeholder")
                continue
            generated = items_for_source[0]
            if (
                not generated.get("skipped")
                or generated.get("stem")
                or generated.get("options")
                or generated.get("parts")
                or not str(generated.get("skip_reason") or "").strip()
            ):
                figure_skip_errors.append(f"{source_id}: invalid skipped placeholder")
        deterministic_checks["figure_skips"] = {
            "status": "failed" if figure_skip_errors else "passed",
            "checked_source_ids": checked_figure_ids,
            "errors": figure_skip_errors,
        }

    if not hasattr(client, "evals"):
        report = _build_base_report(
            status="skipped",
            reason="OpenAI client has no evals API surface",
            grader_model=grader_model,
            dataset_path=None,
            items_evaluated=0,
        )
        report["deterministic_checks"] = deterministic_checks
        report = _attach_report_file_metadata(
            report,
            report_path=report_path,
            dataset_path=None,
            output_items_path=None,
            spreadsheet_path=None,
            rubric_file=rubric_file,
            source_files=source_files,
        )
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    items: list[dict[str, Any]] = []
    for generated in generated_questions:
        source = source_by_id.get(generated.get("source_id"))
        if source is None:
            continue
        if source.get("has_figure") and generated.get("skipped"):
            continue
        items.append(
            {
                "source_id": str(generated.get("source_id", "")),
                "source_question_text": _render_source_question_text(source),
                "generated_question_text": _render_generated_question_text(generated),
                "source_distractor_logic": _summarize_source_distractor_logic(source),
            }
        )

    if not items:
        report = _build_base_report(
            status="skipped",
            reason="No source/generated pairs available for evaluation",
            grader_model=grader_model,
            dataset_path=None,
            items_evaluated=0,
        )
        report["deterministic_checks"] = deterministic_checks
        report = _attach_report_file_metadata(
            report,
            report_path=report_path,
            dataset_path=None,
            output_items_path=None,
            spreadsheet_path=None,
            rubric_file=rubric_file,
            source_files=source_files,
        )
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    dataset_path.write_text(_to_jsonl(items), encoding="utf-8")

    try:
        with open(dataset_path, "rb") as file_handle:
            eval_data_file = client.files.create(file=file_handle, purpose="evals")

        eval_obj = client.evals.create(
            name="Isomorphic Question Rubric Eval",
            metadata={"source": "isomorphic-question-generator"},
            data_source_config={
                "type": "custom",
                "item_schema": _build_item_schema(),
                "include_sample_schema": False,
            },
            testing_criteria=_build_testing_criteria(model=grader_model),
        )

        run = client.evals.runs.create(
            eval_id=eval_obj.id,
            name="Rubric Run",
            data_source=_build_run_data_source(eval_data_file.id),
        )

        started = time.time()
        current = run
        while True:
            current = client.evals.runs.retrieve(eval_id=eval_obj.id, run_id=current.id)
            status = getattr(current, "status", "")
            if status in {"completed", "failed", "cancelled"}:
                break
            if time.time() - started > max_wait_seconds:
                break
            time.sleep(poll_interval_seconds)

        run_status = getattr(current, "status", "unknown")
        report_status = "completed"
        if run_status == "failed":
            report_status = "failed"
        elif run_status == "cancelled":
            report_status = "cancelled"
        elif run_status not in {"completed"}:
            report_status = "incomplete"

        criterion_scores: dict[str, Any] = {}
        aggregation_notes: list[str] = []
        output_items: list[Any] = []
        output_item_records: list[dict[str, Any]] = []

        coverage_mismatch: Optional[dict[str, Any]] = None
        if run_status == "completed":
            output_items = _collect_output_items(client, eval_obj.id, current.id)
            if output_items:
                _write_output_items_jsonl(
                    output_items_path,
                    output_items,
                )
                output_item_records = [
                    to_jsonable(output_item) for output_item in output_items
                ]
            criterion_scores, aggregation_notes = _aggregate_question_scores(output_items)
            expected = len(items)
            actual = len(output_items)
            if expected != actual:
                coverage_mismatch = {
                    "expected_items": expected,
                    "received_items": actual,
                    "missing": expected - actual,
                }
                aggregation_notes.append(
                    f"coverage_mismatch: expected {expected} eval items, received {actual}"
                )
                logger.warning(
                    "Eval coverage mismatch: expected %d items, received %d",
                    expected, actual,
                )

        _write_evaluation_spreadsheet(
            spreadsheet_path,
            dataset_items=items,
            output_items=output_item_records,
            threshold=PASSING_SCORE_THRESHOLD,
        )

        overall_score = _compute_overall_score(criterion_scores)
        if report_status == "completed":
            report_status = "complete" if isinstance(overall_score, (int, float)) else "incomplete"
        if deterministic_checks.get("figure_skips", {}).get("status") == "failed":
            report_status = "failed"
        if coverage_mismatch:
            deterministic_checks["coverage_mismatch"] = coverage_mismatch
        report = {
            "status": report_status,
            "created_at": utc_now_iso(),
            "rubric": rubric_criteria(),
            "items_evaluated": len(items),
            "grader_model": grader_model,
            "grader_seed": GRADER_SEED,
            "reasoning_effort_by_criterion": reasoning_effort_by_criterion(),
            "dataset_file": _evaluation_relative_path(dataset_path),
            "criterion_scores": criterion_scores,
            "overall_score": overall_score,
            "passing_score_threshold": PASSING_SCORE_THRESHOLD,
            "eval_data_file_id": eval_data_file.id,
            "eval_id": eval_obj.id,
            "run_id": current.id,
            "run_status": run_status,
            "output_items_count": len(output_items),
            "deterministic_checks": deterministic_checks,
        }
        if output_items_path.exists():
            report["output_items_file"] = _evaluation_relative_path(output_items_path)

        if hasattr(current, "report_url"):
            report["report_url"] = current.report_url

        if report_status == "incomplete":
            report["reason"] = (
                f"Eval run did not reach a terminal state within {max_wait_seconds} seconds"
            )
        elif report_status in {"failed", "cancelled"}:
            report["reason"] = f"Eval run ended with status: {run_status}"

        if aggregation_notes:
            report["aggregation_notes"] = aggregation_notes

        report = _attach_report_file_metadata(
            report,
            report_path=report_path,
            dataset_path=dataset_path,
            output_items_path=output_items_path,
            spreadsheet_path=spreadsheet_path,
            rubric_file=rubric_file,
            source_files=source_files,
        )
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    except Exception as exc:
        logger.exception("Evaluation run failed for outputs at %s", outputs_dir)
        report = {
            "status": "failed",
            "created_at": utc_now_iso(),
            "rubric": rubric_criteria(),
            "items_evaluated": len(items),
            "grader_model": grader_model,
            "grader_seed": GRADER_SEED,
            "reasoning_effort_by_criterion": reasoning_effort_by_criterion(),
            "dataset_file": _evaluation_relative_path(dataset_path),
            "criterion_scores": {},
            "overall_score": _compute_overall_score({}),
            "passing_score_threshold": PASSING_SCORE_THRESHOLD,
            "error": str(exc),
            "deterministic_checks": deterministic_checks,
        }
        if dataset_path.exists():
            _write_evaluation_spreadsheet(
                spreadsheet_path,
                dataset_items=items,
                output_items=[],
                threshold=PASSING_SCORE_THRESHOLD,
            )
        report = _attach_report_file_metadata(
            report,
            report_path=report_path,
            dataset_path=dataset_path if dataset_path.exists() else None,
            output_items_path=output_items_path if output_items_path.exists() else None,
            spreadsheet_path=spreadsheet_path if spreadsheet_path.exists() else None,
            rubric_file=rubric_file,
            source_files=source_files,
        )
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
