"""Tests for evaluation aggregation: NA reporting, LaTeX log scanning."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import evaluation  # noqa: E402


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


def test_latex_log_scan_detects_errors(tmp_path: Path):
    log = tmp_path / "doc.log"
    log.write_text("This is fine\n! LaTeX Error: Something broke\nmore\n")
    errors = evaluation._scan_latex_log(log)
    assert errors and "LaTeX Error" in errors[0]


def test_latex_log_scan_clean(tmp_path: Path):
    log = tmp_path / "doc.log"
    log.write_text("All good here\nnothing fatal\n")
    assert evaluation._scan_latex_log(log) == []


def test_resolve_latex_check_fails_on_log_errors(tmp_path: Path):
    tex = tmp_path / "doc.tex"
    pdf = tmp_path / "doc.pdf"
    log = tmp_path / "doc.log"
    tex.write_text("\\documentclass{article}\\begin{document}\\end{document}")
    pdf.write_bytes(b"%PDF-1.4 fake")
    log.write_text("! LaTeX Error: undefined\n")
    record = evaluation._resolve_latex_check(tex, pdf)
    assert record["status"] == "failed"
    assert record["latex_log_errors"]


def test_resolve_latex_check_passes_when_clean(tmp_path: Path):
    tex = tmp_path / "doc.tex"
    pdf = tmp_path / "doc.pdf"
    log = tmp_path / "doc.log"
    tex.write_text("x")
    pdf.write_bytes(b"x")
    log.write_text("no errors here")
    record = evaluation._resolve_latex_check(tex, pdf)
    assert record["status"] == "passed"
    assert "latex_log_errors" not in record
