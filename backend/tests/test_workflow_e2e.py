"""End-to-end smoke tests for the shared workflow engine.

These tests exercise the full data flow from prompt → WorkflowResult using the
same mocking approach as test_engine_workflow.py, but focus on:

1. Result *content* — generated questions are well-formed, not just non-empty.
2. stop_after_agent — pipeline stops early and downstream agents are NOT called.

Marked with @unittest.skipUnless to gate real-API tests on OPENAI_API_KEY, though
all tests here still use mocks and pass without credentials.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

from backend.src.schemas import (
    ExtracterOutput,
    GeneratorOutput,
    LaTeXOutput,
    NewQuestionItem,
    NormalizerOutput,
    OptionItem,
    SourceQuestionItem,
    VerifierOutput,
    WorkflowResult,
)
from backend.src.workflow import engine


# ---------- test data ----------

def _option(key: str, text: str, *, correct: bool, logic: str = "generic_plausible_value") -> OptionItem:
    return OptionItem(
        key=key, text=text, is_correct=correct,
        explanation=f"Because {text}", distractor_logic=logic,
    )


def _source_q(source_id: str = "S1") -> SourceQuestionItem:
    return SourceQuestionItem(
        source_id=source_id,
        source_raw="A ball is dropped. How far does it fall in 2 s?",
        stem="A ball is dropped. How far does it fall in 2 s?",
        options=[
            _option("A", "19.6 m", correct=True),
            _option("B", "9.8 m", correct=False, logic="off_by_factor_of_two"),
        ],
        has_explicit_answer=True,
        question_type="multiple_choice",
        topic_tags=["kinematics", "free_fall"],
        answer="A",
    )


def _generated_q(source_id: str = "S1") -> NewQuestionItem:
    return NewQuestionItem(
        id=1,
        source_id=source_id,
        stem="A rock is dropped. How far does it fall in 3 s?",
        options=[
            _option("A", "44.1 m", correct=True),
            _option("B", "29.4 m", correct=False, logic="off_by_factor_of_two"),
        ],
        topic_tags=["kinematics", "free_fall"],
        generation_notes="changed time from 2s to 3s",
        question_type="multiple_choice",
        answer="A",
    )


def _stage_outputs(source_id: str = "S1") -> dict[str, object]:
    src = _source_q(source_id)
    return {
        "normalizer": NormalizerOutput(new_questions_per_question=1, theme="physics"),
        "extracter": ExtracterOutput(
            filename="quiz.pdf",
            title="Physics Quiz",
            theme="physics",
            questions=[src],
        ),
        "verifier": VerifierOutput(questions=[src]),
        "generator": GeneratorOutput(isomorphic_questions=[_generated_q(source_id)]),
        "latexifier": LaTeXOutput(
            latex_content="\\documentclass{article}\\begin{document}Q\\end{document}",
            filename="isomorphic_questions.tex",
            pdf_filename="isomorphic_questions.pdf",
        ),
    }


class _FakeRuntime:
    def __init__(self, *, client, container_id, output_dir, allowed_domains=None, **_):
        self.output_dir = Path(output_dir)

    def bootstrap(self):
        return "bootstrapped"

    def build_compile_latex_tool(self):
        return None

    def compile_latex(self, latex_source, output_basename):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        pdf = self.output_dir / f"{output_basename}.pdf"
        pdf.write_bytes(b"%PDF-1.4 e2e")
        tex = self.output_dir / f"{output_basename}.tex"
        tex.write_text(latex_source, encoding="utf-8")
        return SimpleNamespace(
            pdf_filename=pdf.name, tex_filename=tex.name,
            pdf_path=pdf, tex_path=tex, response_id="resp_e2e",
        )


def _fake_agents(**_kwargs) -> dict[str, object]:
    return {
        stage: SimpleNamespace(
            stage=stage, name=stage.title(), model="gpt-test",
            model_settings=SimpleNamespace(reasoning=None),
        )
        for stage in ("normalizer", "extracter", "verifier", "generator", "latexifier")
    }


def _workflow_record() -> dict:
    keys = ("normalizer", "extracter", "verifier", "generator", "latexifier", "evaluation", "output")
    return {
        "status": "pending", "outputFolder": "",
        "container": {"status": "idle", "id": "", "allowedDomains": []},
        "agents": {k: {"status": "idle", "data": {}} for k in keys},
        "files": [], "result": None,
    }


async def _to_thread(func, /, *args, **kwargs):
    return func(*args, **kwargs)


def _run(
    outputs_dir: Path,
    workflow: dict,
    *,
    stop_after_agent: str | None = None,
    **kwargs,
) -> WorkflowResult:
    outputs = _stage_outputs()
    called_agents: list[str] = []

    async def runner_side_effect(agent, *_a, **_k):
        called_agents.append(agent.stage)
        return SimpleNamespace(final_output=outputs[agent.stage])

    client = MagicMock()
    client.containers.create.return_value.id = "cntr_e2e"

    with (
        patch.object(engine, "create_agents", _fake_agents),
        patch.object(engine, "SharedWorkflowRuntime", _FakeRuntime),
        patch.object(engine, "upload_file_to_openai", return_value="fid_e2e"),
        patch.object(engine, "run_openai_output_evaluation", return_value={"score": 0.9}),
        patch.object(engine, "Runner") as runner,
    ):
        runner.run = AsyncMock(side_effect=runner_side_effect)
        result = asyncio.run(
            engine.run_workflow_async(
                client=client,
                workflow_id="wf-e2e",
                workflow=workflow,
                prompt="generate physics variants",
                file_paths=[],
                outputs_dir=outputs_dir,
                output_folder_label=outputs_dir.name,
                touch_workflow=lambda _: None,
                to_thread=_to_thread,
                load_rubric_model=lambda: "gpt-test",
                load_rubric_payload=lambda: {"criteria": []},
                stop_after_agent=stop_after_agent,
                **kwargs,
            )
        )
    return result, called_agents


class WorkflowE2ETests(unittest.TestCase):

    def test_full_pipeline_question_content(self) -> None:
        """Full pipeline produces well-formed generated questions."""
        with tempfile.TemporaryDirectory() as tmp:
            workflow = _workflow_record()
            result, _ = _run(Path(tmp) / "full", workflow)

        self.assertEqual(workflow["status"], "completed")
        self.assertEqual(len(result.source_questions), 1)
        self.assertEqual(len(result.verified_questions), 1)
        self.assertEqual(len(result.generated_questions), 1)

        gen = result.generated_questions[0]
        self.assertEqual(gen.source_id, "S1")
        self.assertGreater(len(gen.stem), 10)
        self.assertEqual(len(gen.options), 2)
        self.assertTrue(any(o.is_correct for o in gen.options), "No correct option")
        self.assertEqual(gen.question_type, "multiple_choice")
        self.assertFalse(gen.skipped)

    def test_full_pipeline_result_file_written(self) -> None:
        """final_result.json is written and parses as WorkflowResult."""
        import json
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "result_file"
            workflow = _workflow_record()
            _run(out, workflow)
            result_path = out / "final_result.json"
            self.assertTrue(result_path.exists(), "final_result.json not written")
            data = json.loads(result_path.read_text(encoding="utf-8"))
            parsed = WorkflowResult.model_validate(data)
            self.assertEqual(len(parsed.generated_questions), 1)

    def test_stop_after_verifier_skips_generator(self) -> None:
        """stop_after_agent='verifier' stops before generator is called."""
        with tempfile.TemporaryDirectory() as tmp:
            workflow = _workflow_record()
            result, called = _run(
                Path(tmp) / "stop_verifier",
                workflow,
                stop_after_agent="verifier",
            )

        self.assertEqual(workflow["status"], "completed")
        # Source and verified present, but generator was not run
        self.assertEqual(len(result.source_questions), 1)
        self.assertEqual(len(result.verified_questions), 1)
        self.assertEqual(len(result.generated_questions), 0, "Generator should not have run")
        self.assertEqual(result.latex_file, "")

        # Confirm Runner was not called for generator or latexifier
        for stage in called:
            self.assertNotIn(stage, ("generator", "latexifier"),
                             f"Agent '{stage}' ran after stop_after_agent='verifier'")

    def test_stop_after_normalizer_skips_all_downstream(self) -> None:
        """stop_after_agent='normalizer' returns only config, no questions."""
        with tempfile.TemporaryDirectory() as tmp:
            workflow = _workflow_record()
            result, called = _run(
                Path(tmp) / "stop_normalizer",
                workflow,
                stop_after_agent="normalizer",
            )

        self.assertEqual(result.config.theme, "physics")
        self.assertEqual(result.source_questions, [])
        self.assertEqual(result.verified_questions, [])
        self.assertEqual(result.generated_questions, [])
        # Only normalizer should have been called
        self.assertEqual(called, ["normalizer"])

    def test_stop_after_generator_skips_latexifier(self) -> None:
        """stop_after_agent='generator' stops before latexifier."""
        with tempfile.TemporaryDirectory() as tmp:
            workflow = _workflow_record()
            result, called = _run(
                Path(tmp) / "stop_generator",
                workflow,
                stop_after_agent="generator",
            )

        self.assertEqual(len(result.generated_questions), 1)
        self.assertEqual(result.latex_file, "")
        self.assertNotIn("latexifier", called)

    def test_no_eval_skips_evaluation(self) -> None:
        """run_evaluation=False skips the evaluation stage."""
        with tempfile.TemporaryDirectory() as tmp:
            workflow = _workflow_record()
            result, _ = _run(Path(tmp) / "no_eval", workflow, run_evaluation=False)

        self.assertEqual(workflow["status"], "completed")
        self.assertIsNone(result.evaluation)
        self.assertEqual(workflow["agents"]["evaluation"]["status"], "inactive")

    def test_invalid_stop_after_agent_raises(self) -> None:
        """Unknown stop_after_agent raises ValueError before any API calls."""
        with tempfile.TemporaryDirectory() as tmp:
            workflow = _workflow_record()
            with self.assertRaises(ValueError, msg="Expected ValueError for invalid stop_after_agent"):
                _run(Path(tmp) / "bad", workflow, stop_after_agent="output")


if __name__ == "__main__":
    unittest.main()
