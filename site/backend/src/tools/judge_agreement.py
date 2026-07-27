"""Judge ↔ professor agreement metrics for evaluation runs.

Loads the professor's hand-graded workbook (sheet-per-PDF, criteria rows,
source-id columns, cells = 0/1/'na') and a generated batch's per-PDF judge
grids, then computes raw agreement, Cohen's κ, and a per-criterion confusion
matrix. Cells where either side is 'na' are excluded from κ to avoid spurious
penalty for declared-N/A items.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from openpyxl import load_workbook

Cell = int | str  # 0, 1, or 'na'
Grid = dict[int, dict[str, Cell]]  # {source_id: {criterion_name: cell}}


SUMMARY_SHEET_NAMES = {"summary", "overview", "all"}


def cohen_kappa(pairs: Iterable[tuple[int, int]]) -> float | None:
    """Compute Cohen's κ for binary 0/1 pairs. Returns None if undefined."""
    pairs = list(pairs)
    n = len(pairs)
    if n == 0:
        return None
    po = sum(1 for x, y in pairs if x == y) / n
    a_counts = {0: 0, 1: 0}
    b_counts = {0: 0, 1: 0}
    for x, y in pairs:
        a_counts[x] += 1
        b_counts[y] += 1
    pe = sum((a_counts[k] / n) * (b_counts[k] / n) for k in (0, 1))
    if pe >= 1.0:
        # When both raters use only one label, expected agreement is 1 and
        # Cohen's kappa has a zero denominator. Raw agreement remains useful,
        # but reporting kappa=1 would overstate calibration evidence.
        return None
    return round((po - pe) / (1 - pe), 4)


@dataclass
class CriterionAgreement:
    name: str
    n_compared: int = 0
    n_na_either: int = 0
    raw_agreement: float | None = None
    kappa: float | None = None
    # Confusion matrix keyed by (professor, judge).
    confusion: dict[str, int] = field(
        default_factory=lambda: {"00": 0, "01": 0, "10": 0, "11": 0}
    )
    judge_only_pass: int = 0  # prof=0, judge=1 — judge inflates
    judge_only_fail: int = 0  # prof=1, judge=0 — judge misses

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "n_compared": self.n_compared,
            "n_na_either": self.n_na_either,
            "raw_agreement": self.raw_agreement,
            "cohen_kappa": self.kappa,
            "confusion": dict(self.confusion),
            "judge_inflated_pass_count": self.judge_only_pass,
            "judge_missed_pass_count": self.judge_only_fail,
        }


def _norm_cell(value: Any) -> Cell:
    if value is None:
        return "na"
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        v = int(value)
        if v in (0, 1):
            return v
        return 1 if v >= 1 else 0
    text = str(value).strip().lower()
    if not text or text in {"na", "n/a", "-", "—"}:
        return "na"
    if text in {"1", "true", "pass", "passed", "yes", "y"}:
        return 1
    if text in {"0", "false", "fail", "failed", "no", "n"}:
        return 0
    # Numeric strings.
    try:
        return 1 if float(text) >= 1 else 0
    except ValueError:
        return "na"


def _normalize_criterion_key(text: str) -> str:
    return " ".join(text.lower().split())


def load_xlsx_grids(
    xlsx_path: Path,
    criteria: list[dict[str, Any]],
    skip_sheets: set[str] | None = None,
) -> dict[str, Grid]:
    """Load per-PDF grids from a workbook in the createXLSX layout.

    First row: [pdf_label, source_id_1, source_id_2, ...].
    Subsequent rows: [criterion_question_or_name, cell_1, cell_2, ...].

    Cells are normalized to 0/1/'na'. Criteria are matched by exact rubric
    ``name`` or normalized ``question`` text. Unknown rows are ignored rather
    than being positionally assigned to a potentially different criterion.
    """
    skip_sheets = {s.lower() for s in (skip_sheets or set())} | SUMMARY_SHEET_NAMES
    wb = load_workbook(xlsx_path, data_only=True)
    name_by_question = {
        _normalize_criterion_key(str(c.get("question", ""))): c["name"]
        for c in criteria
        if c.get("question")
    }
    rubric_name_set = {c["name"] for c in criteria}

    grids: dict[str, Grid] = {}
    for sheet_name in wb.sheetnames:
        if sheet_name.strip().lower() in skip_sheets:
            continue
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        if not rows or len(rows) < 2:
            continue
        header = rows[0]
        source_ids: list[int] = []
        col_positions: list[int] = []
        for idx, cell in enumerate(header[1:], start=1):
            if cell is None:
                continue
            text = str(cell).strip()
            if text.isdigit():
                source_ids.append(int(text))
                col_positions.append(idx)
        if not source_ids:
            continue

        grid: Grid = {sid: {} for sid in source_ids}
        for row in rows[1:]:
            if not row:
                continue
            label = "" if row[0] is None else str(row[0]).strip()
            criterion_name: str | None = None
            if label in rubric_name_set:
                criterion_name = label
            else:
                criterion_name = name_by_question.get(_normalize_criterion_key(label))
            if criterion_name is None:
                continue
            for sid, col in zip(source_ids, col_positions, strict=False):
                if col >= len(row):
                    grid[sid][criterion_name] = "na"
                else:
                    grid[sid][criterion_name] = _norm_cell(row[col])
        grids[sheet_name] = grid
    return grids


