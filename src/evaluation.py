"""
OpenAI Evals integration for generated isomorphic question outputs.

This module evaluates final workflow data against rubric criteria and writes
an evaluation report to the run output directory.
"""

from __future__ import annotations

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


DEFAULT_RUBRIC_CRITERIA: list[dict[str, str]] = [
    {
        "name": "preserve_physical_content",
        "category": "Preservation",
        "question": "Does the generated question preserve the original physical content?",
    },
    {
        "name": "scenario_variation",
        "category": "Variation",
        "question": "Does the generated question use a scenario different from the original one?",
    },
    {
        "name": "numerical_variation",
        "category": "Variation",
        "question": "If the original question is numerical, does the generated question appropriately modify numerical values?",
    },
    {
        "name": "physics_correctness",
        "category": "Quality control",
        "question": "Is the generated question free of physics errors or misconceptions?",
    },
    {
        "name": "numeric_reasonableness",
        "category": "Quality control",
        "question": "Do all numerical values have physically reasonable magnitudes and units?",
    },
    {
        "name": "distractor_structure_preservation",
        "category": "Preservation",
        "question": "Does the generated question preserve the logical structure of the distractors?",
    },
    {
        "name": "clerical_quality",
        "category": "Quality control",
        "question": "Is the generated question free of clerical errors (e.g., typos, missing symbols, ambiguous wording)?",
    },
    {
        "name": "latex_syntax_quality",
        "category": "Quality control",
        "question": "Is the generated LaTeX code free of syntax errors?",
    },
    {
        "name": "solvable_and_specified",
        "category": "Quality control",
        "question": "Is the generated question fully specified and solvable as written?",
    },
]


def _load_rubric_config() -> dict[str, Any]:
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


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_jsonl(items: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps({"item": item}, ensure_ascii=False) for item in items)


def _build_item_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "source_question": {"type": "object"},
            "generated_question": {"type": "object"},
            "latex_document": {"type": "string"},
        },
        "required": ["source_question", "generated_question", "latex_document"],
        "additionalProperties": False,
    }


def _build_testing_criteria(model: str = RUBRIC_MODEL) -> list[dict[str, Any]]:
    criteria: list[dict[str, Any]] = []
    for criterion in RUBRIC_CRITERIA:
        criteria.append(
            {
                "name": criterion["name"],
                "type": "score_model",
                "model": model,
                "range": [0, 1],
                "input": [
                    {
                        "role": "system",
                        "content": (
                            "You are grading one generated isomorphic physics MCQ against its source. "
                            "Return a strict binary score: 1 if criterion is satisfied, else 0. "
                            f"Criterion: {criterion['question']} "
                            "Use only provided item content. Be strict and conservative."
                        ),
                    }
                ],
            }
        )
    return criteria


def _build_run_data_source(eval_data_file_id: str) -> dict[str, Any]:
    return {
        "type": "jsonl",
        "source": {"type": "file_id", "id": eval_data_file_id},
    }


def run_openai_output_evaluation(
    client: OpenAI,
    final_result: dict[str, Any],
    outputs_dir: Path,
    latex_path: Optional[Path] = None,
    grader_model: Optional[str] = None,
    poll_interval_seconds: float = 3.0,
    max_wait_seconds: int = 900,
) -> dict[str, Any]:
    """
    Evaluate generated questions using OpenAI Evals and save evaluation_report.json.
    """
    report_path = outputs_dir / "evaluation_report.json"
    grader_model = grader_model or RUBRIC_MODEL

    generated_questions = final_result.get("generated_questions", [])
    if not generated_questions:
        report = {
            "status": "skipped",
            "reason": "No generated questions to evaluate",
            "created_at": _utc_timestamp(),
            "rubric": RUBRIC_CRITERIA,
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    if not hasattr(client, "evals"):
        report = {
            "status": "skipped",
            "reason": "OpenAI client has no evals API surface",
            "created_at": _utc_timestamp(),
            "rubric": RUBRIC_CRITERIA,
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    source_candidates = (
        final_result.get("verified_questions")
        or final_result.get("source_questions")
        or []
    )
    source_by_id = {q.get("source_id"): q for q in source_candidates if q.get("source_id")}
    latex_content = ""
    if latex_path and latex_path.exists():
        latex_content = latex_path.read_text(encoding="utf-8")

    items: list[dict[str, Any]] = []
    for generated in generated_questions:
        source = source_by_id.get(generated.get("source_id"))
        if source is None:
            continue
        items.append(
            {
                "source_question": source,
                "generated_question": generated,
                "latex_document": latex_content,
            }
        )

    if not items:
        report = {
            "status": "skipped",
            "reason": "No source/generated pairs available for evaluation",
            "created_at": _utc_timestamp(),
            "rubric": RUBRIC_CRITERIA,
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    dataset_path = outputs_dir / "evaluation_dataset.jsonl"
    dataset_path.write_text(_to_jsonl(items), encoding="utf-8")

    try:
        with open(dataset_path, "rb") as f:
            eval_data_file = client.files.create(file=f, purpose="evals")

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

        report: dict[str, Any] = {
            "status": report_status,
            "created_at": _utc_timestamp(),
            "rubric": RUBRIC_CRITERIA,
            "items_evaluated": len(items),
            "grader_model": grader_model,
            "dataset_file": str(dataset_path.name),
            "eval_data_file_id": eval_data_file.id,
            "eval_id": eval_obj.id,
            "run_id": current.id,
            "run_status": run_status,
            "run": current.model_dump() if hasattr(current, "model_dump") else str(current),
        }

        if report_status == "incomplete":
            report["reason"] = (
                f"Eval run did not reach a terminal state within {max_wait_seconds} seconds"
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
            "dataset_file": str(dataset_path.name),
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
