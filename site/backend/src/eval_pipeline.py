"""Evaluation pipeline helpers for batch eval runs (eval without full workflow)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agents import RunConfig, Runner

from backend.src.helpers import build_json_message, upload_file_to_openai
from backend.src.schemas import ExtracterOutput, NormalizerOutput, SourceQuestionItem
from backend.src.workflow import create_agents


def _make_extraction_config() -> NormalizerOutput:
    return NormalizerOutput(new_questions_per_question=1, notes="Evaluation-only extraction; do not generate or rewrite questions.")

def _extraction_payload(pdf_path: Path, config: NormalizerOutput, *, generated_pdf: bool) -> dict[str, Any]:
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
            "difficulty_rule": "leave question difficulty empty unless normalizer_config.difficulty is non-empty",
        },
    }

async def extract_questions_from_pdf(*, client, pdf_path: Path, run_config: RunConfig, generated_pdf: bool = False) -> ExtracterOutput:
    """Upload a PDF to OpenAI and extract questions using the Extracter agent."""
    # Upload synchronously in thread
    file_id = await asyncio.to_thread(upload_file_to_openai, client, pdf_path)
    agents = create_agents(file_ids=[file_id])
    config = _make_extraction_config()
    payload = _extraction_payload(pdf_path, config, generated_pdf=generated_pdf)
    file_refs = [{"type": "input_file", "file_id": file_id}]
    extracter_input = build_json_message(payload, file_refs=file_refs)
    result = await Runner.run(agents["extracter"], input=extracter_input, run_config=run_config)
    extraction: ExtracterOutput = result.final_output
    if not extraction.questions:
        retry_payload = {**payload, "task": "extract_source_questions_retry", "reason": "previous_result_had_zero_questions", "requirements": {**payload["requirements"], "must_return_all_questions": True}}
        retry_result = await Runner.run(agents["extracter"], input=build_json_message(retry_payload, file_refs=file_refs), run_config=run_config)
        extraction = retry_result.final_output
    for q in extraction.questions:
        if q.options:
            q.question_type = "multiple_choice"
    extraction.filename = extraction.filename or pdf_path.name
    return extraction

def question_to_generated_item(question: SourceQuestionItem, *, source_id: str, index: int, pdf_name: str) -> dict[str, Any]:
    raw = question.source_raw.strip()
    use_raw = bool(raw)
    return {
        "id": index, "source_id": source_id,
        "stem": raw if use_raw else question.stem,
        "options": [] if use_raw else [o.model_dump() for o in question.options],
        "difficulty": question.difficulty,
        "tags": list(question.tags),
        "generation_notes": f"Extracted from existing PDF: {pdf_name}" + (" (source_raw used)" if use_raw else ""),
        "question_type": "open_ended" if use_raw else (question.question_type or "multiple_choice"),
        "skipped": False, "skip_reason": "", "answer": question.answer,
        "parts": [] if use_raw else [{"part_label": p.part_label, "stem": p.stem, "question_type": p.question_type, "options": [o.model_dump() for o in p.options], "answer": p.answer} for p in question.parts],
    }

def build_eval_final_result(*, source_extraction: ExtracterOutput, generated_extraction: ExtracterOutput, generated_pdf: Path) -> dict[str, Any]:
    source_qs = source_extraction.questions
    generated_qs = []
    for i, gen in enumerate(generated_extraction.questions, start=1):
        sid = source_qs[i - 1].source_id if i - 1 < len(source_qs) else gen.source_id or str(i)
        generated_qs.append(question_to_generated_item(gen, source_id=str(sid), index=i, pdf_name=generated_pdf.name))
    config = NormalizerOutput(new_questions_per_question=1, theme=source_extraction.theme or "", title=source_extraction.title or "Evaluation Run", notes="Evaluation-only run.")
    return {"config": config.model_dump(), "source_questions": [q.model_dump() for q in source_qs], "verified_questions": [q.model_dump() for q in source_qs], "generated_questions": generated_qs}
