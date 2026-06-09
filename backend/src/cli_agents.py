"""Per-agent CLI for isolated testing and debugging.

Runs a single agent or a subset of the pipeline starting from a given agent,
using a saved JSON input file. Artifacts are written to backend/outputs/.

Usage:
    python -m backend.src.cli_agents list
    python -m backend.src.cli_agents schema <agent>
    python -m backend.src.cli_agents validate <agent> --input <file>
    python -m backend.src.cli_agents run <agent> --input <file> [--stop-after <agent>] [--no-eval]
    python -m backend.src.cli_agents run-one <agent> --input <file> [--output <file>]

The `run` subcommand starts from <agent> and continues the full pipeline
(or stops at --stop-after). `run-one` is shorthand for --stop-after <agent>.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import asyncio
import json
import os
from datetime import datetime
from typing import Any

from agents.logger import logger
from openai import OpenAI
from pydantic import ValidationError

from backend.src.artifacts import AGENT_DISPLAY_NAMES, WORKFLOW_AGENT_SEQUENCE
from backend.src.bootstrap import load_dotenv as _load_dotenv
from backend.src.evaluation import rubric_model as _load_rubric_model
from backend.src.evaluation import rubric_payload as _load_rubric_payload
from backend.src.paths import ENV_PATH, OUTPUTS_DIR
from backend.src.registry import cli_descriptions as _cli_descriptions
from backend.src.registry import llm_agents as _llm_agents
from backend.src.safety import create_unique_run_dir
from backend.src.schemas import AGENT_INPUT_TYPES, WorkflowResult
from backend.src.workflow import run_workflow_async

_load_dotenv(ENV_PATH)


_cli_client: OpenAI | None = None


def _get_client() -> OpenAI:
    """Lazily create the OpenAI client so subcommands that don't need it stay fast."""
    global _cli_client
    if _cli_client is None:
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for run/run-one subcommands.")
        _cli_client = OpenAI(api_key=api_key)
    return _cli_client


def _new_workflow_record() -> dict[str, Any]:
    agent_keys = tuple(WORKFLOW_AGENT_SEQUENCE)
    return {
        "status": "pending",
        "outputFolder": "",
        "container": {"status": "idle", "id": "", "allowedDomains": []},
        "agents": {key: {"status": "idle", "data": {}} for key in agent_keys},
        "files": [],
        "result": None,
    }


async def _to_thread(func, /, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


def _load_input_json(path: str) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    text = file_path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Input file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Input file must be a JSON object, got {type(data).__name__}")
    return data


# ---------- subcommand handlers ----------

def cmd_list(_args: argparse.Namespace) -> None:
    """List all agents with descriptions."""
    print("Agents (in pipeline order):\n")
    for key in WORKFLOW_AGENT_SEQUENCE:
        name = AGENT_DISPLAY_NAMES.get(key, key.title())
        desc = _cli_descriptions().get(key, "")
        marker = "  " if key not in _llm_agents() else "* "
        print(f"  {marker}{key:<14} {name:<14} {desc}")
    print("\n  * = LLM agent (requires OPENAI_API_KEY to run)")
    print("\nUse  `schema <agent>`  to see the expected input JSON structure.")
    print("Use  `validate <agent> --input <file>`  to check a JSON file without making API calls.")


def cmd_schema(args: argparse.Namespace) -> None:
    """Print the JSON schema for an agent's input type."""
    agent = args.agent
    if agent not in AGENT_INPUT_TYPES:
        print(f"No input schema defined for agent '{agent}'.")
        print(f"Agents with schemas: {', '.join(AGENT_INPUT_TYPES)}")
        raise SystemExit(1)
    schema = AGENT_INPUT_TYPES[agent].model_json_schema()
    print(json.dumps(schema, indent=2))


def cmd_validate(args: argparse.Namespace) -> None:
    """Validate an input JSON file against the agent's Pydantic schema."""
    agent = args.agent
    if agent not in AGENT_INPUT_TYPES:
        print(f"No input schema defined for agent '{agent}'.")
        raise SystemExit(1)
    try:
        data = _load_input_json(args.input)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}")
        raise SystemExit(1) from exc

    schema_type = AGENT_INPUT_TYPES[agent]
    try:
        schema_type.model_validate(data)
        print(f"OK — input is valid for agent '{agent}' ({schema_type.__name__})")
    except ValidationError as exc:
        print(f"INVALID — input does not match {schema_type.__name__}:\n")
        for error in exc.errors():
            loc = " → ".join(str(p) for p in error["loc"])
            print(f"  [{loc}] {error['msg']}")
        raise SystemExit(1)


