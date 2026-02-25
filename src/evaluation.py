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
from datetime import datetime
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


def _load_rubric_criteria() -> list[dict[str, str]]:
    if RUBRIC_PATH.exists():
        try:
            data = json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data
            logger.warning("Rubric file %s is empty or not a list; using defaults", RUBRIC_PATH)
        except Exception as exc:
            logger.warning("Failed to load rubric from %s: %s; using defaults", RUBRIC_PATH, exc)
    return DEFAULT_RUBRIC_CRITERIA


RUBRIC_CRITERIA = _load_rubric_criteria()


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


def _build_testing_criteria(model: str = "o4-mini") -> list[dict[str, Any]]:
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


def run_openai_output_evaluation(
    client: OpenAI,
    final_result: dict[str, Any],
    outputs_dir: Path,
    latex_path: Optional[Path] = None,
    grader_model: str = "o4-mini",
    poll_interval_seconds: float = 3.0,
    max_wait_seconds: int = 900,
) -> dict[str, Any]:
    """
    Evaluate generated questions using OpenAI Evals and save evaluation_report.json.
    """
    report_path = outputs_dir / "evaluation_report.json"

    generated_questions = final_result.get("generated_questions", [])
    if not generated_questions:
        report = {
            "status": "skipped",
            "reason": "No generated questions to evaluate",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "rubric": RUBRIC_CRITERIA,
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    if not hasattr(client, "evals"):
        report = {
            "status": "skipped",
            "reason": "OpenAI client has no evals API surface",
            "created_at": datetime.utcnow().isoformat() + "Z",
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
            "created_at": datetime.utcnow().isoformat() + "Z",
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
            data_source={
                "type": "completions",
                "source": {"type": "file_id", "id": eval_data_file.id},
            },
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

        report: dict[str, Any] = {
            "status": "completed",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "rubric": RUBRIC_CRITERIA,
            "items_evaluated": len(items),
            "grader_model": grader_model,
            "dataset_file": str(dataset_path.name),
            "eval_data_file_id": eval_data_file.id,
            "eval_id": eval_obj.id,
            "run_id": current.id,
            "run_status": getattr(current, "status", "unknown"),
            "run": current.model_dump() if hasattr(current, "model_dump") else str(current),
        }

        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    except Exception as exc:
        report = {
            "status": "failed",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "rubric": RUBRIC_CRITERIA,
            "items_evaluated": len(items),
            "grader_model": grader_model,
            "dataset_file": str(dataset_path.name),
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
