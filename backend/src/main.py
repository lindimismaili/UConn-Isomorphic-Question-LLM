"""CLI entrypoint for the isomorphic question generation workflow.

This module parses user arguments and delegates the entire 6-stage pipeline to
the shared workflow engine. All orchestration logic lives in
`backend.src.workflow.engine` so the FastAPI server and the CLI run the same
code.
"""

from __future__ import annotations

# Allow `python backend/src/main.py` (script mode) to use absolute backend.src
# imports without a try/except dance. In `python -m backend.src.main` mode the
# insert is a no-op because the repo root is already on sys.path.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import asyncio
import os
from datetime import datetime
from typing import Any

from agents.logger import logger
from openai import OpenAI

from backend.src.bootstrap import load_dotenv as _load_dotenv
from backend.src.evaluation import rubric_model as _load_rubric_model
from backend.src.evaluation import rubric_payload as _load_rubric_payload
from backend.src.paths import (
    ENV_PATH,
    OUTPUTS_DIR,
    QUESTION_DATA_DIR,
)
from backend.src.safety import create_unique_run_dir
from backend.src.schemas import WorkflowInput, WorkflowResult
from backend.src.workflow import run_workflow_async

_load_dotenv(ENV_PATH)

_cli_openai_client: OpenAI | None = None


def get_cli_openai_client() -> OpenAI:
    """Lazily create the OpenAI client so `--help` does not need credentials."""
    global _cli_openai_client
    if _cli_openai_client is None:
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required to run the workflow.")
        _cli_openai_client = OpenAI(api_key=api_key)
    return _cli_openai_client


def _new_cli_workflow_record() -> dict[str, Any]:
    """Build the stub workflow dict the shared engine mutates in-place.

    The FastAPI server maintains the same shape and persists it for the UI; the
    CLI just discards the mutations after the run completes.
    """
    agent_keys = ("normalizer", "extracter", "verifier", "generator", "latexifier", "evaluation", "output")
    return {
        "status": "pending",
        "outputFolder": "",
        "container": {"status": "idle", "id": "", "allowedDomains": []},
        "agents": {key: {"status": "idle", "data": {}} for key in agent_keys},
        "files": [],
        "result": None,
    }


async def _to_thread(func, /, *args, **kwargs):
    """Run blocking SDK/filesystem calls in the default executor (no semaphore for CLI)."""
    return await asyncio.to_thread(func, *args, **kwargs)


async def run_workflow(workflow_input: WorkflowInput) -> WorkflowResult:
    """Run the full pipeline by delegating to the shared engine."""
    client = get_cli_openai_client()
    timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    outputs_dir, output_folder_label = create_unique_run_dir(
        OUTPUTS_DIR,
        f"cli-{timestamp}",
    )
    workflow_record = _new_cli_workflow_record()
    workflow_id = f"cli-{timestamp}"

    await run_workflow_async(
        client=client,
        workflow_id=workflow_id,
        workflow=workflow_record,
        prompt=workflow_input.user_instructions or "",
        file_paths=workflow_input.file_paths,
        outputs_dir=outputs_dir,
        output_folder_label=output_folder_label,
        touch_workflow=lambda _record: None,
        to_thread=_to_thread,
        load_rubric_model=_load_rubric_model,
        load_rubric_payload=_load_rubric_payload,
    )

    result_payload = workflow_record.get("result") or {}
    return WorkflowResult.model_validate(result_payload)


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        description="Generate isomorphic physics questions from PDF/DOCX files"
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Path(s) to PDF or DOCX files",
    )
    parser.add_argument(
        "-i", "--instructions",
        default="",
        help="Additional instructions for question generation",
    )
    parser.add_argument(
        "-n", "--num-questions",
        type=int,
        default=1,
        help="Number of isomorphic questions per source question",
    )
    args = parser.parse_args()

    if not args.files:
        default_file = QUESTION_DATA_DIR / "PDF" / "Quiz_Compilation.pdf"
        if default_file.exists():
            args.files = [str(default_file)]
            logger.info("Using default file: %s", default_file)
        else:
            logger.error("No files provided and default file not found: %s", default_file)
            raise SystemExit(1)

    instructions = args.instructions
    if args.num_questions != 1:
        instructions = f"Generate {args.num_questions} questions per source. " + instructions

    workflow_input = WorkflowInput(
        file_paths=args.files,
        user_instructions=instructions,
    )

    try:
        result = asyncio.run(run_workflow(workflow_input))
    except Exception as exc:
        logger.error("Workflow failed: %s", exc)
        raise SystemExit(1) from exc

    logger.info("WORKFLOW COMPLETE")
    logger.info("Source questions:    %d", len(result.source_questions))
    logger.info("Verified questions:  %d", len(result.verified_questions))
    logger.info("Generated questions: %d", len(result.generated_questions))

    if result.generated_questions:
        for q in result.generated_questions:
            logger.info("[Q%d] (from source %s): %s", q.id, q.source_id, q.stem[:80])


if __name__ == "__main__":
    main()
