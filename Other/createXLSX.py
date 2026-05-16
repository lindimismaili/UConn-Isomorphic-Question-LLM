#!/usr/bin/env python3
"""Build a single Evaluation-style workbook for an eval batch.

The output matches the professor's `Other/Evaluation.xlsx` layout:

    <batch>/XLSX/compiled.xlsx
        Sheet 1: "Summary"  — criteria as rows, source_ids 1..N as columns,
                              cells = SUM of passes across all PDFs ('na' if all PDFs N/A),
                              plus Overall / Conceptual / Abstract / Numerical averages.
        Sheets 2..K: one per Phase_1 PDF ("1219-1", "1220-2", ...) — criteria as
                     rows, source_ids 1..N as columns, cells = 0/1/'na' for that PDF.

Run from the repo root:

    python Other/createXLSX.py [path/to/batch]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


SCRIPT_PATH = Path(__file__).resolve()
OTHER_DIR = SCRIPT_PATH.parent
REPO_ROOT = OTHER_DIR.parent
RUBRIC_PATH = REPO_ROOT / "backend" / "data" / "rubric.json"
LATEX_CRITERION = "latex_syntax_quality"

# Professor's manual classification of source questions in physics_1_set.pdf.
# Source IDs are 1-indexed. Conceptual = no meaningful numerical content;
# Abstract = symbolic / abstract-scenario items the professor flagged; the
# remainder are treated as Numerical.
CONCEPTUAL_IDS = {1, 3, 7, 10}
ABSTRACT_IDS = {5, 6, 9, 11}
NUMERICAL_IDS = {2, 4, 8, 12, 13}

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(color="FFFFFF", bold=True)
PASS_FILL = PatternFill("solid", fgColor="C6EFCE")
FAIL_FILL = PatternFill("solid", fgColor="FFC7CE")
NA_FILL = PatternFill("solid", fgColor="E7E6E6")
AVG_FILL = PatternFill("solid", fgColor="D9EAF7")


def load_rubric() -> list[dict[str, Any]]:
    data = json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))
    return [c for c in data.get("criteria", []) if c.get("name") and c["name"] != LATEX_CRITERION]


def source_sort_key(value: Any) -> tuple[int, Any]:
    text = str(value).strip()
    if text.isdigit():
        return (0, int(text))
    match = re.search(r"\d+", text)
    if match:
        return (1, int(match.group()), text)
    return (2, text)


def newest_batch() -> Path:
    candidates = sorted(
        [p for p in OTHER_DIR.glob("*other-phase1-eval-batch*") if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError("No *other-phase1-eval-batch* folders found under Other")
    return candidates[0]


def batch_report_dirs(batch_dir: Path) -> list[Path]:
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
    """Return {source_id: {criterion: 0|1|'na'}} for one PDF's CSV."""
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


def style_cell(cell, value: int | str) -> None:
    cell.alignment = Alignment(horizontal="center", vertical="center")
    if value == "na":
        cell.fill = NA_FILL
    elif value == 1:
        cell.fill = PASS_FILL
    elif value == 0:
        cell.fill = FAIL_FILL


def write_pdf_sheet(wb: Workbook, pdf_name: str, source_ids: list[int],
                    grid: dict[int, dict[str, int | str]],
                    criteria: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet(pdf_name[:31])
    ws.append([pdf_name, *source_ids])
    for criterion in criteria:
        name = criterion["name"]
        row = [criterion.get("question", name)]
        for sid in source_ids:
            row.append(grid.get(sid, {}).get(name, "na"))
        ws.append(row)
    # Style
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
            style_cell(cell, cell.value)
    ws.freeze_panes = "B2"


def write_summary_sheet(wb: Workbook, source_ids: list[int],
                        per_pdf_grids: dict[str, dict[int, dict[str, int | str]]],
                        criteria: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet("Summary", 0)
    headers = [
        "All", *source_ids,
        "Overall Average", "Conceptual Average", "Abstract Average", "Numerical Average",
    ]
    ws.append(headers)
    n_pdfs = len(per_pdf_grids)

    for criterion in criteria:
        name = criterion["name"]
        question = criterion.get("question", name)
        row: list[Any] = [question]
        # Per-source-id sum (or 'na' if all PDFs say na for this id)
        sums: dict[int, int | str] = {}
        for sid in source_ids:
            values = [grid.get(sid, {}).get(name) for grid in per_pdf_grids.values()]
            numeric = [v for v in values if isinstance(v, int)]
            if numeric:
                sums[sid] = sum(numeric)
            else:
                sums[sid] = "na"
            row.append(sums[sid])

        def avg(ids: set[int]) -> float | str:
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

        overall = avg(set(source_ids))
        conceptual = avg(CONCEPTUAL_IDS)
        abstract = avg(ABSTRACT_IDS)
        numerical = avg(NUMERICAL_IDS)
        row.extend([overall, conceptual, abstract, numerical])
        ws.append(row)

    # Style
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


def create_xlsx(batch_dir: Path) -> Path:
    batch_dir = batch_dir.expanduser().resolve()
    if not batch_dir.exists():
        raise FileNotFoundError(f"Batch folder not found: {batch_dir}")

    report_dirs = batch_report_dirs(batch_dir)
    if not report_dirs:
        raise FileNotFoundError(f"No evaluation_spreadsheet.csv files found in {batch_dir}")

    criteria = load_rubric()
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
    # Drop the default sheet — write_summary_sheet inserts "Summary" at index 0.
    wb.remove(wb.active)
    for pdf_name in sorted(per_pdf_grids, key=source_sort_key):
        write_pdf_sheet(wb, pdf_name, source_ids, per_pdf_grids[pdf_name], criteria)
    write_summary_sheet(wb, source_ids, per_pdf_grids, criteria)

    wb.save(output_path)
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", nargs="?", type=Path,
                        help="Batch folder to convert. Defaults to newest under Other/.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    batch_dir = args.batch or newest_batch()
    out = create_xlsx(batch_dir)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
