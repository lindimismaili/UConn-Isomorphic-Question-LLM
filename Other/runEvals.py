#!/usr/bin/env python3
"""Run eval-only reports for existing Phase_1 PDFs.

This script intentionally does not regenerate questions or LaTeX. It:

1. Extracts source questions from Other/physics_1_set.pdf with the existing
   Extracter agent.
2. Extracts generated questions from each PDF in Other/Phase_1.
3. Pairs generated questions to source questions by question order.
4. Runs the existing OpenAI eval integration once per Phase_1 PDF.
5. Writes a timestamped report folder inside Other.

Run from the repo root:

    python Other/runEvals.py

Optional examples:

    python Other/runEvals.py --limit 2
    python Other/runEvals.py --phase-pdf Other/Phase_1/0121-1.pdf
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve()
OTHER_DIR = SCRIPT_PATH.parent
REPO_ROOT = OTHER_DIR.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents import RunConfig, Runner, trace  # noqa: E402

from backend.src.evaluation import (  # noqa: E402
    EVALUATION_OUTPUTS_DIRNAME,
    EVALUATION_REPORT_FILENAME,
    run_openai_output_evaluation,
)
from backend.src.helpers import build_json_message, upload_file_to_openai  # noqa: E402
from backend.src.main import get_cli_openai_client  # noqa: E402
from backend.src.schemas import ExtracterOutput, NormalizerOutput, SourceQuestionItem  # noqa: E402
from backend.src.safety import safe_stem  # noqa: E402
from backend.src.server import create_agents  # noqa: E402


DEFAULT_SOURCE_PDF = OTHER_DIR / "physics_1_set.pdf"
DEFAULT_PHASE_DIR = OTHER_DIR / "Phase_1"
DEFAULT_OUTPUT_PREFIX = "other-phase1-eval-batch"


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )


def timestamp_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def create_output_root(other_dir: Path, prefix: str) -> Path:
    base = other_dir / f"{timestamp_label()}-{prefix}"
    candidate = base
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = other_dir / f"{base.name}-{suffix}"
    candidate.mkdir(parents=True)
    return candidate


def list_phase_pdfs(phase_dir: Path, explicit_pdfs: list[Path], limit: int | None) -> list[Path]:
    if explicit_pdfs:
        pdfs = [path.expanduser().resolve() for path in explicit_pdfs]
    else:
        pdfs = sorted(phase_dir.expanduser().resolve().glob("*.pdf"))
    if limit is not None:
        pdfs = pdfs[:limit]
    return pdfs


def make_extraction_config() -> NormalizerOutput:
    return NormalizerOutput(
        new_questions_per_question=1,
        difficulty="",
        theme="",
        title="",
        notes="Evaluation-only extraction; do not generate or rewrite questions.",
    )


def make_eval_config(source_extraction: ExtracterOutput) -> NormalizerOutput:
    return NormalizerOutput(
        new_questions_per_question=1,
        difficulty="",
        theme=source_extraction.theme or "",
        title=source_extraction.title or "Physics Phase 1 Evaluation",
        notes=(
            "Evaluation-only run over existing PDFs; no generation or LaTeX "
            "regeneration performed."
        ),
    )


def extraction_payload(pdf_path: Path, config: NormalizerOutput, *, generated_pdf: bool) -> dict[str, Any]:
    source_kind = "generated questions" if generated_pdf else "source questions"
    return {
        "task": "extract_source_questions",
        "files": [pdf_path.name],
        "normalizer_config": config.model_dump(),
        "requirements": {
            "purpose": f"Extract {source_kind} exactly as written for evaluation.",
            "preserve_math_latex": True,
            "extract_metadata": ["title", "theme", "author", "date", "course"],
            "determine_title_from_source": True,
            "determine_theme_from_extracted_information": True,
            "must_include_multiple_choice": True,
            "multiple_choice_option_keys": ["A", "B", "C", "D"],
            "set_has_explicit_answer_only_if_marked": True,
            "preserve_question_order": True,
            "do_not_generate_new_questions": True,
            "do_not_rewrite_or_simplify_questions": True,
            "include_explanations_when_present": True,
            "difficulty_rule": (
                "leave question difficulty empty unless normalizer_config.difficulty "
                "is non-empty"
            ),
        },
    }


async def extract_questions(
    *,
    client: Any,
    pdf_path: Path,
    run_config: RunConfig,
    generated_pdf: bool = False,
    extract_model: str | None = None,
) -> ExtracterOutput:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    file_id = upload_file_to_openai(client, pdf_path, logger=SimpleLogger())
    agents = create_agents(file_ids=[file_id])
    if extract_model:
        agents["extracter"].model = extract_model
    config = make_extraction_config()
    payload = extraction_payload(pdf_path, config, generated_pdf=generated_pdf)
    file_refs = [{"type": "input_file", "file_id": file_id}]
    extracter_input = build_json_message(payload, file_refs=file_refs)

    result = await Runner.run(
        agents["extracter"],
        input=extracter_input,
        run_config=run_config,
    )
    extraction: ExtracterOutput = result.final_output
    if not extraction.questions:
        retry_payload = {
            **payload,
            "task": "extract_source_questions_retry",
            "reason": "previous_result_had_zero_questions",
            "requirements": {
                **payload["requirements"],
                "must_return_all_questions": True,
                "if_pdf_text_is_fragmented_reconstruct_from_reading_order": True,
            },
        }
        retry_result = await Runner.run(
            agents["extracter"],
            input=build_json_message(retry_payload, file_refs=file_refs),
            run_config=run_config,
        )
        extraction = retry_result.final_output

    for question in extraction.questions:
        if question.options:
            question.question_type = "multiple_choice"
    if not extraction.questions:
        raise RuntimeError(f"Extracter returned zero questions for {pdf_path}")

    extraction.filename = extraction.filename or pdf_path.name
    return extraction


def new_question_from_extracted(
    question: SourceQuestionItem,
    *,
    source_id: str,
    index: int,
    pdf_name: str,
) -> dict[str, Any]:
    raw_question_text = question.source_raw.strip()
    use_raw_for_eval = bool(raw_question_text)
    generation_notes = f"Extracted from existing Phase_1 PDF: {pdf_name}"
    if use_raw_for_eval:
        generation_notes = (
            f"{generation_notes}. Eval text uses source_raw to avoid PDF line-wrap "
            "truncation in structured stem/option fields."
        )

    return {
        "id": index,
        "source_id": source_id,
        "stem": raw_question_text if use_raw_for_eval else question.stem,
        "options": [] if use_raw_for_eval else [option.model_dump() for option in question.options],
        "difficulty": question.difficulty,
        "theme": question.theme,
        "topic_tags": list(question.topic_tags),
        "generation_notes": generation_notes,
        "question_type": "open_ended" if use_raw_for_eval else question.question_type or "multiple_choice",
        "skipped": False,
        "skip_reason": "",
        "answer": question.answer,
        "parts": [] if use_raw_for_eval else [
            {
                "part_label": part.part_label,
                "stem": part.stem,
                "question_type": part.question_type,
                "options": [option.model_dump() for option in part.options],
                "answer": part.answer,
            }
            for part in question.parts
        ],
    }


def build_final_result(
    *,
    source_extraction: ExtracterOutput,
    generated_extraction: ExtracterOutput,
    phase_pdf: Path,
) -> dict[str, Any]:
    source_questions = source_extraction.questions
    generated_questions = []
    for index, generated in enumerate(generated_extraction.questions, start=1):
        source_id = (
            source_questions[index - 1].source_id
            if index - 1 < len(source_questions)
            else generated.source_id
            or str(index)
        )
        generated_questions.append(
            new_question_from_extracted(
                generated,
                source_id=str(source_id),
                index=index,
                pdf_name=phase_pdf.name,
            )
        )

    return {
        "config": make_eval_config(source_extraction).model_dump(),
        "source_questions": [question.model_dump() for question in source_questions],
        "verified_questions": [question.model_dump() for question in source_questions],
        "generated_questions": generated_questions,
    }


def recompute_question_only_score(report: dict[str, Any]) -> float | None:
    scores = [
        criterion.get("score")
        for criterion in (report.get("criterion_scores") or {}).values()
        if isinstance(criterion, dict) and isinstance(criterion.get("score"), (int, float))
    ]
    if not scores:
        return None
    return round(sum(float(score) for score in scores) / len(scores), 2)


def mark_latex_check_skipped(report: dict[str, Any], phase_pdf: Path) -> dict[str, Any]:
    report["latex_document_check"] = {
        "name": "latex_syntax_quality",
        "category": "Quality control",
        "question": "Is the generated LaTeX code free of syntax errors?",
        "score": None,
        "status": "skipped",
        "reason": (
            "Skipped for this evaluation-only run because Phase_1 contains existing "
            "PDFs, not regenerated .tex artifacts."
        ),
        "latex_file": "",
        "pdf_file": phase_pdf.name,
    }

    question_only_score = recompute_question_only_score(report)
    report["overall_score"] = question_only_score
    if (
        report.get("run_status") == "completed"
        and not report.get("error")
        and isinstance(question_only_score, (int, float))
    ):
        report["status"] = "complete"
    return report


def report_summary(
    *,
    phase_pdf: Path,
    output_dir: Path,
    generated_extraction: ExtracterOutput | None,
    report: dict[str, Any] | None,
    elapsed_seconds: float,
    error: str = "",
) -> dict[str, Any]:
    evaluation_dir = output_dir / EVALUATION_OUTPUTS_DIRNAME
    return {
        "file": str(phase_pdf),
        "output_folder": str(output_dir),
        "status": (report or {}).get("status", "failed" if error else "unknown"),
        "question_count": len(generated_extraction.questions) if generated_extraction else 0,
        "items_evaluated": (report or {}).get("items_evaluated", 0),
        "overall_score": (report or {}).get("overall_score"),
        "report_path": str(evaluation_dir / EVALUATION_REPORT_FILENAME),
        "error": error,
        "dataset_path": str(evaluation_dir / "evaluation_dataset.jsonl"),
        "spreadsheet_path": str(evaluation_dir / "evaluation_spreadsheet.csv"),
        "elapsed_seconds": round(elapsed_seconds, 2),
    }


async def run_one_phase_pdf(
    *,
    client: Any,
    source_pdf: Path,
    source_extraction: ExtracterOutput,
    phase_pdf: Path,
    output_root: Path,
    run_config: RunConfig,
    extract_model: str | None,
    eval_model: str | None,
) -> dict[str, Any]:
    started = time.time()
    output_dir = output_root / safe_stem(phase_pdf.stem, fallback="phase-pdf")
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        generated_extraction = await extract_questions(
            client=client,
            pdf_path=phase_pdf,
            run_config=run_config,
            generated_pdf=True,
            extract_model=extract_model,
        )
        write_json(output_dir / "generated_extraction.json", generated_extraction)

        final_result = build_final_result(
            source_extraction=source_extraction,
            generated_extraction=generated_extraction,
            phase_pdf=phase_pdf,
        )
        write_json(output_dir / "final_result.json", final_result)

        report = run_openai_output_evaluation(
            client=client,
            final_result=final_result,
            outputs_dir=output_dir,
            latex_path=None,
            pdf_path=phase_pdf,
            source_files=[str(source_pdf), str(phase_pdf)],
            grader_model=eval_model,
        )
        report = mark_latex_check_skipped(report, phase_pdf)
        report_path = output_dir / EVALUATION_OUTPUTS_DIRNAME / EVALUATION_REPORT_FILENAME
        write_json(report_path, report)

        elapsed = time.time() - started
        return report_summary(
            phase_pdf=phase_pdf,
            output_dir=output_dir,
            generated_extraction=generated_extraction,
            report=report,
            elapsed_seconds=elapsed,
        )
    except Exception as exc:
        elapsed = time.time() - started
        error_payload = {
            "status": "failed",
            "file": str(phase_pdf),
            "error": str(exc),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        write_json(output_dir / "error.json", error_payload)
        return report_summary(
            phase_pdf=phase_pdf,
            output_dir=output_dir,
            generated_extraction=None,
            report=None,
            elapsed_seconds=elapsed,
            error=str(exc),
        )


class SimpleLogger:
    def info(self, message: str, *args: Any) -> None:
        print(message % args if args else message)

    def warning(self, message: str, *args: Any) -> None:
        print(message % args if args else message, file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-pdf",
        type=Path,
        default=DEFAULT_SOURCE_PDF,
        help="Source PDF to extract original questions from.",
    )
    parser.add_argument(
        "--phase-dir",
        type=Path,
        default=DEFAULT_PHASE_DIR,
        help="Directory containing Phase_1 generated PDFs.",
    )
    parser.add_argument(
        "--phase-pdf",
        type=Path,
        action="append",
        default=[],
        help="Specific Phase_1 PDF to evaluate. May be passed more than once.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of Phase_1 PDFs evaluated.",
    )
    parser.add_argument(
        "--output-prefix",
        default=DEFAULT_OUTPUT_PREFIX,
        help="Suffix used for the timestamped output folder under Other.",
    )
    parser.add_argument(
        "--extract-model",
        default="gpt-5.5",
        help=(
            "Model used by the Extracter agent for this script only. Defaults to "
            "gpt-5.5 because gpt-5.5-mini is not available on this API account."
        ),
    )
    parser.add_argument(
        "--eval-model",
        default="gpt-5.5",
        help=(
            "OpenAI eval grader model for this script only. Defaults to gpt-5.5 "
            "because gpt-5.5-mini is not available on this API account."
        ),
    )
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    source_pdf = args.source_pdf.expanduser().resolve()
    phase_dir = args.phase_dir.expanduser().resolve()
    phase_pdfs = list_phase_pdfs(phase_dir, args.phase_pdf, args.limit)

    if not source_pdf.exists():
        raise FileNotFoundError(f"Source PDF not found: {source_pdf}")
    if not phase_pdfs:
        raise FileNotFoundError(f"No Phase_1 PDFs found in {phase_dir}")

    output_root = create_output_root(OTHER_DIR, args.output_prefix)
    client = get_cli_openai_client()
    run_config = RunConfig(
        trace_metadata={
            "__trace_source__": "other-run-evals",
            "source_pdf": str(source_pdf),
            "phase_pdf_count": len(phase_pdfs),
        }
    )

    print(f"Output root: {output_root}")
    print(f"Extracting source questions from {source_pdf}")

    with trace("Other Phase_1 eval-only batch"):
        source_extraction = await extract_questions(
            client=client,
            pdf_path=source_pdf,
            run_config=run_config,
            generated_pdf=False,
            extract_model=args.extract_model,
        )
        source_output_dir = output_root / "source_extraction"
        write_json(source_output_dir / "source_extraction.json", source_extraction)
        print(f"Extracted {len(source_extraction.questions)} source questions")

        reports: list[dict[str, Any]] = []
        for index, phase_pdf in enumerate(phase_pdfs, start=1):
            print(f"[{index}/{len(phase_pdfs)}] Evaluating {phase_pdf.name}")
            result = await run_one_phase_pdf(
                client=client,
                source_pdf=source_pdf,
                source_extraction=source_extraction,
                phase_pdf=phase_pdf,
                output_root=output_root,
                run_config=run_config,
                extract_model=args.extract_model,
                eval_model=args.eval_model,
            )
            reports.append(result)
            status = result.get("status", "unknown")
            score = result.get("overall_score")
            print(f"  {status}, score={score}, output={result['output_folder']}")

    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_pdf": str(source_pdf),
        "source_question_count": len(source_extraction.questions),
        "phase_pdf_count": len(phase_pdfs),
        "output_root": str(output_root),
        "reports": reports,
    }
    write_json(output_root / "summary.json", summary)
    print(f"Summary written to {output_root / 'summary.json'}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
