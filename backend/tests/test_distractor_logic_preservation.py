"""Tests for deterministic distractor_logic preservation in generator output."""

from __future__ import annotations

from backend.src.pipeline_validation import (
    validate_distractor_logic_preservation,
    validate_generator_output,
)
from backend.src.schemas import (
    GeneratorOutput,
    NewQuestionItem,
    OptionItem,
    SourceQuestionItem,
)


def _source_mc(source_id: str, labels: list[str]) -> SourceQuestionItem:
    opts = [
        OptionItem(
            key=chr(ord("A") + i),
            text=f"opt {i}",
            is_correct=(i == 0),
            explanation="x",
            distractor_logic=label,
        )
        for i, label in enumerate(labels)
    ]
    return SourceQuestionItem(
        source_id=source_id,
        source_raw="...",
        stem="stem",
        options=opts,
        has_explicit_answer=True,
        question_type="multiple_choice",
        topic_tags=[],
    )


def _generated_mc(source_id: str, labels: list[str]) -> NewQuestionItem:
    opts = [
        OptionItem(
            key=chr(ord("A") + i),
            text=f"g{i}",
            is_correct=(i == 0),
            explanation="x",
            distractor_logic=label,
        )
        for i, label in enumerate(labels)
    ]
    return NewQuestionItem(
        id=1,
        source_id=source_id,
        stem="g-stem",
        options=opts,
        topic_tags=[],
        generation_notes="",
        question_type="multiple_choice",
    )


def test_all_labels_preserved_passes():
    src = _source_mc("1", ["correct", "uses mass instead of weight", "sign flip", "unit error"])
    gen = GeneratorOutput(isomorphic_questions=[
        _generated_mc("1", ["correct", "uses mass instead of weight", "sign flip", "unit error"]),
    ])
    report = validate_distractor_logic_preservation([src], gen)
    assert report.valid, report.errors


def test_missing_label_fails():
    src = _source_mc("1", ["correct", "uses mass instead of weight", "sign flip", "unit error"])
    gen = GeneratorOutput(isomorphic_questions=[
        _generated_mc("1", ["correct", "uses mass instead of weight", "wrong-label", "unit error"]),
    ])
    report = validate_distractor_logic_preservation([src], gen)
    assert not report.valid
    assert any("sign flip" in e for e in report.errors)


def test_generic_label_not_required():
    src = _source_mc("1", ["correct", "generic_plausible_value"])
    gen = GeneratorOutput(isomorphic_questions=[_generated_mc("1", ["correct", "anything-goes"])])
    report = validate_distractor_logic_preservation([src], gen)
    assert report.valid


def test_validate_generator_output_includes_distractor_check():
    src = _source_mc("1", ["correct", "specific misconception"])
    gen = GeneratorOutput(isomorphic_questions=[_generated_mc("1", ["correct", "generic"])])
    report = validate_generator_output([src], gen, per_source=1)
    assert not report.valid
    assert any("distractor_logic" in e for e in report.errors)


def test_figure_items_skip_distractor_check():
    src = _source_mc("1", ["correct", "specific misconception"])
    src.has_figure = True
    gen = GeneratorOutput(isomorphic_questions=[])  # figures may be skipped placeholders
    report = validate_distractor_logic_preservation([src], gen)
    assert report.valid


def test_renumbered_label_preserved():
    # Same misconception, different numbers (the generator is told to vary
    # numbers): wording differs only inside the LaTeX math, so it must pass.
    src = _source_mc("1", [
        "correct",
        r"compute \(\delta x=5.03-0.33\) and multiply by \(f_x=3.04\)",
    ])
    gen = GeneratorOutput(isomorphic_questions=[_generated_mc("1", [
        "correct",
        r"compute \(\delta x=7.2-1.1\) and multiply by \(f_x=4.5\)",
    ])])
    report = validate_distractor_logic_preservation([src], gen)
    assert report.valid, report.errors


def test_reworded_same_logic_preserved():
    # Light rewording of the same misconception still shares enough tokens.
    src = _source_mc("1", ["correct", "uses mass instead of weight"])
    gen = GeneratorOutput(isomorphic_questions=[
        _generated_mc("1", ["correct", "uses the mass instead of the weight here"]),
    ])
    report = validate_distractor_logic_preservation([src], gen)
    assert report.valid, report.errors


def test_different_misconception_still_fails():
    # A genuinely different error type must still be flagged as not preserved.
    src = _source_mc("1", ["correct", "forgets to convert units"])
    gen = GeneratorOutput(isomorphic_questions=[
        _generated_mc("1", ["correct", "flips the sign of the acceleration"]),
    ])
    report = validate_distractor_logic_preservation([src], gen)
    assert not report.valid
    assert any("forgets to convert units" in e for e in report.errors)


def test_pure_math_label_not_enforced():
    # A label that is only math/numbers has no checkable logic signature, so it
    # must not block (can't deterministically compare a bare expression).
    src = _source_mc("1", ["correct", r"\(\delta x = 5.03 - 0.33\)"])
    gen = GeneratorOutput(isomorphic_questions=[
        _generated_mc("1", ["correct", r"\(q = 9.9\)"]),
    ])
    report = validate_distractor_logic_preservation([src], gen)
    assert report.valid, report.errors
