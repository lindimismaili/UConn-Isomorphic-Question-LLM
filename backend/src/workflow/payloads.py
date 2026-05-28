"""Shared payload builders for API and CLI workflow orchestration."""

from __future__ import annotations

from typing import Any

try:
    from .schemas import ExtracterOutput, GeneratorOutput, NormalizerOutput, VerifierOutput
except ImportError:
    from schemas import ExtracterOutput, GeneratorOutput, NormalizerOutput, VerifierOutput


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

