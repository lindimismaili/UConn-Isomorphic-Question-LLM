"""Mocked integration coverage for the shared workflow engine.

The engine's ``run_workflow_async`` drives the whole 6-stage pipeline and had no
execution-level test. These tests stub every OpenAI/container seam (agents,
Runner, container runtime, evaluation) and exercise three control-flow families:

* a full fresh run (normalizer -> ... -> evaluation),
* a resume-from-cache run (upstream stages reused from a prior run's artifacts),
* an input-to-agent run (upstream context coerced from an uploaded payload).

They assert the engine reaches ``completed`` and produces a well-formed
``WorkflowResult`` so the large function can be refactored safely.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from backend.src.schemas import (
    ExtracterOutput,
    GeneratorOutput,
    LaTeXOutput,
    NewQuestionItem,
    NormalizerOutput,
    OptionItem,
    SourceQuestionItem,
    VerifierOutput,
)
from backend.src.workflow import engine


def _source_question() -> SourceQuestionItem:
    return SourceQuestionItem(
        source_id="S1",
        source_raw="What is 2 + 2?",
        stem="What is 2 + 2?",
        options=[
            OptionItem(key="A", text="4", is_correct=True, explanation="2+2=4", distractor_logic="generic_plausible_value"),
            OptionItem(key="B", text="5", is_correct=False, explanation="off by one", distractor_logic="off_by_one"),
        ],
        has_explicit_answer=True,
        question_type="multiple_choice",
        tags=["arithmetic"],
        has_figure=False,
        answer="A",
    )


def _generated_question() -> NewQuestionItem:
    return NewQuestionItem(
        id=1,
        source_id="S1",
        stem="What is 3 + 3?",
        options=[
            OptionItem(key="A", text="6", is_correct=True, explanation="3+3=6", distractor_logic="generic_plausible_value"),
            OptionItem(key="B", text="7", is_correct=False, explanation="off by one", distractor_logic="off_by_one"),
        ],
        tags=["arithmetic"],
        generation_notes="varied numbers",
        question_type="multiple_choice",
        answer="A",
    )


def _stage_outputs() -> dict[str, object]:
    src = _source_question()
    return {
        "normalizer": NormalizerOutput(new_questions_per_question=1),
        "extracter": ExtracterOutput(filename="quiz.pdf", title="Quiz", theme="physics_abstract", questions=[src]),
        "verifier": VerifierOutput(questions=[src]),
        "generator": GeneratorOutput(isomorphic_questions=[_generated_question()]),
        "latexifier": LaTeXOutput(
            latex_content="\\documentclass{article}\\begin{document}Q\\end{document}",
            filename="isomorphic_questions.tex",
            pdf_filename="isomorphic_questions.pdf",
        ),
    }


class _FakeRuntime:
    """Stand-in for SharedWorkflowRuntime that writes a fake PDF locally."""

    def __init__(self, *, client, container_id, output_dir, allowed_domains=None, **_kw):
        self.output_dir = Path(output_dir)

    def bootstrap(self):
        return "bootstrapped"

    def build_compile_latex_tool(self):
        return None

    def compile_latex(self, latex_source, output_basename):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        pdf = self.output_dir / f"{output_basename}.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        tex = self.output_dir / f"{output_basename}.tex"
        tex.write_text(latex_source, encoding="utf-8")
        return SimpleNamespace(
            pdf_filename=pdf.name, tex_filename=tex.name, pdf_path=pdf, tex_path=tex, response_id="resp"
        )


def _fake_agents(**_kwargs) -> dict[str, object]:
    return {
        stage: SimpleNamespace(
            stage=stage,
            name=stage.title(),
            model="gpt-test",
            model_settings=SimpleNamespace(reasoning=None),
        )
        for stage in ("normalizer", "extracter", "verifier", "generator", "latexifier")
    }


def _new_workflow_record() -> dict:
    keys = ("normalizer", "extracter", "verifier", "generator", "latexifier", "evaluation", "output")
    return {
        "status": "pending",
        "outputFolder": "",
        "container": {"status": "idle", "id": "", "allowedDomains": []},
        "agents": {k: {"status": "idle", "data": {}} for k in keys},
        "files": [],
        "result": None,
    }


async def _to_thread(func, /, *args, **kwargs):
    return func(*args, **kwargs)


class EngineWorkflowTests(unittest.TestCase):
    def _run(self, *, outputs_dir, workflow, **kwargs):
        outputs = _stage_outputs()

        async def runner_side_effect(agent, *_a, **_k):
            return SimpleNamespace(final_output=outputs[agent.stage])

        client = MagicMock()
        client.containers.create.return_value.id = "cntr_test"

        with (
            patch.object(engine, "create_agents", _fake_agents),
            patch.object(engine, "SharedWorkflowRuntime", _FakeRuntime),
            patch.object(engine, "upload_file_to_openai", return_value="file_test"),
            patch.object(
                engine, "run_openai_output_evaluation", return_value={"status": "complete", "overall_score": 1.0}
            ),
            patch.object(engine, "Runner") as runner,
        ):
            runner.run = AsyncMock(side_effect=runner_side_effect)
            return asyncio.run(
                engine.run_workflow_async(
                    client=client,
                    workflow_id="wf-test",
                    workflow=workflow,
                    prompt="make variants",
                    file_paths=[],
                    outputs_dir=outputs_dir,
                    output_folder_label=outputs_dir.name,
                    touch_workflow=lambda _w: None,
                    to_thread=_to_thread,
                    load_rubric_model=lambda: "gpt-test",
                    load_rubric_payload=lambda: {"model": "gpt-test", "criteria": []},
                    **kwargs,
                )
            )

    def _assert_complete(self, workflow, result) -> None:
        self.assertEqual(workflow["status"], "completed")
        self.assertEqual(len(result.source_questions), 1)
        self.assertEqual(len(result.verified_questions), 1)
        self.assertEqual(len(result.generated_questions), 1)
        self.assertEqual(result.latex_file, "isomorphic_questions.tex")
        self.assertTrue(result.pdf_file)

    def test_full_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outputs_dir = Path(tmp) / "run1"
            workflow = _new_workflow_record()
            result = self._run(outputs_dir=outputs_dir, workflow=workflow)
            self._assert_complete(workflow, result)
            self.assertTrue((outputs_dir / "final_result.json").exists())
            self.assertEqual(result.evaluation, {"status": "complete", "overall_score": 1.0})
            for key in ("normalizer", "extracter", "verifier", "generator", "latexifier", "output"):
                self.assertEqual(workflow["agents"][key]["status"], "completed")

    def test_resume_from_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            self._run(outputs_dir=source_dir, workflow=_new_workflow_record())

            resume_dir = Path(tmp) / "resumed"
            workflow = _new_workflow_record()
            result = self._run(
                outputs_dir=resume_dir,
                workflow=workflow,
                resume_from_agent="generator",
                resume_source_dir=source_dir,
                resume_source_label="source",
            )
            self._assert_complete(workflow, result)
            # Upstream stages were reused from cache, not re-run.
            self.assertEqual(workflow["agents"]["normalizer"]["data"].get("receivedFrom"), "cached")
            self.assertEqual(workflow["agents"]["verifier"]["data"].get("receivedFrom"), "cached")

    def test_input_to_agent(self) -> None:
        src = _source_question()
        payload = {
            "task": "generate_isomorphic_questions",
            "configuration": NormalizerOutput(new_questions_per_question=1).model_dump(),
            "source_questions": {"questions": [src.model_dump()]},
        }
        with tempfile.TemporaryDirectory() as tmp:
            outputs_dir = Path(tmp) / "input_to_agent"
            workflow = _new_workflow_record()
            result = self._run(
                outputs_dir=outputs_dir,
                workflow=workflow,
                resume_from_agent="generator",
                agent_input_override=payload,
                agent_input_filename="generator_input.json",
                input_to_agent_run=True,
            )
            self._assert_complete(workflow, result)
            # Upstream context was derived from the uploaded payload.
            self.assertEqual(workflow["agents"]["extracter"]["data"].get("receivedFrom"), "uploaded_input")


if __name__ == "__main__":
    unittest.main()
