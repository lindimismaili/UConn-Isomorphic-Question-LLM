"""Tests for deterministic distractor_logic preservation in generator output."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline_validation import (  # noqa: E402
    validate_distractor_logic_preservation,
    validate_generator_output,
)
from schemas import (  # noqa: E402
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
