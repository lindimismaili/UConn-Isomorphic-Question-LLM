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
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.src import main
from backend.src.api import state as api_state
from backend.src.registry import llm_agents, stoppable_agents
from backend.src.schemas import NormalizerOutput, WorkflowResult
from backend.src.tools import meta_evaluator


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


class BatchCommandTests(unittest.TestCase):
    """`batch` must delegate to state.run_create_data_batch_async and mirror

    its exit-code contract: 0 only when the batch reaches "completed" with no
    failed entries, 1 otherwise (including when there are no evaluation files).
    """

    def setUp(self) -> None:
        self._saved_batches = dict(api_state.batch_jobs)
        api_state.batch_jobs.clear()

    def tearDown(self) -> None:
        api_state.batch_jobs.clear()
        api_state.batch_jobs.update(self._saved_batches)

    def _fake_files(self, tmp: Path) -> list[Path]:
        f = tmp / "quiz.pdf"
        f.write_bytes(b"%PDF-1.4 x")
        return [f]

    def test_exits_zero_when_batch_completes_with_no_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            eval_dir = Path(tmp)
            files = self._fake_files(eval_dir)

            async def _fake_run(batch_id: str) -> None:
                batch = api_state.batch_jobs[batch_id]
                batch["status"] = "completed"
                batch["completedFiles"] = batch["totalFiles"]
                batch["failedFiles"] = 0
                batch["doneFiles"] = batch["totalFiles"]

            with (
                patch.object(main, "get_cli_openai_client", return_value=object()),
                patch.object(main, "EVALUATION_FIXTURES_DIR", eval_dir),
                patch.object(api_state, "_list_evaluation_source_files", return_value=files),
                patch.object(api_state, "run_create_data_batch_async", new=_fake_run),
            ):
                with self.assertRaises(SystemExit) as ctx:
                    main.main(["batch"])
            self.assertEqual(ctx.exception.code, 0)

    def test_exits_one_when_batch_has_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            eval_dir = Path(tmp)
            files = self._fake_files(eval_dir)

            async def _fake_run(batch_id: str) -> None:
                batch = api_state.batch_jobs[batch_id]
                batch["status"] = "completed"
                batch["completedFiles"] = 0
                batch["failedFiles"] = batch["totalFiles"]
                batch["doneFiles"] = batch["totalFiles"]

            with (
                patch.object(main, "get_cli_openai_client", return_value=object()),
                patch.object(main, "EVALUATION_FIXTURES_DIR", eval_dir),
                patch.object(api_state, "_list_evaluation_source_files", return_value=files),
                patch.object(api_state, "run_create_data_batch_async", new=_fake_run),
            ):
                with self.assertRaises(SystemExit) as ctx:
                    main.main(["batch"])
            self.assertEqual(ctx.exception.code, 1)

    def test_exits_one_when_no_evaluation_files(self) -> None:
        with (
            patch.object(main, "get_cli_openai_client", return_value=object()),
            patch.object(api_state, "_list_evaluation_source_files", return_value=[]),
        ):
            with self.assertRaises(SystemExit) as ctx:
                main.main(["batch"])
        self.assertEqual(ctx.exception.code, 1)


class EvalCommandTests(unittest.TestCase):
    """`eval` must delegate to state.run_eval_batch_async for both --runs and

    --pairs, mirroring the same "completed with no failures" exit-code
    contract as `batch`.
    """

    def setUp(self) -> None:
        self._saved_eval_batches = dict(api_state.eval_batch_jobs)
        api_state.eval_batch_jobs.clear()

    def tearDown(self) -> None:
        api_state.eval_batch_jobs.clear()
        api_state.eval_batch_jobs.update(self._saved_eval_batches)

    @staticmethod
    async def _fake_run_completed(batch_id: str) -> None:
        batch = api_state.eval_batch_jobs[batch_id]
        batch["status"] = "completed"
        batch["completedFiles"] = batch["totalFiles"]
        batch["failedFiles"] = 0
        batch["doneFiles"] = batch["totalFiles"]

    def test_runs_mode_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outputs_dir = Path(tmp)
            run_dir = outputs_dir / "run1"
            run_dir.mkdir()
            (run_dir / "final_result.json").write_text("{}", encoding="utf-8")

            with (
                patch.object(main, "get_cli_openai_client", return_value=object()),
                patch.object(main, "OUTPUTS_DIR", outputs_dir),
                patch.object(api_state, "run_eval_batch_async", new=self._fake_run_completed),
            ):
                with self.assertRaises(SystemExit) as ctx:
                    main.main(["eval", "--runs", "run1"])
            self.assertEqual(ctx.exception.code, 0)

    def test_runs_mode_missing_final_result_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outputs_dir = Path(tmp)
            (outputs_dir / "run-empty").mkdir()
            with (
                patch.object(main, "get_cli_openai_client", return_value=object()),
                patch.object(main, "OUTPUTS_DIR", outputs_dir),
            ):
                with self.assertRaises(SystemExit) as ctx:
                    main.main(["eval", "--runs", "run-empty"])
            self.assertEqual(ctx.exception.code, 1)

    def test_pairs_mode_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_pdf = root / "source.pdf"
            source_pdf.write_bytes(b"%PDF-1.4 s")
            gen_pdf = root / "gen.pdf"
            gen_pdf.write_bytes(b"%PDF-1.4 g")

            with (
                patch.object(main, "get_cli_openai_client", return_value=object()),
                patch.object(api_state, "run_eval_batch_async", new=self._fake_run_completed),
            ):
                with self.assertRaises(SystemExit) as ctx:
                    main.main(["eval", "--pairs", str(source_pdf), str(gen_pdf)])
            self.assertEqual(ctx.exception.code, 0)

    def test_pairs_mode_missing_path_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_pdf = Path(tmp) / "source.pdf"
            source_pdf.write_bytes(b"%PDF-1.4 s")
            missing_gen = Path(tmp) / "does-not-exist.pdf"
            with patch.object(main, "get_cli_openai_client", return_value=object()):
                with self.assertRaises(SystemExit) as ctx:
                    main.main(["eval", "--pairs", str(source_pdf), str(missing_gen)])
            self.assertEqual(ctx.exception.code, 1)


