"""Professor-comparison endpoint: judge vs. hand-graded workbook agreement."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Form, HTTPException

from backend.src.api.artifact_access import artifact_mutation_endpoint
from backend.src.api.state import (
    _output_owner_is_terminal,
    _relative_output_folder,
    _resolve_output_folder_label,
)

router = APIRouter()

@router.post("/api/outputs/{label:path}/compare-professor")
@artifact_mutation_endpoint
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
    owner_status = _output_owner_is_terminal(label)
    if owner_status is None:
        raise HTTPException(status_code=404, detail="Output owner not found")
    if not owner_status:
        raise HTTPException(
            status_code=409,
            detail="Professor comparison requires a fully stopped output owner.",
        )
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
    xlsx_file = outcome["xlsx_path"].resolve().relative_to(batch_dir.resolve()).as_posix()
    return {
        "outputFolder": _relative_output_folder(batch_dir),
        "summary": report.get("summary", {}),
        "criteria": report.get("criteria", {}),
        "pdfsCompared": outcome.get("pdfs_compared", []),
        "xlsxPath": xlsx_file,
    }
