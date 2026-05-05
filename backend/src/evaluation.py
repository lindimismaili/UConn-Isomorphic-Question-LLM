"""
OpenAI Evals integration for generated isomorphic question outputs.

This module evaluates generated questions against rubric criteria and writes
an evaluation report to the run output directory.
"""

from __future__ import annotations

import csv
import json
import logging
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)


THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
DATA_DIR = REPO_ROOT / "data"
RUBRIC_PATH = DATA_DIR / "rubric.json"
LATEX_CRITERION_NAME = "latex_syntax_quality"
PASSING_SCORE_THRESHOLD = 70.0
EVALUATION_OUTPUTS_DIRNAME = "evaluation_outputs"
EVALUATION_REPORT_FILENAME = "evaluation_report.json"
EVALUATION_DATASET_FILENAME = "evaluation_dataset.jsonl"
EVALUATION_ITEMS_FILENAME = "evaluation_items.jsonl"
EVALUATION_SPREADSHEET_FILENAME = "evaluation_spreadsheet.csv"
EVALUATION_RUBRIC_FILENAME = "rubric.json"


DEFAULT_RUBRIC_CRITERIA: list[dict[str, Any]] = [
    {
        "name": "preserve_physical_content",
        "category": "Preservation",
        "question": "Does the generated question preserve the original physical content?",
        "guidance": [
            "Reward preservation of the underlying physics concept, law, modeling assumption, and required reasoning steps.",
            "Do not require the same story, object names, numerical values, or final numeric answer.",
            "Penalize changes that move to a different physics topic, require a different law, or change the intended conceptual distinction.",
        ],
    },
    {
        "name": "scenario_variation",
        "category": "Variation",
        "question": "Does the generated question use a scenario different from the original one?",
        "guidance": [
            "This criterion rewards appropriate surface-context variation while keeping the same physics task.",
            "A different real-world object, setting, or story should score high when the same concept and reasoning are preserved.",
            "Do not mark a changed scenario as a failure merely because it is different. Penalize exact copying, overly superficial edits, or changes that break the isomorphic physics mapping.",
        ],
    },
    {
        "name": "numerical_variation",
        "category": "Variation",
        "question": "If the original question is numerical, does the generated question appropriately modify numerical values?",
        "guidance": [
            "For numerical questions, reward changed values that are recalculated consistently throughout the stem, answer choices, and explanations.",
            "The generated question may have a different final value or correct option if the new values support it.",
            "For conceptual questions with only incidental scales or no meaningful numerical values, do not penalize the generated question for having little numerical variation.",
        ],
    },
    {
        "name": "physics_correctness",
        "category": "Quality control",
        "question": "Is the generated question free of physics errors or misconceptions?",
        "guidance": [
            "Judge the generated question on its own physics correctness.",
            "Check equations, units, signs, assumptions, and whether the marked or implied correct answer is actually correct.",
            "Do not penalize for being a different scenario if the generated physics is internally correct.",
        ],
    },
    {
        "name": "numeric_reasonableness",
        "category": "Quality control",
        "question": "Do all numerical values have physically reasonable magnitudes and units?",
        "guidance": [
            "Check dimensional consistency and whether magnitudes are plausible for the stated context.",
            "Incorrect distractor values can still be reasonable if they are plausible wrong answers with correct units.",
            "Penalize impossible units, nonsensical scales, or values that contradict the stated physical setup.",
        ],
    },
    {
        "name": "distractor_structure_preservation",
        "category": "Preservation",
        "question": "Does the generated question preserve the logical structure of the distractors?",
        "guidance": [
            "Compare the role of each answer choice, not the option letter. The generated question may reorder choices and move the correct answer.",
            "Reward preservation of the same misconception types, such as adding vector components instead of using magnitude, confusing coefficients with derivatives, or overgeneralizing an idealization.",
            "Pay attention to structural roles such as 'none of the above', single-correct vs multi-correct, and whether the same kind of correct statement remains present.",
        ],
    },
    {
        "name": "clerical_quality",
        "category": "Quality control",
        "question": "Is the generated question free of clerical errors (e.g., typos, missing symbols, ambiguous wording)?",
        "guidance": [
            "Judge spelling, notation, missing symbols, grammar that affects meaning, and formatting ambiguity.",
            "Do not penalize harmless wording changes or valid notation differences.",
        ],
    },
    {
        "name": "latex_syntax_quality",
        "category": "Quality control",
        "question": "Is the generated LaTeX code free of syntax errors?",
        "guidance": [
            "This criterion is checked from generated LaTeX/PDF artifacts rather than by the score model.",
        ],
    },
    {
        "name": "solvable_and_specified",
        "category": "Quality control",
        "question": "Is the generated question fully specified and solvable as written?",
        "guidance": [
            "Check whether the generated stem gives enough information for a unique intended answer.",
            "For multiple-choice questions, verify that the answer choices do not create multiple correct answers unless the stem asks for them.",
            "Do not require extra source details if the generated question is independently solvable.",
        ],
    },
]