async def _run_from_agent(
    *,
    agent: str,
    input_path: str,
    stop_after: str | None,
    run_evaluation: bool,
    output_path: str | None,
) -> None:
    """Core async runner shared by `run` and `run-one`."""
    try:
        input_data = _load_input_json(input_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}")
        raise SystemExit(1) from exc

    client = _get_client()
    timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    run_label = f"cli-{agent}-{timestamp}"
    outputs_dir, output_folder_label = create_unique_run_dir(OUTPUTS_DIR, run_label)
    workflow_record = _new_workflow_record()

    logger.info(
        "cli_agents: running from '%s'%s → %s",
        agent,
        f" (stop after '{stop_after}')" if stop_after else " (full pipeline)",
        outputs_dir,
    )

    result: WorkflowResult = await run_workflow_async(
        client=client,
        workflow_id=run_label,
        workflow=workflow_record,
        prompt="",
        file_paths=[],
        outputs_dir=outputs_dir,
        output_folder_label=output_folder_label,
        touch_workflow=lambda _: None,
        to_thread=_to_thread,
        load_rubric_model=_load_rubric_model,
        load_rubric_payload=_load_rubric_payload,
        resume_from_agent=agent,
        agent_input_override=input_data,
        agent_input_filename=str(input_path),
        input_to_agent_run=True,
        run_evaluation=run_evaluation,
        stop_after_agent=stop_after,
    )

    print(f"\nOutput directory: {outputs_dir}")
    if stop_after:
        print(f"Stopped after: {stop_after}")
    print(f"  Source questions:    {len(result.source_questions)}")
    print(f"  Verified questions:  {len(result.verified_questions)}")
    print(f"  Generated questions: {len(result.generated_questions)}")
    if result.latex_file:
        print(f"  LaTeX file:          {result.latex_file}")

    if output_path:
        out = Path(output_path)
        out.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        print(f"\nResult JSON written to: {out}")
    elif stop_after:
        # For run-one, pretty-print the relevant agent's output
        if stop_after == "normalizer":
            _print_partial(result.config.model_dump(), "normalizer output (config)")
        elif stop_after == "extracter":
            _print_partial({"questions": [q.model_dump() for q in result.source_questions]}, "extracter output")
        elif stop_after == "verifier":
            _print_partial({"questions": [q.model_dump() for q in result.verified_questions]}, "verifier output")
        elif stop_after == "generator":
            _print_partial(
                {"isomorphic_questions": [q.model_dump() for q in result.generated_questions]}, "generator output"
            )
        elif stop_after == "latexifier":
            if result.generated_questions:
                print(f"\n[latexifier output] LaTeX written to {outputs_dir / result.latex_file}")


def _print_partial(data: dict[str, Any], label: str) -> None:
    print(f"\n[{label}]\n" + json.dumps(data, indent=2))


def cmd_run(args: argparse.Namespace) -> None:
    """Start pipeline from <agent> using a saved input JSON."""
    stop_after = getattr(args, "stop_after", None)
    run_evaluation = not getattr(args, "no_eval", False)
    output_path = getattr(args, "output", None)
    asyncio.run(
        _run_from_agent(
            agent=args.agent,
            input_path=args.input,
            stop_after=stop_after,
            run_evaluation=run_evaluation,
            output_path=output_path,
        )
    )


def cmd_run_one(args: argparse.Namespace) -> None:
    """Run exactly one agent and print its output."""
    output_path = getattr(args, "output", None)
    asyncio.run(
        _run_from_agent(
            agent=args.agent,
            input_path=args.input,
            stop_after=args.agent,
            run_evaluation=False,
            output_path=output_path,
        )
    )


# ---------- argument parser ----------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.src.cli_agents",
        description="Per-agent CLI for the isomorphic question workflow",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # list
    sub.add_parser("list", help="List all agents")

    # schema
    p_schema = sub.add_parser("schema", help="Print the JSON schema for an agent's input")
    p_schema.add_argument("agent", choices=list(AGENT_INPUT_TYPES), help="Agent name")

    # validate
    p_val = sub.add_parser(
        "validate",
        help="Validate an input JSON file against the agent's schema (no API calls)",
    )
    p_val.add_argument("agent", choices=list(AGENT_INPUT_TYPES), help="Agent name")
    p_val.add_argument("--input", required=True, metavar="FILE", help="Path to input JSON")

    # run
    p_run = sub.add_parser(
        "run",
        help="Start pipeline from <agent> using a saved input JSON; continues to end unless --stop-after",
    )
    p_run.add_argument("agent", choices=list(_llm_agents()), help="Agent to start from")
    p_run.add_argument("--input", required=True, metavar="FILE", help="Path to input JSON")
    p_run.add_argument(
        "--stop-after",
        dest="stop_after",
        choices=list(_llm_agents()),
        default=None,
        metavar="AGENT",
        help="Stop the pipeline after this agent (must be at or after <agent>)",
    )
    p_run.add_argument(
        "--no-eval",
        dest="no_eval",
        action="store_true",
        help="Skip the evaluation stage",
    )
    p_run.add_argument(
        "--output",
        metavar="FILE",
        default=None,
        help="Write WorkflowResult JSON to this file",
    )

    # run-one
    p_one = sub.add_parser(
        "run-one",
        help="Run exactly one agent and print/save its output (equivalent to run --stop-after <agent>)",
    )
    p_one.add_argument("agent", choices=list(_llm_agents()), help="Agent to run")
    p_one.add_argument("--input", required=True, metavar="FILE", help="Path to input JSON")
    p_one.add_argument(
        "--output",
        metavar="FILE",
        default=None,
        help="Write the agent's output JSON to this file",
    )

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    dispatch = {
        "list": cmd_list,
        "schema": cmd_schema,
        "validate": cmd_validate,
        "run": cmd_run,
        "run-one": cmd_run_one,
    }

    try:
        dispatch[args.subcommand](args)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\nInterrupted.")
        raise SystemExit(130)
    except Exception as exc:
        logger.error("cli_agents error: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
