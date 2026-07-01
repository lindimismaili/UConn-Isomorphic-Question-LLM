"""Offline tests for the argparse subcommand CLI (backend.src.main).

Every test mocks the shared engine / batch coroutines (via unittest.mock.patch)
so nothing hits the network or requires OPENAI_API_KEY. The CLI's own engine
wrapper (``run_workflow``) calls ``run_workflow_async`` from the engine, so we
patch that symbol *on the main module* (where it was imported) to intercept the
call and capture its kwargs.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.src import main
from backend.src.registry import llm_agents, stoppable_agents
from backend.src.schemas import NormalizerOutput, WorkflowResult


def _fake_result() -> WorkflowResult:
    """Minimal WorkflowResult so _log_workflow_result runs without error."""
    return WorkflowResult(
        config=NormalizerOutput(),
        source_questions=[],
        verified_questions=[],
        generated_questions=[],
    )


class NormalizeArgvShimTests(unittest.TestCase):
    """The no-subcommand shim must route legacy flat invocations to `run`."""

    def test_leading_file_routes_to_run(self) -> None:
        self.assertEqual(
            main._normalize_argv(["x.pdf", "-n", "2", "-i", "hi"]),
            ["run", "x.pdf", "-n", "2", "-i", "hi"],
        )

    def test_leading_flag_routes_to_run(self) -> None:
        self.assertEqual(
            main._normalize_argv(["-i", "hi", "x.pdf"]),
            ["run", "-i", "hi", "x.pdf"],
        )

    def test_explicit_subcommand_untouched(self) -> None:
        self.assertEqual(main._normalize_argv(["list", "themes"]), ["list", "themes"])
        self.assertEqual(main._normalize_argv(["run", "a.pdf"]), ["run", "a.pdf"])

    def test_help_flag_untouched(self) -> None:
        self.assertEqual(main._normalize_argv(["-h"]), ["-h"])
        self.assertEqual(main._normalize_argv(["--help"]), ["--help"])

    def test_shim_dispatches_run_handler(self) -> None:
        """A bare file argument must parse into the `run` command handler."""
        parser = main.build_parser()
        args = parser.parse_args(main._normalize_argv(["some.pdf", "-n", "3"]))
        self.assertEqual(args.command, "run")
        self.assertEqual(args.files, ["some.pdf"])
        self.assertEqual(args.num_questions, 3)
        self.assertIs(args.func, main._cmd_run)


class RunCommandTests(unittest.TestCase):
    """`run` must thread files/theme/stop-after/eval into the engine call."""

    def test_run_passes_engine_kwargs(self) -> None:
        captured: dict = {}

        async def _fake_engine(**kwargs):
            captured.update(kwargs)
            return _fake_result()

        with (
            patch.object(main, "run_workflow_async", side_effect=_fake_engine) as engine,
            patch.object(main, "get_cli_openai_client", return_value=object()),
        ):
            code = main._cmd_run(
                main.build_parser().parse_args(
                    ["run", "one.pdf", "-n", "2", "--no-eval", "--stop-after", "generator"]
                )
            )
        self.assertEqual(code, 0)
        engine.assert_awaited_once()
        self.assertEqual(captured["file_paths"], ["one.pdf"])
        self.assertFalse(captured["run_evaluation"])
        self.assertEqual(captured["stop_after_agent"], "generator")
        # -n 2 is folded into the prompt sent to the normalizer.
        self.assertIn("Generate 2 questions per source", captured["prompt"])

    def test_run_rejects_unknown_theme(self) -> None:
        with patch.object(main, "get_cli_openai_client", return_value=object()):
            code = main._cmd_run(
                main.build_parser().parse_args(["run", "one.pdf", "--theme", "NotATheme"])
            )
        self.assertEqual(code, 1)

    def test_run_threads_valid_theme(self) -> None:
        captured: dict = {}

        async def _fake_engine(**kwargs):
            captured.update(kwargs)
            return _fake_result()

        with (
            patch.object(main, "run_workflow_async", side_effect=_fake_engine),
            patch.object(main, "get_cli_openai_client", return_value=object()),
        ):
            code = main._cmd_run(
                main.build_parser().parse_args(["run", "one.pdf", "--theme", "Physics"])
            )
        self.assertEqual(code, 0)
        self.assertEqual(captured["theme"], "Physics")


class RerunFromAgentTests(unittest.TestCase):
    """rerun-from-agent must thread resume_source_dir/label/from into the engine."""

    def test_threads_resume_args(self) -> None:
        captured: dict = {}

        async def _fake_engine(**kwargs):
            captured.update(kwargs)
            return _fake_result()

        source_dir = main.OUTPUTS_DIR / "06-30-2026" / "12-00-00"
        with (
            patch.object(main, "run_workflow_async", side_effect=_fake_engine),
            patch.object(main, "get_cli_openai_client", return_value=object()),
            patch.object(main, "_resolve_output_folder", return_value=source_dir),
            patch.object(main, "_relative_output_folder", return_value="06-30-2026/12-00-00"),
            patch(
                "backend.src.artifacts.load_source_file_paths",
                return_value=["/tmp/source.pdf"],
            ),
        ):
            code = main._cmd_rerun_from_agent(
                main.build_parser().parse_args(
                    ["rerun-from-agent", "--source", "06-30-2026/12-00-00", "--from", "generator"]
                )
            )
        self.assertEqual(code, 0)
        self.assertEqual(captured["resume_from_agent"], "generator")
        self.assertEqual(captured["resume_source_dir"], source_dir)
        self.assertEqual(captured["resume_source_label"], "06-30-2026/12-00-00")
        self.assertEqual(captured["file_paths"], ["/tmp/source.pdf"])

    def test_rejects_non_runnable_stage(self) -> None:
        """A guardrail/runtime node like `setup` is not a valid --from target.

        argparse rejects it at parse time because --from is choices=llm_agents().
        """
        with self.assertRaises(SystemExit):
            main.build_parser().parse_args(
                ["rerun-from-agent", "--source", "x", "--from", "setup"]
            )


class FromAgentInputTests(unittest.TestCase):
    """from-agent-input must schema-validate the payload before running."""

    def _write_payload(self, payload: dict) -> Path:
        import tempfile

        tmp = Path(tempfile.mkdtemp()) / "input.json"
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        return tmp

    def test_invalid_payload_rejected_before_engine(self) -> None:
        bad = self._write_payload({"not": "a valid verifier input"})
        with (
            patch.object(main, "run_workflow_async", new=AsyncMock()) as engine,
            patch.object(main, "get_cli_openai_client", return_value=object()),
        ):
            code = main._cmd_from_agent_input(
                main.build_parser().parse_args(
                    ["from-agent-input", "--from", "verifier", "--input", str(bad)]
                )
            )
        self.assertEqual(code, 1)
        engine.assert_not_awaited()

    def test_missing_file_rejected(self) -> None:
        with patch.object(main, "run_workflow_async", new=AsyncMock()) as engine:
            code = main._cmd_from_agent_input(
                main.build_parser().parse_args(
                    ["from-agent-input", "--from", "verifier", "--input", "/no/such/file.json"]
                )
            )
        self.assertEqual(code, 1)
        engine.assert_not_awaited()


class ListPromptShowZipTests(unittest.TestCase):
    """Introspection + read-only verbs must produce output without a client."""

    def _run(self, argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        args = main.build_parser().parse_args(argv)
        with redirect_stdout(buf):
            code = args.func(args)
        return code, buf.getvalue()

    def test_list_agents_includes_runtime_and_guardrail(self) -> None:
        code, out = self._run(["list", "agents"])
        self.assertEqual(code, 0)
        self.assertIn("setup", out)
        self.assertIn("output", out)
        self.assertIn("input_guardrail", out)

    def test_list_themes(self) -> None:
        code, out = self._run(["list", "themes"])
        self.assertEqual(code, 0)
        self.assertIn("Physics", out)

    def test_list_prompts(self) -> None:
        code, out = self._run(["list", "prompts"])
        self.assertEqual(code, 0)
        self.assertIn("generator", out)

    def test_list_rubric_is_json(self) -> None:
        code, out = self._run(["list", "rubric"])
        self.assertEqual(code, 0)
        parsed = json.loads(out)
        self.assertIn("model", parsed)

    def test_list_eval_files(self) -> None:
        code, out = self._run(["list", "eval-files"])
        self.assertEqual(code, 0)

    def test_prompt_by_key(self) -> None:
        code, out = self._run(["prompt", "generator"])
        self.assertEqual(code, 0)
        self.assertTrue(out.strip())

    def test_prompt_promptname_alias(self) -> None:
        """`evaluation` maps to its promptName (`evaluator`) prompt file."""
        code, out = self._run(["prompt", "evaluation"])
        self.assertEqual(code, 0)
        self.assertTrue(out.strip())

    def test_prompt_unknown_returns_error(self) -> None:
        code, _ = self._run(["prompt", "definitely_not_an_agent"])
        self.assertEqual(code, 1)

    def test_show_reads_final_result(self) -> None:
        import tempfile

        run_dir = Path(tempfile.mkdtemp())
        (run_dir / "final_result.json").write_text(
            json.dumps(
                {
                    "config": {"title": "T", "theme": "Physics"},
                    "source_questions": [{}, {}],
                    "verified_questions": [{}, {}],
                    "generated_questions": [{}],
                }
            ),
            encoding="utf-8",
        )
        buf = io.StringIO()
        with (
            patch.object(main, "_resolve_output_folder", return_value=run_dir),
            patch.object(main, "_relative_output_folder", return_value="run-x"),
        ):
            args = main.build_parser().parse_args(["show", "run-x"])
            with redirect_stdout(buf):
                code = args.func(args)
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("Source questions:    2", out)
        self.assertIn("Generated questions: 1", out)

    def test_zip_builds_archive(self) -> None:
        import tempfile

        run_dir = Path(tempfile.mkdtemp())
        (run_dir / "final_result.json").write_text("{}", encoding="utf-8")
        buf = io.StringIO()
        with (
            patch.object(main, "_resolve_output_folder", return_value=run_dir),
            patch.object(main, "_relative_output_folder", return_value=run_dir.name),
        ):
            args = main.build_parser().parse_args(["zip", run_dir.name])
            with redirect_stdout(buf):
                code = args.func(args)
        self.assertEqual(code, 0)
        self.assertTrue((run_dir / f"{run_dir.name}.zip").exists())


class HelpNoCredentialsTests(unittest.TestCase):
    """--help (and subcommand --help) must work with no OPENAI_API_KEY."""

    def test_top_level_help(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("OPENAI_API_KEY", None)
            with self.assertRaises(SystemExit) as ctx:
                main.build_parser().parse_args(["--help"])
            self.assertEqual(ctx.exception.code, 0)

    def test_subcommand_help(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            main.build_parser().parse_args(["run", "--help"])
        self.assertEqual(ctx.exception.code, 0)


class RegistryDrivenChoicesTests(unittest.TestCase):
    """Choices must derive from the registry, not hardcoded literals."""

    def test_from_choices_are_llm_agents(self) -> None:
        parser = main.build_parser()
        # A stoppable stop-after value must parse.
        args = parser.parse_args(["run", "x.pdf", "--stop-after", stoppable_agents()[0]])
        self.assertEqual(args.stop_after, stoppable_agents()[0])

    def test_rerun_from_accepts_every_llm_agent(self) -> None:
        parser = main.build_parser()
        for agent in llm_agents():
            args = parser.parse_args(["rerun-from-agent", "--source", "x", "--from", agent])
            self.assertEqual(args.from_agent, agent)


if __name__ == "__main__":
    unittest.main()
