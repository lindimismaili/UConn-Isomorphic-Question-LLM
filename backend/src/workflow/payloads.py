"""Shared payload builders for API and CLI workflow orchestration."""

from __future__ import annotations

from typing import Any

from ..schemas import ExtracterOutput, GeneratorOutput, NormalizerOutput, VerifierOutput


def build_normalizer_input(prompt: str, file_names: list[str]) -> dict[str, Any]:
    """Canonical normalizer task payload."""
    return {
        "task": "normalize_user_request",
        "user_input": {
            "prompt": prompt or "",
            "files": list(file_names),
        },
        "defaults": {
            "new_questions_per_question": 1,
            "difficulty": "",
            "theme": "",
            "title": "",
        },
        "rules": {
            "difficulty": (
                "leave empty unless the user explicitly requests a difficulty target"
            ),
            "theme": (
                "leave empty unless the user requests a target generation theme; "
                "the extracter determines source theme"
            ),
            "title": (
                "leave empty unless the user requests an output title; "
                "the extracter determines source title"
            ),
        },
    }


def build_extracter_input(
    file_names: list[str],
    normalizer_config: NormalizerOutput,
) -> dict[str, Any]:
    """Canonical extracter task payload (first pass)."""
    return {
        "task": "extract_source_questions",
        "files": list(file_names),
        "normalizer_config": normalizer_config.model_dump(),
        "requirements": {
            "preserve_math_latex": True,
            "extract_metadata": ["title", "theme", "author", "date", "course"],
            "determine_title_from_source": True,
            "determine_theme_from_extracted_information": True,
            "must_include_multiple_choice": True,
            "multiple_choice_option_keys": ["A", "B", "C", "D"],
            "set_has_explicit_answer_only_if_marked": True,
            "difficulty_rule": (
                "leave question difficulty empty unless normalizer_config.difficulty "
                "is non-empty"
            ),
        },
    }


def build_extracter_retry_input(
    file_names: list[str],
    normalizer_config: NormalizerOutput,
) -> dict[str, Any]:
    """Canonical extracter retry payload after a zero-question first pass."""
    return {
        "task": "extract_source_questions_retry",
        "reason": "previous_result_had_zero_questions",
        "files": list(file_names),
        "normalizer_config": normalizer_config.model_dump(),
        "requirements": {
            "must_include_multiple_choice": True,
            "determine_title_from_source": True,
            "determine_theme_from_extracted_information": True,
            "difficulty_rule": (
                "leave question difficulty empty unless "
                "normalizer_config.difficulty is non-empty"
            ),
        },
    }


def build_verifier_input(extraction: ExtracterOutput) -> dict[str, Any]:
    """Canonical verifier task payload."""
    return {
        "task": "verify_source_questions",
        "source_questions": extraction.model_dump(),
        "requirements": {
            "preserve_all_questions": True,
            "if_answer_marked_keep_it": True,
            "if_unmarked_solve_and_mark_correct_option": True,
            "add_option_explanations": True,
            "use_code_interpreter_for_calculations": True,
            "solve_open_ended_and_provide_answer": True,
            "solve_multi_part_questions_per_part": True,
        },
    }


def build_verifier_repair_input(
    extraction: ExtracterOutput,
    repair_questions: list[Any],
    reason: str,
) -> dict[str, Any]:
    """Verifier repair payload covering only the failing source IDs."""
    base = build_verifier_input(extraction)
    base["task"] = "verify_source_questions_repair"
    base["reason"] = reason
    base["source_questions"] = {
        **extraction.model_dump(),
        "questions": [q.model_dump() for q in repair_questions],
    }
    return base


def generator_requirements(config: NormalizerOutput) -> dict[str, Any]:
    """Return the canonical generator requirements payload."""
    return {
        "new_questions_per_source": config.new_questions_per_question,
        "fully_original_questions": True,
        "same_underlying_concept": True,
        "vary_numerical_values_significantly": True,
        "physically_reasonable_values": True,
        "plausible_misconception_distractors": True,
        "detailed_correct_answer_explanations": True,
        "explain_each_distractor": True,
        "latex_math": True,
        "use_code_interpreter_for_verification": True,
        "skip_questions_with_figures": True,
        "generate_open_ended_for_open_ended_sources": True,
        "open_ended_questions_have_no_options": True,
        "generate_answers_for_all_open_ended": True,
        "generate_multi_part_for_multi_part_sources": True,
        "preserve_source_style_and_verbosity": True,
        "keep_verbose_open_ended_sources_verbose": True,
        "do_not_compress_narrative_stems_into_short_directives": True,
        "do_not_add_padding_to_concise_sources": True,
        "self_review_style_before_return": True,
    }


