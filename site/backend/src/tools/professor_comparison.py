"""Compile eval-batch spreadsheets and compare the judge against the professor's grid.

Port of the retired ``Other/createXLSX.py`` + ``Other/compare_to_professor.py``:

- ``create_xlsx(batch_dir)`` bundles every entry's
  ``evaluation_outputs/evaluation_spreadsheet.csv`` into ``<batch>/XLSX/compiled.xlsx``
  (a "Summary" sheet plus one sheet per entry), matching the professor's
  ``Evaluation.xlsx`` layout.
- ``run_professor_comparison(batch_dir, professor_xlsx)`` compares that workbook
  against the professor's hand-graded grid via ``judge_agreement`` and writes
  ``judge_agreement_report.json``, ``judge_disagreements.csv``, and an
  "Agreement" sheet appended to the compiled workbook.

The professor's manual conceptual/abstract/numerical classification of source
questions lives in ``data/professor_comparison/source_classification.json``,
keyed by source-PDF stem. An unrecognized or blank ``source_stem`` degrades
gracefully to ``"na"`` category averages.
"""

from __future__ import annotations

import csv
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from backend.src.evaluation import rubric_criteria
from backend.src.paths import DATA_DIR
from backend.src.tools.judge_agreement import (
    compare_grids,
    load_xlsx_grids,
    write_disagreements_csv,
    write_report_json,
)

PROFESSOR_DATA_DIR = DATA_DIR / "professor_comparison"
DEFAULT_PROFESSOR_XLSX = PROFESSOR_DATA_DIR / "Evaluation.xlsx"
SOURCE_CLASSIFICATION_PATH = PROFESSOR_DATA_DIR / "source_classification.json"

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(color="FFFFFF", bold=True)
PASS_FILL = PatternFill("solid", fgColor="C6EFCE")
FAIL_FILL = PatternFill("solid", fgColor="FFC7CE")
NA_FILL = PatternFill("solid", fgColor="E7E6E6")
AVG_FILL = PatternFill("solid", fgColor="D9EAF7")

CLASSIFICATION_CATEGORIES = ("conceptual", "abstract", "numerical")


class ProfessorComparisonError(RuntimeError):
    """Raised when the judge and professor workbooks cannot be compared."""


@lru_cache(maxsize=1)
def _load_source_classifications() -> dict[str, dict[str, frozenset[int]]]:
    """Load the hand-curated per-source-stem question classification (lazy)."""
    try:
        raw = json.loads(SOURCE_CLASSIFICATION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    classifications: dict[str, dict[str, frozenset[int]]] = {}
    for stem, categories in raw.items():
        if not isinstance(categories, dict):
            continue
        classifications[str(stem)] = {
            category: frozenset(
                int(item) for item in categories.get(category, []) if str(item).strip().isdigit() or isinstance(item, int)
            )
            for category in CLASSIFICATION_CATEGORIES
        }
    return classifications


def source_classification(source_stem: str) -> dict[str, frozenset[int]]:
    """Classification id sets for a source-PDF stem; empty sets when unknown."""
    known = _load_source_classifications().get((source_stem or "").strip())
    if known is not None:
        return known
    return {category: frozenset() for category in CLASSIFICATION_CATEGORIES}


def _load_criteria() -> list[dict[str, Any]]:
    return [c for c in rubric_criteria() if c.get("name")]


def source_sort_key(value: Any) -> tuple[Any, ...]:
    text = str(value).strip()
    if text.isdigit():
        return (0, int(text))
    match = re.search(r"\d+", text)
    if match:
        return (1, int(match.group()), text)
    return (2, text)


def batch_report_dirs(batch_dir: Path) -> list[Path]:
    """Batch entries that produced an evaluation spreadsheet."""
    dirs = []
    for path in sorted(batch_dir.iterdir(), key=lambda i: i.name):
        if (path / "evaluation_outputs" / "evaluation_spreadsheet.csv").exists():
            dirs.append(path)
    return dirs


def parse_cell(score_text: str, passed_text: str) -> int | str:
    s = (score_text or "").strip()
    p = (passed_text or "").strip().upper()
    if p == "NA" or s.upper() == "NA":
        return "na"
    if p in {"TRUE", "PASS", "PASSED", "1"}:
        return 1
    if p in {"FALSE", "FAIL", "FAILED", "0"}:
        return 0
    if not s:
        return 0
    try:
        return 1 if float(s) >= 1 else 0
    except ValueError:
        return 0


def load_pdf_grid(csv_path: Path, criteria_names: list[str]) -> dict[int, dict[str, int | str]]:
    """Return {source_id: {criterion: 0|1|'na'}} for one entry's CSV."""
    grid: dict[int, dict[str, int | str]] = {}
    with csv_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            sid_text = (row.get("source_id") or "").strip()
            if not sid_text.isdigit():
                continue
            sid = int(sid_text)
            cells: dict[str, int | str] = {}
            for name in criteria_names:
                cells[name] = parse_cell(
                    row.get(f"{name}_score", ""),
                    row.get(f"{name}_passed", ""),
                )
            grid[sid] = cells
    return grid


def _style_cell(cell, value: int | str) -> None:
    cell.alignment = Alignment(horizontal="center", vertical="center")
    if value == "na":
        cell.fill = NA_FILL
    elif value == 1:
        cell.fill = PASS_FILL
    elif value == 0:
        cell.fill = FAIL_FILL


def _write_pdf_sheet(
    wb: Workbook,
    pdf_name: str,
    source_ids: list[int],
    grid: dict[int, dict[str, int | str]],
    criteria: list[dict[str, Any]],
) -> None:
    ws = wb.create_sheet(pdf_name[:31])
    ws.append([pdf_name, *source_ids])
    for criterion in criteria:
        name = criterion["name"]
        row = [criterion.get("question", name)]
        for sid in source_ids:
            row.append(grid.get(sid, {}).get(name, "na"))
        ws.append(row)
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 30
    ws.column_dimensions["A"].width = 75
    for idx in range(2, len(source_ids) + 2):
        ws.column_dimensions[get_column_letter(idx)].width = 6
    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=1).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        for c in range(2, ws.max_column + 1):
            cell = ws.cell(row=r, column=c)
            _style_cell(cell, cell.value)
    ws.freeze_panes = "B2"