GENERAL_GRADER_INSTRUCTIONS = """\
You are grading one generated isomorphic physics question against its source.

An isomorphic generated question should preserve the source question's core physics
idea and reasoning pattern while changing surface details such as names, settings,
numbers, and sometimes answer order. Grade only the current criterion. Do not
double-penalize an issue that belongs to another criterion unless it directly
affects the current criterion.

Before scoring, identify the source's intended answer logic and the generated
question's intended answer logic. For multiple-choice questions, compare answer
choice roles and misconception patterns; option letters do not need to match.
"""


def _load_rubric_config() -> dict[str, Any]:
    """Load rubric criteria and grading model configuration from disk."""
    if not RUBRIC_PATH.exists():
        raise FileNotFoundError(f"Rubric file not found: {RUBRIC_PATH}")

    data = json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Rubric file {RUBRIC_PATH} must be a JSON object")

    criteria = data.get("criteria")
    model = data.get("model")
    if not isinstance(criteria, list) or not criteria:
        logger.warning(
            "Rubric file %s is missing a valid criteria list; using default criteria",
            RUBRIC_PATH,
        )
        criteria = DEFAULT_RUBRIC_CRITERIA

    if not isinstance(model, str) or not model:
        raise ValueError(f"Rubric file {RUBRIC_PATH} must define a non-empty 'model' string")

    return {"criteria": criteria, "model": model}


RUBRIC_CONFIG = _load_rubric_config()
RUBRIC_CRITERIA = RUBRIC_CONFIG["criteria"]
RUBRIC_MODEL = RUBRIC_CONFIG["model"]
RUBRIC_CRITERIA_BY_NAME = {
    criterion["name"]: criterion for criterion in RUBRIC_CRITERIA if criterion.get("name")
}
QUESTION_LEVEL_CRITERIA = [
    criterion
    for criterion in RUBRIC_CRITERIA
    if criterion.get("name") and criterion["name"] != LATEX_CRITERION_NAME
]


