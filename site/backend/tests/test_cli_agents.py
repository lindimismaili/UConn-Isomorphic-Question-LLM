"""Regression tests for the isolated per-agent CLI."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.src import cli_agents
from backend.src.registry import evaluation_agent, llm_agents


def test_agent_cli_evaluation_is_explicit_opt_in() -> None:
    parser = cli_agents._build_parser()

    default_args = parser.parse_args(
        ["run", llm_agents()[0], "--input", "input.json"]
    )
    eval_args = parser.parse_args(
        ["run", llm_agents()[0], "--input", "input.json", "--eval"]
    )

    assert default_args.run_evaluation is False
    assert eval_args.run_evaluation is True


def test_agent_cli_evaluation_start_requires_opt_in() -> None:
    args = cli_agents._build_parser().parse_args(
        ["run", evaluation_agent(), "--input", "input.json"]
    )

    with patch.object(cli_agents, "_run_from_agent") as run:
        with pytest.raises(ValueError, match="explicit --eval"):
            cli_agents.cmd_run(args)

    run.assert_not_called()


def test_run_one_excludes_non_stoppable_evaluation(capsys) -> None:
    with pytest.raises(SystemExit):
        cli_agents._build_parser().parse_args(
            ["run-one", evaluation_agent(), "--input", "input.json"]
        )
    assert "invalid choice" in capsys.readouterr().err


def test_agent_cli_rejects_stop_before_start() -> None:
    sequence = list(llm_agents())
    args = cli_agents._build_parser().parse_args(
        [
            "run",
            sequence[1],
            "--input",
            "input.json",
            "--stop-after",
            sequence[0],
        ]
    )

    with patch.object(cli_agents, "_run_from_agent") as run:
        with pytest.raises(ValueError, match="starting agent or a later"):
            cli_agents.cmd_run(args)

    run.assert_not_called()