def compare_grids(
    professor: dict[str, Grid],
    judge: dict[str, Grid],
    criteria: list[dict[str, Any]],
) -> tuple[dict[str, CriterionAgreement], list[dict[str, Any]]]:
    """Compare two batches of grids. Returns (per-criterion agreement, disagreement rows)."""
    per_criterion: dict[str, CriterionAgreement] = {
        c["name"]: CriterionAgreement(name=c["name"]) for c in criteria
    }
    disagreements: list[dict[str, Any]] = []

    common_pdfs = sorted(set(professor) & set(judge))
    for pdf in common_pdfs:
        prof_grid = professor[pdf]
        judge_grid = judge[pdf]
        common_sids = sorted(set(prof_grid) & set(judge_grid))
        for sid in common_sids:
            for criterion in criteria:
                name = criterion["name"]
                p = prof_grid.get(sid, {}).get(name, "na")
                j = judge_grid.get(sid, {}).get(name, "na")
                agg = per_criterion[name]
                if p == "na" or j == "na":
                    agg.n_na_either += 1
                    continue
                agg.n_compared += 1
                key = f"{p}{j}"
                agg.confusion[key] = agg.confusion.get(key, 0) + 1
                if p != j:
                    if p == 0 and j == 1:
                        agg.judge_only_pass += 1
                    else:
                        agg.judge_only_fail += 1
                    disagreements.append(
                        {
                            "pdf": pdf,
                            "source_id": sid,
                            "criterion": name,
                            "professor": p,
                            "judge": j,
                            "direction": "judge_inflated" if p == 0 else "judge_missed",
                        }
                    )

    for agg in per_criterion.values():
        if agg.n_compared:
            agree = agg.confusion["00"] + agg.confusion["11"]
            agg.raw_agreement = round(agree / agg.n_compared, 4)
            pairs = (
                [(0, 0)] * agg.confusion["00"]
                + [(0, 1)] * agg.confusion["01"]
                + [(1, 0)] * agg.confusion["10"]
                + [(1, 1)] * agg.confusion["11"]
            )
            agg.kappa = cohen_kappa(pairs)
    return per_criterion, disagreements


def write_disagreements_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("pdf,source_id,criterion,professor,judge,direction\n", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["pdf", "source_id", "criterion", "professor", "judge", "direction"],
        )
        writer.writeheader()
        writer.writerows(rows)


def write_report_json(
    path: Path,
    per_criterion: dict[str, CriterionAgreement],
    *,
    professor_path: Path,
    judge_source: str,
    pdfs_compared: list[str],
) -> dict[str, Any]:
    professor_label = professor_path.name or "professor-workbook"
    judge_label = Path(judge_source).name or "judge-output"
    payload = {
        "professor_workbook": professor_label,
        "judge_source": judge_label,
        "pdfs_compared": pdfs_compared,
        "criteria": {name: agg.to_dict() for name, agg in per_criterion.items()},
        "summary": _summary(per_criterion),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def _summary(per_criterion: dict[str, CriterionAgreement]) -> dict[str, Any]:
    kappas = [a.kappa for a in per_criterion.values() if a.kappa is not None]
    agreements = [a.raw_agreement for a in per_criterion.values() if a.raw_agreement is not None]
    return {
        "mean_kappa": round(sum(kappas) / len(kappas), 4) if kappas else None,
        "mean_raw_agreement": round(sum(agreements) / len(agreements), 4) if agreements else None,
        "criteria_with_poor_kappa": [
            name for name, agg in per_criterion.items()
            if agg.kappa is not None and agg.kappa < 0.4
        ],
    }
