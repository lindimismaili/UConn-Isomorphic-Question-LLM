"""Professor-comparison endpoint: judge vs. hand-graded workbook agreement."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Form, HTTPException

from backend.src.api.state import _relative_output_folder, _resolve_output_folder_label

router = APIRouter()

@router.post("/api/outputs/{label:path}/compare-professor")
async def compare_professor(label: str, sourceStem: str = Form("")):
    """Compare a batch output folder's judge grades against the professor workbook.

    Serves both create-data and eval-batch output folders — anything under
    OUTPUTS_DIR whose entries carry evaluation spreadsheets.
    """
    from backend.src.tools.professor_comparison import (
        DEFAULT_PROFESSOR_XLSX,
        ProfessorComparisonError,
        run_professor_comparison,
    )

    batch_dir = _resolve_output_folder_label(label)
    if not DEFAULT_PROFESSOR_XLSX.exists():
        raise HTTPException(status_code=404, detail="Professor workbook not found")

    try:
        outcome = await asyncio.to_thread(
            run_professor_comparison,
            batch_dir,
            source_stem=sourceStem or "",
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProfessorComparisonError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    report = outcome["report"]
    return {
        "outputFolder": _relative_output_folder(batch_dir),
        "summary": report.get("summary", {}),
        "criteria": report.get("criteria", {}),
        "pdfsCompared": outcome.get("pdfs_compared", []),
        "xlsxPath": str(outcome["xlsx_path"]),
    }