def _write_summary_sheet(
    wb: Workbook,
    source_ids: list[int],
    per_pdf_grids: dict[str, dict[int, dict[str, int | str]]],
    criteria: list[dict[str, Any]],
    classification: dict[str, frozenset[int]],
) -> None:
    ws = wb.create_sheet("Summary", 0)
    headers = [
        "All",
        *source_ids,
        "Overall Average",
        "Conceptual Average",
        "Abstract Average",
        "Numerical Average",
    ]
    ws.append(headers)
    n_pdfs = len(per_pdf_grids)

    for criterion in criteria:
        name = criterion["name"]
        question = criterion.get("question", name)
        row: list[Any] = [question]
        for sid in source_ids:
            values = [grid.get(sid, {}).get(name) for grid in per_pdf_grids.values()]
            numeric = [v for v in values if isinstance(v, int)]
            row.append(sum(numeric) if numeric else "na")

        def avg(ids: frozenset[int] | set[int], name: str = name) -> float | str:
            cells = []
            for sid in ids:
                if sid not in source_ids:
                    continue
                for grid in per_pdf_grids.values():
                    v = grid.get(sid, {}).get(name)
                    if isinstance(v, int):
                        cells.append(v)
            if not cells:
                return "na"
            return sum(cells) / len(cells)

        row.extend(
            [
                avg(set(source_ids)),
                avg(classification["conceptual"]),
                avg(classification["abstract"]),
                avg(classification["numerical"]),
            ]
        )
        ws.append(row)

    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 42
    ws.column_dimensions["A"].width = 75
    for idx in range(2, len(source_ids) + 2):
        ws.column_dimensions[get_column_letter(idx)].width = 6
    for idx in range(len(source_ids) + 2, len(headers) + 1):
        ws.column_dimensions[get_column_letter(idx)].width = 14

    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=1).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        for c in range(2, len(source_ids) + 2):
            cell = ws.cell(row=r, column=c)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            v = cell.value
            if v == "na":
                cell.fill = NA_FILL
            elif isinstance(v, (int, float)):
                if v == n_pdfs:
                    cell.fill = PASS_FILL
                elif v == 0:
                    cell.fill = FAIL_FILL
        for c in range(len(source_ids) + 2, len(headers) + 1):
            cell = ws.cell(row=r, column=c)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.fill = AVG_FILL
            if isinstance(cell.value, float):
                cell.number_format = "0.000"
    ws.freeze_panes = "B2"


def create_xlsx(batch_dir: Path, *, source_stem: str = "") -> Path:
    """Compile a batch's per-entry evaluation CSVs into ``<batch>/XLSX/compiled.xlsx``.

    Works on any batch directory whose entries contain
    ``evaluation_outputs/evaluation_spreadsheet.csv`` (both create-data and
    eval-batch output folders qualify).
    """
    batch_dir = batch_dir.expanduser().resolve()
    if not batch_dir.exists():
        raise FileNotFoundError(f"Batch folder not found: {batch_dir}")

    report_dirs = batch_report_dirs(batch_dir)
    if not report_dirs:
        raise FileNotFoundError(f"No evaluation_spreadsheet.csv files found in {batch_dir}")

    criteria = _load_criteria()
    criteria_names = [c["name"] for c in criteria]

    per_pdf_grids: dict[str, dict[int, dict[str, int | str]]] = {}
    all_source_ids: set[int] = set()
    for report_dir in report_dirs:
        csv_path = report_dir / "evaluation_outputs" / "evaluation_spreadsheet.csv"
        grid = load_pdf_grid(csv_path, criteria_names)
        per_pdf_grids[report_dir.name] = grid
        all_source_ids.update(grid.keys())

    source_ids = sorted(all_source_ids)

    output_dir = batch_dir / "XLSX"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "compiled.xlsx"

    wb = Workbook()
    # Drop the default sheet — _write_summary_sheet inserts "Summary" at index 0.
    wb.remove(wb.active)
    for pdf_name in sorted(per_pdf_grids, key=source_sort_key):
        _write_pdf_sheet(wb, pdf_name, source_ids, per_pdf_grids[pdf_name], criteria)
    _write_summary_sheet(wb, source_ids, per_pdf_grids, criteria, source_classification(source_stem))

    wb.save(output_path)
    return output_path


