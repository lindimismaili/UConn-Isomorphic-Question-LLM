#!/usr/bin/env python3
"""Create Cleaned-XLSX-style workbooks for an eval batch.

The script reads a batch folder created by Other/runEvals.py and writes:

    <batch>/XLSX/<phase-file>.xlsx
    <batch>/XLSX/compiled.xlsx

Run from the repo root:

    python Other/createXLSX.py Other/<timestamp>-other-phase1-eval-batch

If no batch folder is provided, the newest matching batch under Other is used.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill


SCRIPT_PATH = Path(__file__).resolve()
OTHER_DIR = SCRIPT_PATH.parent

CRITERIA = [
    "preserve_physical_content",
    "scenario_variation",
    "numerical_variation",
    "physics_correctness",
    "numeric_reasonableness",
    "distractor_structure_preservation",
    "clerical_quality",
    "solvable_and_specified",
]

PER_FILE_HEADERS = [
    "source_id",
    "status",
    *[f"{criterion}_score" for criterion in CRITERIA],
]

COMPILED_HEADERS = [
    "source_id",
    "max_versions",
    *[f"{criterion}_score_total" for criterion in CRITERIA],
]

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(color="FFFFFF", bold=True)
PASS_FILL = PatternFill("solid", fgColor="C6EFCE")
FAIL_FILL = PatternFill("solid", fgColor="FFC7CE")
TOTAL_FILL = PatternFill("solid", fgColor="D9EAF7")


def source_sort_key(value: Any) -> tuple[int, Any]:
    text = str(value).strip()
    if text.isdigit():
        return (0, int(text))
    match = re.search(r"\d+", text)
    if match:
        return (1, int(match.group()), text)
    return (2, text)


def truthy(value: str) -> bool:
    return str(value).strip().upper() in {"TRUE", "1", "YES", "PASSED", "PASS"}


def cell_source_id(value: Any) -> int | str:
    text = str(value).strip()
    return int(text) if text.isdigit() else text


def newest_batch() -> Path:
    candidates = sorted(
        [
            path
            for path in OTHER_DIR.glob("*other-phase1-eval-batch*")
            if path.is_dir()
        ],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError("No *other-phase1-eval-batch* folders found under Other")
    return candidates[0]


def batch_report_dirs(batch_dir: Path) -> list[Path]:
    report_dirs = []
    for path in sorted(batch_dir.iterdir(), key=lambda item: item.name):
        csv_path = path / "evaluation_outputs" / "evaluation_spreadsheet.csv"
        if csv_path.exists():
            report_dirs.append(path)
    return report_dirs


def load_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def cleaned_rows(rows: list[dict[str, str]]) -> list[list[Any]]:
    output_rows = []
    for row in rows:
        source_id = row.get("source_id", "")
        status = row.get("status", "")
        values = [cell_source_id(source_id), status]
        for criterion in CRITERIA:
            passed_key = f"{criterion}_passed"
            score_key = f"{criterion}_score"
            if passed_key in row and row[passed_key] != "":
                values.append(1 if truthy(row[passed_key]) else 0)
            else:
                try:
                    values.append(1 if float(row.get(score_key, 0) or 0) >= 70 else 0)
                except ValueError:
                    values.append(0)
        output_rows.append(values)
    return sorted(output_rows, key=lambda item: source_sort_key(item[0]))


def safe_sheet_name(name: str) -> str:
    cleaned = re.sub(r"[\[\]:*?/\\]", "-", name).strip() or "Sheet1"
    return cleaned[:31]


def style_sheet(ws, *, compiled: bool = False) -> None:
    max_row = ws.max_row
    max_col = ws.max_column
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    widths = [14, 14, 33, 28, 30, 30, 34, 46, 28, 34]
    for idx, width in enumerate(widths[:max_col], start=1):
        ws.column_dimensions[chr(64 + idx)].width = width

    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 42 if compiled else 38

    for row in ws.iter_rows(min_row=2, max_row=max_row, max_col=max_col):
        for cell in row:
            cell.alignment = Alignment(horizontal="center", vertical="center")
        if compiled and row[0].value == "TOTAL":
            for cell in row:
                cell.fill = TOTAL_FILL
                cell.font = Font(bold=True)
            continue
        if not compiled:
            status = str(row[1].value or "").lower()
            status_fill = PASS_FILL if status == "passed" else FAIL_FILL
            row[1].fill = status_fill
            for cell in row[2:]:
                cell.fill = PASS_FILL if cell.value == 1 else FAIL_FILL


def write_workbook(path: Path, sheet_name: str, headers: list[str], rows: list[list[Any]], *, compiled: bool = False) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = safe_sheet_name(sheet_name)
    ws.append(headers)
    for row in rows:
        ws.append(row)
    style_sheet(ws, compiled=compiled)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def build_compiled(per_file_rows: dict[str, list[list[Any]]]) -> list[list[Any]]:
    counts: dict[str, int] = defaultdict(int)
    totals: dict[str, list[int]] = defaultdict(lambda: [0] * len(CRITERIA))

    for rows in per_file_rows.values():
        seen_in_file: set[str] = set()
        for row in rows:
            source_id = str(row[0])
            if source_id not in seen_in_file:
                counts[source_id] += 1
                seen_in_file.add(source_id)
            for index, value in enumerate(row[2:]):
                totals[source_id][index] += int(value or 0)

    compiled_rows = [
        [cell_source_id(source_id), counts[source_id], *totals[source_id]]
        for source_id in sorted(totals, key=source_sort_key)
    ]
    if compiled_rows:
        total_row = ["TOTAL", sum(row[1] for row in compiled_rows)]
        for index in range(len(CRITERIA)):
            total_row.append(sum(row[index + 2] for row in compiled_rows))
        compiled_rows.append(total_row)
    return compiled_rows


def create_xlsx(batch_dir: Path) -> Path:
    batch_dir = batch_dir.expanduser().resolve()
    if not batch_dir.exists():
        raise FileNotFoundError(f"Batch folder not found: {batch_dir}")

    report_dirs = batch_report_dirs(batch_dir)
    if not report_dirs:
        raise FileNotFoundError(f"No evaluation_spreadsheet.csv files found in {batch_dir}")

    output_dir = batch_dir / "XLSX"
    output_dir.mkdir(parents=True, exist_ok=True)

    per_file_rows: dict[str, list[list[Any]]] = {}
    for report_dir in report_dirs:
        csv_path = report_dir / "evaluation_outputs" / "evaluation_spreadsheet.csv"
        rows = cleaned_rows(load_rows(csv_path))
        per_file_rows[report_dir.name] = rows
        write_workbook(
            output_dir / f"{report_dir.name}.xlsx",
            report_dir.name,
            PER_FILE_HEADERS,
            rows,
        )

    compiled_rows = build_compiled(per_file_rows)
    write_workbook(
        output_dir / "compiled.xlsx",
        "Compiled Totals",
        COMPILED_HEADERS,
        compiled_rows,
        compiled=True,
    )
    return output_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "batch",
        nargs="?",
        type=Path,
        help="Batch folder to convert. Defaults to the newest Other/*other-phase1-eval-batch* folder.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    batch_dir = args.batch or newest_batch()
    output_dir = create_xlsx(batch_dir)
    print(f"Wrote XLSX files to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