def build_generator_repair_input(
    config: NormalizerOutput,
    verifier_output: VerifierOutput,
    generator_output: GeneratorOutput,
    repair_questions: list[Any],
    reason: str,
) -> dict[str, Any]:
    """Generator repair payload covering only the failing source IDs."""
    return {
        "task": "repair_generator_contract_violations",
        "reason": reason,
        "configuration": config.model_dump(),
        "source_questions": {
            "questions": [q.model_dump() for q in repair_questions],
        },
        "draft_isomorphic_questions": generator_output.model_dump(),
        "requirements": {
            **generator_requirements(config),
            "preserve_ids_and_source_ids": True,
            "return_complete_final_set": True,
        },
    }


def build_generator_input(config: NormalizerOutput, verifier_output: VerifierOutput) -> dict[str, Any]:
    """Build the canonical generator input payload."""
    return {
        "task": "generate_isomorphic_questions",
        "configuration": config.model_dump(),
        "source_questions": verifier_output.model_dump(),
        "requirements": generator_requirements(config),
    }


def build_style_review_input(
    config: NormalizerOutput,
    verifier_output: VerifierOutput,
    generator_output: GeneratorOutput,
) -> dict[str, Any]:
    """Build the generator style-review pass payload."""
    return {
        "task": "review_and_revise_isomorphic_questions_for_style",
        "configuration": config.model_dump(),
        "source_questions": verifier_output.model_dump(),
        "draft_isomorphic_questions": generator_output.model_dump(),
        "requirements": {
            **generator_requirements(config),
            "preserve_ids_and_source_ids": True,
            "return_complete_final_set": True,
            "revise_only_when_needed": True,
            "review_focus": [
                "Preserve the source question's verbosity and rhetorical style.",
                "Keep verbose open-ended questions verbose and explanation-oriented.",
                "Do not compress narrative or multi-sentence source stems into short directives.",
                "Do not add narrative padding to concise sources.",
                "Preserve part structure and skip placeholders exactly.",
            ],
        },
    }


def build_latex_questions(generator_output: GeneratorOutput) -> list[dict[str, Any]]:
    """Build LaTeX question payload items from generated output."""
    return [
        {
            "source_id": q.source_id,
            "stem": q.stem,
            "options": [opt.model_dump() for opt in q.options],
            "question_type": q.question_type,
            "skipped": q.skipped,
            "skip_reason": q.skip_reason,
            "answer": q.answer,
            "parts": [part.model_dump() for part in q.parts],
        }
        for q in generator_output.isomorphic_questions
    ]


def build_latex_input(
    config: NormalizerOutput,
    extraction: ExtracterOutput,
    generator_output: GeneratorOutput,
) -> dict[str, Any]:
    """Build the canonical LaTeX input payload."""
    return {
        "task": "format_questions_to_latex",
        "document": {
            "title": config.title or "Isomorphic Physics Questions",
            "theme": config.theme,
            "source_title": extraction.title,
            "course": extraction.course,
        },
        "questions": build_latex_questions(generator_output),
        "requirements": {
            "include_document_preamble": True,
            "include_macros": [
                "\\QBlock",
                "\\Correct",
                "\\Wrong",
                "\\OEBlock",
                "\\MPBlock",
                "\\MCPart",
                "\\OEPart",
                "\\SkipBlock",
            ],
            "format_all_questions_with_macros": True,
            "proper_math_formatting": True,
            "filename": "isomorphic_questions.tex",
        },
    }


def build_evaluation_final_result(
    config: NormalizerOutput,
    extraction: ExtracterOutput,
    verification: VerifierOutput,
    generation: GeneratorOutput,
) -> dict[str, Any]:
    """Build the canonical final-result context used by evaluation."""
    return {
        "config": config.model_dump(),
        "source_questions": [q.model_dump() for q in extraction.questions],
        "verified_questions": [q.model_dump() for q in verification.questions],
        "generated_questions": [q.model_dump() for q in generation.isomorphic_questions],
    }