def write_agreement_sheet(xlsx_path: Path, report: dict) -> None:
    """Append/replace an 'Agreement' sheet on the compiled workbook."""
    from openpyxl import load_workbook

    wb = load_workbook(xlsx_path)
    if "Agreement" in wb.sheetnames:
        del wb["Agreement"]
    ws = wb.create_sheet("Agreement")
    ws.append(
        [
            "Criterion",
            "N compared",
            "N N/A (either)",
            "Raw agreement",
            "Cohen κ",
            "Confusion 00",
            "01",
            "10",
            "11",
            "Judge inflated",
            "Judge missed",
        ]
    )
    for cell in ws[1]:
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
    for crit in report["criteria"].values():
        conf = crit["confusion"]
        ws.append(
            [
                crit["name"],
                crit["n_compared"],
                crit["n_na_either"],
                crit["raw_agreement"],
                crit["cohen_kappa"],
                conf.get("00", 0),
                conf.get("01", 0),
                conf.get("10", 0),
                conf.get("11", 0),
                crit["judge_inflated_pass_count"],
                crit["judge_missed_pass_count"],
            ]
        )
    summary = report["summary"]
    ws.append([])
    ws.append(["Mean κ", summary["mean_kappa"]])
    ws.append(["Mean raw agreement", summary["mean_raw_agreement"]])
    ws.append(["Criteria with κ < 0.4", ", ".join(summary["criteria_with_poor_kappa"]) or "(none)"])
    ws.column_dimensions["A"].width = 38
    for col in "BCDEFGHIJK":
        ws.column_dimensions[col].width = 13
    wb.save(xlsx_path)


def run_professor_comparison(
    batch_dir: Path,
    professor_xlsx: Path | None = None,
    *,
    source_stem: str = "",
) -> dict[str, Any]:
    """Compare a batch's judge grades against the professor's workbook.

    Ensures ``<batch>/XLSX/compiled.xlsx`` exists (building it if needed),
    computes per-criterion agreement, writes the report JSON, disagreements
    CSV, and the workbook's "Agreement" sheet, and returns::

        {"report": ..., "xlsx_path": ..., "report_path": ...,
         "disagreements_path": ..., "pdfs_compared": [...]}

    Raises:
        FileNotFoundError: Missing batch folder, professor workbook, or
            evaluation spreadsheets.
        ProfessorComparisonError: No overlapping sheets to compare.
    """
    batch_dir = Path(batch_dir).expanduser().resolve()
    if not batch_dir.exists():
        raise FileNotFoundError(f"Batch folder not found: {batch_dir}")
    professor_path = Path(professor_xlsx) if professor_xlsx else DEFAULT_PROFESSOR_XLSX
    if not professor_path.exists():
        raise FileNotFoundError(f"Professor workbook not found: {professor_path}")

    compiled = batch_dir / "XLSX" / "compiled.xlsx"
    if not compiled.exists():
        compiled = create_xlsx(batch_dir, source_stem=source_stem)

    criteria = _load_criteria()
    professor_grids = load_xlsx_grids(professor_path, criteria)
    judge_grids = load_xlsx_grids(compiled, criteria)

    common = sorted(set(professor_grids) & set(judge_grids))
    if not common:
        raise ProfessorComparisonError(
            "No overlapping PDF sheets between professor workbook and judge workbook. "
            f"Professor sheets: {sorted(professor_grids)}. Judge sheets: {sorted(judge_grids)}."
        )

    per_criterion, disagreements = compare_grids(professor_grids, judge_grids, criteria)

    output_dir = compiled.parent
    report_path = output_dir / "judge_agreement_report.json"
    disagreements_path = output_dir / "judge_disagreements.csv"
    write_disagreements_csv(disagreements_path, disagreements)
    report = write_report_json(
        report_path,
        per_criterion,
        professor_path=professor_path,
        judge_source=str(compiled),
        pdfs_compared=common,
    )
    write_agreement_sheet(compiled, report)

    return {
        "report": report,
        "xlsx_path": compiled,
        "report_path": report_path,
        "disagreements_path": disagreements_path,
        "pdfs_compared": common,
    }