def _utc_timestamp() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
        path.resolve().relative_to((DATA_DIR / "evaluation").resolve())
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
    criteria = [criterion["name"] for criterion in QUESTION_LEVEL_CRITERIA]

    if output_items:
        records: list[dict[str, Any]] = output_items
    else:
        records = [{"datasource_item": item, "results": []} for item in dataset_items]

    for index, record in enumerate(records, start=1):
        item = record.get("datasource_item") or record.get("item") or {}
        if not isinstance(item, dict):
            item = {}
        grades, passes = _extract_eval_result_maps(record)
        known_results = [name for name in criteria if name in grades or name in passes]
        failed_names = [
            name
            for name in criteria
            if passes.get(name) is False
            or (
                name not in passes
                and isinstance(grades.get(name), (int, float))
                and float(grades[name]) < threshold
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
                "label": "Evaluation item outputs",
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
    """Return the item schema used when creating the OpenAI eval dataset."""
    return {
        "type": "object",
        "properties": {
            "source_id": {"type": "string"},
            "source_question_text": {"type": "string"},
            "generated_question_text": {"type": "string"},
        },
        "required": ["source_id", "source_question_text", "generated_question_text"],
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
            "\nScoring guide: 90-100 means the criterion is strongly satisfied; "
            "70-89 means it is satisfied with minor issues; 40-69 means partial "
            "satisfaction; 0-39 means the criterion is mostly not satisfied."
        )

    return "\n".join(lines)


def _normalize_whitespace(value: str) -> str:
    """Collapse trailing whitespace while preserving intentional line breaks."""
    return "\n".join(line.rstrip() for line in value.strip().splitlines()).strip()


def _format_option_lines(options: list[dict[str, Any]]) -> list[str]:
    """Render answer options into plain text lines."""
    lines: list[str] = []
    for option in options:
        key = str(option.get("key", "")).strip()
        text = str(option.get("text", "")).strip()
        if not key and not text:
            continue
        prefix = f"{key}. " if key else "- "
        lines.append(f"{prefix}{text}")
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
    return lines


def _render_question_text(question: dict[str, Any]) -> str:
    """Render one question record into comparable plain text."""
    stem = str(question.get("stem", "")).strip()
    question_type = str(question.get("question_type", "")).strip()
    skipped = bool(question.get("skipped"))

    lines: list[str] = []
    if stem:
        lines.append(stem)

    if skipped:
        skip_reason = str(question.get("skip_reason", "")).strip()
        if skip_reason:
            lines.append(f"Skipped: {skip_reason}")
        return _normalize_whitespace("\n".join(lines))

    if question_type == "multiple_choice" or question.get("options"):
        lines.extend(_format_option_lines(question.get("options", [])))
    elif question_type == "multi_part" or question.get("parts"):
        lines.extend(_format_part_lines(question.get("parts", [])))

    return _normalize_whitespace("\n".join(lines))


def _render_source_question_text(question: dict[str, Any]) -> str:
    """Render the source question text for eval comparisons."""
    source_raw = str(question.get("source_raw", "")).strip()
    if source_raw:
        return _normalize_whitespace(source_raw)
    return _render_question_text(question)


def _render_generated_question_text(question: dict[str, Any]) -> str:
    """Render the generated question text for eval comparisons."""
    return _render_question_text(question)


def _build_testing_criteria(model: str = RUBRIC_MODEL) -> list[dict[str, Any]]:
    """Build the criterion definitions used when creating an eval."""
    criteria: list[dict[str, Any]] = []
    for criterion in QUESTION_LEVEL_CRITERIA:
        criteria.append(
            {
                "name": criterion["name"],
                "type": "score_model",
                "model": model,
                "pass_threshold": PASSING_SCORE_THRESHOLD,
                "range": [0, 100],
                "input": [
                    {
                        "role": "system",
                        "content": (
                            f"{GENERAL_GRADER_INSTRUCTIONS}\n"
                            f"{_format_criterion_prompt(criterion)}\n\n"
                            "Return a single numeric score from 0 to 100, where 0 means the criterion is not satisfied at all "
                            "and 100 means it is fully satisfied. Use the full range when appropriate. "
                            "Judge only the provided source and generated question text."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "Source question:\n{{ item.source_question_text }}\n\n"
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


def _resolve_latex_check(latex_path: Optional[Path]) -> dict[str, Any]:
    """Return a synthetic pass/fail record for the LaTeX artifact check."""
    criterion = RUBRIC_CRITERIA_BY_NAME.get(
        LATEX_CRITERION_NAME,
        {
            "name": LATEX_CRITERION_NAME,
            "category": "Quality control",
            "question": "Is the generated LaTeX code free of syntax errors?",
        },
    )

    pdf_path = latex_path.with_suffix(".pdf") if latex_path else None
    latex_exists = bool(latex_path and latex_path.exists())
    pdf_exists = bool(pdf_path and pdf_path.exists())
    passed = latex_exists and pdf_exists
    status = "passed" if passed else "failed"

    if passed:
        reason = "Generated LaTeX document and compiled PDF artifact are both present."
    elif latex_exists:
        reason = "Generated LaTeX document exists, but the compiled PDF artifact is missing."
    else:
        reason = "Generated LaTeX document is missing."

    return {
        "name": criterion["name"],
        "category": criterion.get("category", ""),
        "question": criterion.get("question", ""),
        "score": 100.0 if passed else 0.0,
        "status": status,
        "reason": reason,
        "latex_file": latex_path.name if latex_path else "",
        "pdf_file": pdf_path.name if pdf_path else "",
    }


def _normalize_criterion_name(result_name: str) -> Optional[str]:
    """Map an eval result name back to a rubric criterion name when possible."""
    if result_name in RUBRIC_CRITERIA_BY_NAME:
        return result_name
    for criterion_name in RUBRIC_CRITERIA_BY_NAME:
        if result_name.startswith(f"{criterion_name}-"):
            return criterion_name
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


def _to_jsonable(value: Any) -> Any:
    """Convert OpenAI eval output objects into JSON-serializable values."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


def _write_output_items_jsonl(path: Path, output_items: list[Any]) -> None:
    """Write eval output items to a JSONL file."""
    lines = [
        json.dumps(_to_jsonable(output_item), ensure_ascii=False, default=str)
        for output_item in output_items
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _aggregate_question_scores(output_items: list[Any]) -> tuple[dict[str, Any], list[str]]:
    """Aggregate per-criterion scores and return any unmatched result names."""
    criterion_scores: dict[str, list[float]] = {
        criterion["name"]: [] for criterion in QUESTION_LEVEL_CRITERIA
    }
    unmatched_results: list[str] = []

    for output_item in output_items:
        for result in getattr(output_item, "results", []) or []:
            raw_name = str(getattr(result, "name", "") or "")
            criterion_name = _normalize_criterion_name(raw_name)
            if not criterion_name or criterion_name == LATEX_CRITERION_NAME:
                if raw_name:
                    unmatched_results.append(raw_name)
                continue

            score = getattr(result, "score", None)
            if isinstance(score, (int, float)):
                criterion_scores[criterion_name].append(float(score))

    summary: dict[str, Any] = {}
    missing_scores: list[str] = []
    for criterion in QUESTION_LEVEL_CRITERIA:
        name = criterion["name"]
        values = criterion_scores[name]
        average_score = round(sum(values) / len(values), 2) if values else None
        if average_score is None:
            missing_scores.append(name)
        summary[name] = {
            "category": criterion.get("category", ""),
            "question": criterion.get("question", ""),
            "score": average_score,
            "items_scored": len(values),
        }

    return summary, sorted(set(unmatched_results + missing_scores))


def _compute_overall_score(
    question_level_scores: dict[str, Any],
    latex_document_check: dict[str, Any],
) -> Optional[float]:
    """Compute a combined score across question-level and LaTeX checks."""
    numeric_scores = [
        entry["score"]
        for entry in question_level_scores.values()
        if isinstance(entry.get("score"), (int, float))
    ]
    if not numeric_scores:
        return None
    latex_score = latex_document_check.get("score")
    if isinstance(latex_score, (int, float)):
        numeric_scores.append(float(latex_score))
    return round(sum(numeric_scores) / len(numeric_scores), 2)


def _resolve_score_status(overall_score: Optional[float]) -> str:
    """Translate the overall score into a coarse status label."""
    if not isinstance(overall_score, (int, float)):
        return "incomplete"
    return "passed" if float(overall_score) >= PASSING_SCORE_THRESHOLD else "failed"


def _build_base_report(
    *,
    status: str,
    reason: str = "",
    grader_model: str,
    dataset_path: Optional[Path],
    items_evaluated: int,
    latex_document_check: dict[str, Any],
) -> dict[str, Any]:
    """Build the common evaluation report skeleton."""
    report: dict[str, Any] = {
        "status": status,
        "created_at": _utc_timestamp(),
        "rubric": RUBRIC_CRITERIA,
        "grader_model": grader_model,
        "items_evaluated": items_evaluated,
        "dataset_file": _evaluation_relative_path(dataset_path) if dataset_path else "",
        "criterion_scores": {},
        "latex_document_check": latex_document_check,
        "overall_score": _compute_overall_score({}, latex_document_check),
        "passing_score_threshold": PASSING_SCORE_THRESHOLD,
    }
    if reason:
        report["reason"] = reason
    return report


def run_openai_output_evaluation(
    client: OpenAI,
    final_result: dict[str, Any],
    outputs_dir: Path,
    latex_path: Optional[Path] = None,
    source_files: Optional[list[str]] = None,
    grader_model: Optional[str] = None,
    poll_interval_seconds: float = 3.0,
    max_wait_seconds: int = 900,
) -> dict[str, Any]:
    """
    Evaluate generated questions using OpenAI Evals and save evaluation_report.json.
    """
    evaluation_outputs_dir = outputs_dir / EVALUATION_OUTPUTS_DIRNAME
    evaluation_outputs_dir.mkdir(parents=True, exist_ok=True)
    report_path = evaluation_outputs_dir / EVALUATION_REPORT_FILENAME
    dataset_path = evaluation_outputs_dir / EVALUATION_DATASET_FILENAME
    output_items_path = evaluation_outputs_dir / EVALUATION_ITEMS_FILENAME
    spreadsheet_path = evaluation_outputs_dir / EVALUATION_SPREADSHEET_FILENAME
    rubric_snapshot_path = evaluation_outputs_dir / EVALUATION_RUBRIC_FILENAME
    grader_model = grader_model or RUBRIC_MODEL
    latex_document_check = _resolve_latex_check(latex_path)
    rubric_file = _write_rubric_snapshot(rubric_snapshot_path) or ""
    source_files = source_files or []

    generated_questions = final_result.get("generated_questions", [])
    if not generated_questions:
        report = _build_base_report(
            status="skipped",
            reason="No generated questions to evaluate",
            grader_model=grader_model,
            dataset_path=None,
            items_evaluated=0,
            latex_document_check=latex_document_check,
        )
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

    if not hasattr(client, "evals"):
        report = _build_base_report(
            status="skipped",
            reason="OpenAI client has no evals API surface",
            grader_model=grader_model,
            dataset_path=None,
            items_evaluated=0,
            latex_document_check=latex_document_check,
        )
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

    items: list[dict[str, Any]] = []
    for generated in generated_questions:
        source = source_by_id.get(generated.get("source_id"))
        if source is None:
            continue
        items.append(
            {
                "source_id": str(generated.get("source_id", "")),
                "source_question_text": _render_source_question_text(source),
                "generated_question_text": _render_generated_question_text(generated),
            }
        )

    if not items:
        report = _build_base_report(
            status="skipped",
            reason="No source/generated pairs available for evaluation",
            grader_model=grader_model,
            dataset_path=None,
            items_evaluated=0,
            latex_document_check=latex_document_check,
        )
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

        if run_status == "completed":
            output_items = _collect_output_items(client, eval_obj.id, current.id)
            if output_items:
                _write_output_items_jsonl(
                    output_items_path,
                    output_items,
                )
                output_item_records = [
                    _to_jsonable(output_item) for output_item in output_items
                ]
            criterion_scores, aggregation_notes = _aggregate_question_scores(output_items)

        _write_evaluation_spreadsheet(
            spreadsheet_path,
            dataset_items=items,
            output_items=output_item_records,
            threshold=PASSING_SCORE_THRESHOLD,
        )

        overall_score = _compute_overall_score(criterion_scores, latex_document_check)
        if report_status == "completed":
            report_status = _resolve_score_status(overall_score)
        report = {
            "status": report_status,
            "created_at": _utc_timestamp(),
            "rubric": RUBRIC_CRITERIA,
            "items_evaluated": len(items),
            "grader_model": grader_model,
            "dataset_file": _evaluation_relative_path(dataset_path),
            "criterion_scores": criterion_scores,
            "latex_document_check": latex_document_check,
            "overall_score": overall_score,
            "passing_score_threshold": PASSING_SCORE_THRESHOLD,
            "eval_data_file_id": eval_data_file.id,
            "eval_id": eval_obj.id,
            "run_id": current.id,
            "run_status": run_status,
            "output_items_count": len(output_items),
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
        report = {
            "status": "failed",
            "created_at": _utc_timestamp(),
            "rubric": RUBRIC_CRITERIA,
            "items_evaluated": len(items),
            "grader_model": grader_model,
            "dataset_file": _evaluation_relative_path(dataset_path),
            "criterion_scores": {},
            "latex_document_check": latex_document_check,
            "overall_score": _compute_overall_score({}, latex_document_check),
            "passing_score_threshold": PASSING_SCORE_THRESHOLD,
            "error": str(exc),
            "traceback": traceback.format_exc(),
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
