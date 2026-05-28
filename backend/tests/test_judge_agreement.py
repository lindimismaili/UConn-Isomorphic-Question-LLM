"""Tests for the judge ↔ professor agreement harness."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from backend.src.tools.judge_agreement import (  # noqa: E402
    cohen_kappa,
    compare_grids,
    load_xlsx_grids,
    write_disagreements_csv,
    write_report_json,
)
from openpyxl import Workbook  # noqa: E402

CRITERIA = [
    {"name": "preserve_physical_content", "question": "Q1?"},
    {"name": "scenario_variation", "question": "Q2?"},
    {"name": "numerical_variation", "question": "Q3?"},
]


def _write_grid(path: Path, sheets: dict[str, list[list]]) -> None:
    wb = Workbook()
    wb.remove(wb.active)
    for sheet_name, rows in sheets.items():
        ws = wb.create_sheet(sheet_name)
        for row in rows:
            ws.append(row)
    wb.save(path)


def test_cohen_kappa_perfect():
    assert cohen_kappa([(1, 1), (0, 0), (1, 1)]) == 1.0


def test_cohen_kappa_chance():
    pairs = [(1, 0), (0, 1), (1, 0), (0, 1)]
    # po=0, pe=0.5, kappa = -1
    assert cohen_kappa(pairs) == -1.0


def test_cohen_kappa_empty():
    assert cohen_kappa([]) is None


def test_load_grid_normalizes_cells(tmp_path: Path):
    p = tmp_path / "g.xlsx"
    _write_grid(p, {"PDF1": [
        ["PDF1", 1, 2, 3],
        ["preserve_physical_content", 1, 0, "na"],
        ["scenario_variation", "PASS", "FAIL", 1],
        ["numerical_variation", "NA", 1, 0],
    ]})
    grids = load_xlsx_grids(p, CRITERIA)
    g = grids["PDF1"]
    assert g[1]["preserve_physical_content"] == 1
    assert g[2]["preserve_physical_content"] == 0
    assert g[3]["preserve_physical_content"] == "na"
    assert g[1]["scenario_variation"] == 1
    assert g[2]["scenario_variation"] == 0
    assert g[1]["numerical_variation"] == "na"


def test_compare_grids_confusion_and_disagreements(tmp_path: Path):
    prof = tmp_path / "p.xlsx"
    judge = tmp_path / "j.xlsx"
    _write_grid(prof, {"PDF1": [
        ["PDF1", 1, 2, 3, 4],
        ["preserve_physical_content", 1, 1, 0, 0],
        ["scenario_variation", 1, 1, 1, 1],
        ["numerical_variation", "na", 1, 0, "na"],
    ]})
    _write_grid(judge, {"PDF1": [
        ["PDF1", 1, 2, 3, 4],
        ["preserve_physical_content", 1, 0, 0, 1],  # 1 inflate, 1 miss
        ["scenario_variation", 1, 1, 1, 1],
        ["numerical_variation", 1, 1, 0, "na"],  # idx 1 prof=na → excluded
    ]})
    prof_grids = load_xlsx_grids(prof, CRITERIA)
    judge_grids = load_xlsx_grids(judge, CRITERIA)
    per, disagreements = compare_grids(prof_grids, judge_grids, CRITERIA)

    p = per["preserve_physical_content"]
    assert p.n_compared == 4
    assert p.judge_only_pass == 1  # source_id=4, prof=0 judge=1
    assert p.judge_only_fail == 1  # source_id=2, prof=1 judge=0
    assert p.raw_agreement == 0.5
    assert p.kappa is not None

    s = per["scenario_variation"]
    assert s.n_compared == 4
    assert s.kappa in (1.0, None)  # all-1 case
    assert s.raw_agreement == 1.0

    # Numerical: prof=na for ids 1 and 4 → excluded; ids 2 (prof=1,judge=1), 3 (0,0)
    n = per["numerical_variation"]
    assert n.n_compared == 2
    assert n.n_na_either == 2

    pdfs = {d["pdf"] for d in disagreements}
    assert pdfs == {"PDF1"}
    assert any(d["direction"] == "judge_inflated" for d in disagreements)
    assert any(d["direction"] == "judge_missed" for d in disagreements)


def test_write_disagreements_csv_empty(tmp_path: Path):
    out = tmp_path / "d.csv"
    write_disagreements_csv(out, [])
    text = out.read_text()
    assert "pdf" in text and "criterion" in text


def test_write_report_json(tmp_path: Path):
    prof = tmp_path / "p.xlsx"
    judge = tmp_path / "j.xlsx"
    _write_grid(prof, {"PDF1": [
        ["PDF1", 1, 2],
        ["preserve_physical_content", 1, 0],
        ["scenario_variation", 1, 1],
        ["numerical_variation", 0, 1],
    ]})
    _write_grid(judge, {"PDF1": [
        ["PDF1", 1, 2],
        ["preserve_physical_content", 1, 0],
        ["scenario_variation", 0, 1],
        ["numerical_variation", 0, 1],
    ]})
    per, _ = compare_grids(load_xlsx_grids(prof, CRITERIA), load_xlsx_grids(judge, CRITERIA), CRITERIA)
    out = tmp_path / "rep.json"
    rep = write_report_json(out, per, professor_path=prof, judge_source=str(judge), pdfs_compared=["PDF1"])
    assert "summary" in rep
    assert "criteria" in rep
    assert out.exists()


@pytest.mark.parametrize("p,j,expected", [
    (1, 1, 1.0),  # perfect, single label → 1
    (0, 0, 1.0),
])
def test_cohen_kappa_single_label_perfect(p, j, expected):
    assert cohen_kappa([(p, j)]) == expected
