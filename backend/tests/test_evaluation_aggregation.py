"""Tests for evaluation aggregation: NA reporting."""

from __future__ import annotations

from types import SimpleNamespace

from backend.src import evaluation


def _result(name: str, score: float) -> SimpleNamespace:
    return SimpleNamespace(name=name, score=score)


def _item(results: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(results=results)


def test_aggregate_reports_na_count():
    items = [
        _item([_result("numerical_variation", -1.0), _result("preserve_physical_content", 1.0)]),
        _item([_result("numerical_variation", -1.0), _result("preserve_physical_content", 0.0)]),
        _item([_result("numerical_variation", 1.0), _result("preserve_physical_content", 1.0)]),
    ]
    summary, _ = evaluation._aggregate_question_scores(items)
    num = summary["numerical_variation"]
    assert num["na_count"] == 2
    assert num["items_scored"] == 1
    assert num["score"] == 1.0
    assert num["na_rate"] == round(2 / 3, 4)
    ppc = summary["preserve_physical_content"]
    assert ppc["na_count"] == 0
    assert ppc["items_scored"] == 3


def test_aggregate_all_na_criterion():
    items = [_item([_result("numerical_variation", -1.0)]) for _ in range(3)]
    summary, missing = evaluation._aggregate_question_scores(items)
    num = summary["numerical_variation"]
    assert num["na_count"] == 3
    assert num["items_scored"] == 0
    assert num["score"] is None
    # An all-NA criterion is not "missing data" — it's a complete N/A result.
    assert "numerical_variation" not in missing