class ImproveCommandTests(unittest.TestCase):
    """`improve` must forward argv verbatim to the meta-evaluator's propose-only CLI."""

    def test_forwards_argv_and_exit_code(self) -> None:
        captured: dict = {}

        def _fake_main(argv: list[str]) -> int:
            captured["argv"] = argv
            return 0

        with patch.object(meta_evaluator, "main", side_effect=_fake_main):
            with self.assertRaises(SystemExit) as ctx:
                main.main(["improve", "--eval-dir", "X", "--labels", "Y", "--kappa-floor", "0.4"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(
            captured["argv"],
            ["--eval-dir", "X", "--labels", "Y", "--kappa-floor", "0.4"],
        )

    def test_nonzero_exit_propagates(self) -> None:
        with patch.object(meta_evaluator, "main", return_value=1):
            with self.assertRaises(SystemExit) as ctx:
                main.main(["improve", "--eval-dir", "X"])
        self.assertEqual(ctx.exception.code, 1)


class CompareProfessorCommandTests(unittest.TestCase):
    """`compare-professor` mirrors the retired script's exit codes: 2 for a
    missing batch/workbook, 3 for a comparison failure, 0 with a summary."""

    def test_success_prints_summary(self) -> None:
        from backend.src.tools import professor_comparison

        with tempfile.TemporaryDirectory() as tmp:
            batch_dir = Path(tmp)
            outcome = {
                "report": {"summary": {"mean_kappa": 0.5, "mean_raw_agreement": 0.9}},
                "xlsx_path": batch_dir / "XLSX" / "compiled.xlsx",
                "report_path": batch_dir / "XLSX" / "judge_agreement_report.json",
                "disagreements_path": batch_dir / "XLSX" / "judge_disagreements.csv",
                "pdfs_compared": ["entry-1"],
            }
            with (
                patch.object(main, "_resolve_output_folder", return_value=batch_dir),
                patch.object(
                    professor_comparison, "run_professor_comparison", return_value=outcome
                ) as run_mock,
            ):
                with self.assertRaises(SystemExit) as ctx:
                    main.main(["compare-professor", "batch-label", "--source", "physics_1_set"])
            self.assertEqual(ctx.exception.code, 0)
            self.assertEqual(run_mock.call_args.kwargs.get("source_stem"), "physics_1_set")

    def test_missing_batch_exits_two(self) -> None:
        with patch.object(main, "_resolve_output_folder", side_effect=FileNotFoundError("nope")):
            with self.assertRaises(SystemExit) as ctx:
                main.main(["compare-professor", "missing-batch"])
        self.assertEqual(ctx.exception.code, 2)

    def test_missing_workbook_exits_two(self) -> None:
        from backend.src.tools import professor_comparison

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(main, "_resolve_output_folder", return_value=Path(tmp)),
                patch.object(
                    professor_comparison,
                    "run_professor_comparison",
                    side_effect=FileNotFoundError("Professor workbook not found"),
                ),
            ):
                with self.assertRaises(SystemExit) as ctx:
                    main.main(["compare-professor", "batch-label"])
            self.assertEqual(ctx.exception.code, 2)

    def test_comparison_failure_exits_three(self) -> None:
        from backend.src.tools import professor_comparison

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(main, "_resolve_output_folder", return_value=Path(tmp)),
                patch.object(
                    professor_comparison,
                    "run_professor_comparison",
                    side_effect=professor_comparison.ProfessorComparisonError("no overlap"),
                ),
            ):
                with self.assertRaises(SystemExit) as ctx:
                    main.main(["compare-professor", "batch-label"])
            self.assertEqual(ctx.exception.code, 3)


if __name__ == "__main__":
    unittest.main()
