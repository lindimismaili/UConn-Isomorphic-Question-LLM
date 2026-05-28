#!/usr/bin/env python3
"""Compare a generated eval batch against the professor's hand-graded workbook.

Usage:
    python Other/compare_to_professor.py <batch_dir> [--professor Other/Evaluation.xlsx]

Outputs (under <batch_dir>/XLSX/):
    judge_agreement_report.json — per-criterion raw agreement, Cohen's κ, confusion
    judge_disagreements.csv     — item-level disagreement list
    compiled.xlsx               — re-generated with an appended "Agreement" sheet

The judge grid is loaded from the batch's compiled.xlsx (created by
createXLSX.py). If the workbook doesn't exist, it is generated first.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "Other"))

from createXLSX import create_xlsx, load_rubric  # noqa: E402
from backend.src.tools.judge_agreement import (  # noqa: E402
    compare_grids,
    load_xlsx_grids,
    write_disagreements_csv,
    write_report_json,
)

DEFAULT_PROFESSOR = REPO_ROOT / "Other" / "Evaluation.xlsx"


def write_agreement_sheet(xlsx_path: Path, report: dict) -> None:
    """Append/replace an 'Agreement' sheet on the compiled workbook."""
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = load_workbook(xlsx_path)
    if "Agreement" in wb.sheetnames:
        del wb["Agreement"]
    ws = wb.create_sheet("Agreement")
    ws.append([
        "Criterion", "N compared", "N N/A (either)",
        "Raw agreement", "Cohen κ",
        "Confusion 00", "01", "10", "11",
        "Judge inflated", "Judge missed",
    ])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
    for crit in report["criteria"].values():
        conf = crit["confusion"]
        ws.append([
            crit["name"], crit["n_compared"], crit["n_na_either"],
            crit["raw_agreement"], crit["cohen_kappa"],
            conf.get("00", 0), conf.get("01", 0), conf.get("10", 0), conf.get("11", 0),
            crit["judge_inflated_pass_count"], crit["judge_missed_pass_count"],
        ])
    summary = report["summary"]
    ws.append([])
    ws.append(["Mean κ", summary["mean_kappa"]])
    ws.append(["Mean raw agreement", summary["mean_raw_agreement"]])
    ws.append(["Criteria with κ < 0.4", ", ".join(summary["criteria_with_poor_kappa"]) or "(none)"])
    ws.column_dimensions["A"].width = 38
    for col in "BCDEFGHIJK":
        ws.column_dimensions[col].width = 13
    wb.save(xlsx_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=Path, help="Batch folder to evaluate")
    parser.add_argument(
        "--professor", type=Path, default=DEFAULT_PROFESSOR,
        help=f"Professor workbook (default: {DEFAULT_PROFESSOR})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    batch_dir = args.batch.expanduser().resolve()
    if not batch_dir.exists():
        print(f"Batch folder not found: {batch_dir}", file=sys.stderr)
        return 2
    if not args.professor.exists():
        print(f"Professor workbook not found: {args.professor}", file=sys.stderr)
        return 2

    compiled = batch_dir / "XLSX" / "compiled.xlsx"
    if not compiled.exists():
        print(f"Generating judge workbook: {compiled}")
        compiled = create_xlsx(batch_dir)

    criteria = load_rubric()
    professor_grids = load_xlsx_grids(args.professor, criteria)
    judge_grids = load_xlsx_grids(compiled, criteria)

    common = sorted(set(professor_grids) & set(judge_grids))
    if not common:
        print(
            "No overlapping PDF sheets between professor workbook and judge "
            f"workbook. Professor sheets: {sorted(professor_grids)}. "
            f"Judge sheets: {sorted(judge_grids)}.",
            file=sys.stderr,
        )
        return 3

    per_criterion, disagreements = compare_grids(professor_grids, judge_grids, criteria)

    output_dir = compiled.parent
    report_path = output_dir / "judge_agreement_report.json"
    disagreements_path = output_dir / "judge_disagreements.csv"
    write_disagreements_csv(disagreements_path, disagreements)
    report = write_report_json(
        report_path,
        per_criterion,
        professor_path=args.professor,
        judge_source=str(compiled),
        pdfs_compared=common,
    )
    write_agreement_sheet(compiled, report)

    print(json.dumps(report["summary"], indent=2))
    print(f"\nWrote {report_path}")
    print(f"Wrote {disagreements_path}")
    print(f"Updated {compiled} (Agreement sheet)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
