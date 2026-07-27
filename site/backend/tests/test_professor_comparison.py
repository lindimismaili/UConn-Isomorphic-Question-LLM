"""Tests for the ported professor-comparison tooling (module + API route)."""

from __future__ import annotations

import csv
import shutil
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import Workbook, load_workbook  # noqa: E402

from backend.src.api import state  # noqa: E402
from backend.src.evaluation import rubric_criteria  # noqa: E402
from backend.src.paths import OUTPUTS_DIR  # noqa: E402
from backend.src.tools.professor_comparison import (  # noqa: E402
    ProfessorComparisonError,
    create_xlsx,
    load_pdf_grid,
    parse_cell,
    run_professor_comparison,
    source_classification,
)

CRITERIA_NAMES = [c["name"] for c in rubric_criteria() if c.get("name")]


def _write_entry_csv(entry_dir: Path, rows: list[dict[str, object]]) -> None:
    """Write an evaluation_spreadsheet.csv for one batch entry.

    ``rows`` maps criterion name -> 0/1/'na' plus a ``source_id``.
    """
    eval_dir = entry_dir / "evaluation_outputs"
    eval_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = ["source_id"]
    for name in CRITERIA_NAMES:
        fieldnames += [f"{name}_score", f"{name}_passed"]
    with (eval_dir / "evaluation_spreadsheet.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            record: dict[str, object] = {"source_id": row["source_id"]}
            for name in CRITERIA_NAMES:
                value = row.get(name, 1)
                if value == "na":
                    record[f"{name}_score"] = "NA"
                    record[f"{name}_passed"] = "NA"
                else:
                    record[f"{name}_score"] = value
                    record[f"{name}_passed"] = "TRUE" if value == 1 else "FALSE"
            writer.writerow(record)


def _make_batch(batch_dir: Path, entry_names: list[str]) -> None:
    """Create a batch dir with all-pass entries for source ids 1..3."""
    for entry in entry_names:
        _write_entry_csv(
            batch_dir / entry,
            [{"source_id": sid} for sid in (1, 2, 3)],
        )


def _write_professor_workbook(path: Path, sheet_names: list[str]) -> None:
    """Professor grid matching the fixture batch: all-pass except one cell."""
    wb = Workbook()
    wb.remove(wb.active)
    for sheet in sheet_names:
        ws = wb.create_sheet(sheet)
        ws.append([sheet, 1, 2, 3])
        for idx, name in enumerate(CRITERIA_NAMES):
            # One disagreement: first criterion fails source 2 in every sheet.
            row = [name, 1, 0 if idx == 0 else 1, 1]
            ws.append(row)
    wb.save(path)


class TestSourceClassification:
    def test_known_stem(self):
        classes = source_classification("physics_1_set")
        assert classes["conceptual"] == frozenset({1, 3, 7, 10})
        assert classes["abstract"] == frozenset({5, 6, 9, 11})
        assert classes["numerical"] == frozenset({2, 4, 8, 12, 13})

    def test_unknown_or_blank_stem_degrades_to_empty(self):
        for stem in ("", "unknown_set"):
            classes = source_classification(stem)
            assert all(ids == frozenset() for ids in classes.values())


class TestCreateXlsx:
    def test_builds_summary_and_per_entry_sheets(self, tmp_path: Path):
        batch = tmp_path / "batch"
        _make_batch(batch, ["entry-1", "entry-2"])
        out = create_xlsx(batch)
        assert out == batch / "XLSX" / "compiled.xlsx"
        wb = load_workbook(out)
        assert wb.sheetnames[0] == "Summary"
        assert set(wb.sheetnames) == {"Summary", "entry-1", "entry-2"}

        summary = wb["Summary"]
        # Row 2 = first criterion; columns B..D are per-source sums across 2 entries.
        assert summary.cell(row=2, column=2).value == 2
        # Overall average column follows the source-id columns (col E = 3 ids + 2).
        assert summary.cell(row=2, column=5).value == 1
        # Blank source_stem → category averages degrade to "na".
        assert summary.cell(row=2, column=6).value == "na"
        assert summary.cell(row=2, column=7).value == "na"
        assert summary.cell(row=2, column=8).value == "na"

    def test_classification_averages_with_known_stem(self, tmp_path: Path):
        batch = tmp_path / "batch"
        _make_batch(batch, ["entry-1"])
        out = create_xlsx(batch, source_stem="physics_1_set")
        summary = load_workbook(out)["Summary"]
        # Conceptual ids ∩ {1,2,3} = {1,3} → all-pass average 1.0.
        assert summary.cell(row=2, column=6).value == 1
        # Abstract ids {5,6,9,11} ∩ {1,2,3} = ∅ → "na".
        assert summary.cell(row=2, column=7).value == "na"
        # Numerical ids ∩ {1,2,3} = {2} → 1.0.
        assert summary.cell(row=2, column=8).value == 1

    def test_missing_batch_and_missing_csvs_raise(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            create_xlsx(tmp_path / "nope")
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FileNotFoundError):
            create_xlsx(empty)

    def test_missing_or_malformed_grade_is_na(self):
        assert parse_cell("", "") == "na"
        assert parse_cell("not-a-score", "") == "na"

    def test_duplicate_source_id_is_rejected(self, tmp_path: Path):
        entry = tmp_path / "entry"
        _write_entry_csv(
            entry,
            [
                {"source_id": 1},
                {"source_id": 1},
            ],
        )

        with pytest.raises(ProfessorComparisonError, match="duplicate source_id 1"):
            load_pdf_grid(
                entry / "evaluation_outputs" / "evaluation_spreadsheet.csv",
                CRITERIA_NAMES,
            )


class TestRunProfessorComparison:
    def test_end_to_end_writes_report_and_agreement_sheet(self, tmp_path: Path):
        batch = tmp_path / "batch"
        _make_batch(batch, ["entry-1", "entry-2"])
        professor = tmp_path / "professor.xlsx"
        _write_professor_workbook(professor, ["entry-1", "entry-2"])

        outcome = run_professor_comparison(batch, professor)
        assert outcome["report_path"].exists()
        assert outcome["disagreements_path"].exists()
        assert outcome["pdfs_compared"] == ["entry-1", "entry-2"]
        summary = outcome["report"]["summary"]
        assert 0 <= summary["mean_raw_agreement"] <= 1
        assert outcome["report"]["coverage"]["complete"] is True

        wb = load_workbook(outcome["xlsx_path"])
        assert wb.sheetnames.count("Agreement") == 1

        # Second run replaces, not duplicates, the Agreement sheet.
        outcome2 = run_professor_comparison(batch, professor)
        wb2 = load_workbook(outcome2["xlsx_path"])
        assert wb2.sheetnames.count("Agreement") == 1

    def test_reports_incomplete_sheet_coverage(self, tmp_path: Path):
        batch = tmp_path / "batch"
        _make_batch(batch, ["entry-1"])
        professor = tmp_path / "professor.xlsx"
        _write_professor_workbook(professor, ["entry-1", "professor-only"])

        outcome = run_professor_comparison(batch, professor)

        coverage = outcome["report"]["coverage"]
        assert coverage["complete"] is False
        assert coverage["missing_pdfs_from_judge"] == ["professor-only"]

    def test_missing_professor_workbook_raises(self, tmp_path: Path):
        batch = tmp_path / "batch"
        _make_batch(batch, ["entry-1"])
        with pytest.raises(FileNotFoundError):
            run_professor_comparison(batch, tmp_path / "missing.xlsx")

    def test_no_overlapping_sheets_raises(self, tmp_path: Path):
        batch = tmp_path / "batch"
        _make_batch(batch, ["entry-1"])
        professor = tmp_path / "professor.xlsx"
        _write_professor_workbook(professor, ["unrelated-sheet"])
        with pytest.raises(ProfessorComparisonError):
            run_professor_comparison(batch, professor)


class TestCompareProfessorRoute:
    @pytest.fixture()
    def client(self):
        from backend.src.server import app

        with TestClient(app) as test_client:
            yield test_client

    def test_bad_label_rejected(self, client):
        # Encoded traversal so the HTTP client doesn't normalize it away.
        response = client.post("/api/outputs/%2E%2E/escape/compare-professor")
        assert response.status_code in (400, 404)

    def test_missing_folder_404(self, client):
        response = client.post(f"/api/outputs/no-such-{uuid.uuid4().hex}/compare-professor")
        assert response.status_code == 404

    def test_success_shape(self, client):
        label = f"test-compare-{uuid.uuid4().hex}"
        batch = OUTPUTS_DIR / label
        _make_batch(batch, ["entry-1"])
        state.batch_jobs[label] = {"status": "completed", "outputFolder": label}
        stub = {
            "report": {
                "summary": {"mean_kappa": 0.5, "mean_raw_agreement": 0.9, "criteria_with_poor_kappa": []},
                "criteria": {},
            },
            "xlsx_path": batch / "XLSX" / "compiled.xlsx",
            "report_path": batch / "XLSX" / "judge_agreement_report.json",
            "disagreements_path": batch / "XLSX" / "judge_disagreements.csv",
            "pdfs_compared": ["entry-1"],
        }
        try:
            with patch(
                "backend.src.tools.professor_comparison.run_professor_comparison",
                return_value=stub,
            ):
                response = client.post(f"/api/outputs/{label}/compare-professor")
            assert response.status_code == 200
            payload = response.json()
            assert payload["outputFolder"] == label
            assert payload["summary"]["mean_kappa"] == 0.5
            assert payload["pdfsCompared"] == ["entry-1"]
            assert payload["xlsxPath"].endswith("compiled.xlsx")
        finally:
            state.batch_jobs.pop(label, None)
            shutil.rmtree(batch, ignore_errors=True)

    def test_comparison_failure_maps_to_422(self, client):
        label = f"test-compare-{uuid.uuid4().hex}"
        batch = OUTPUTS_DIR / label
        _make_batch(batch, ["entry-1"])
        state.batch_jobs[label] = {"status": "completed", "outputFolder": label}
        try:
            with patch(
                "backend.src.tools.professor_comparison.run_professor_comparison",
                side_effect=ProfessorComparisonError("no overlap"),
            ):
                response = client.post(f"/api/outputs/{label}/compare-professor")
            assert response.status_code == 422
        finally:
            state.batch_jobs.pop(label, None)
            shutil.rmtree(batch, ignore_errors=True)
