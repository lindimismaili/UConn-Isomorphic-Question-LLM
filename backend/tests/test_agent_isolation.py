"""Per-agent isolation tests using the stop_after_agent engine parameter.

Each test runs exactly one agent (via stop_after_agent) with a known mocked
Runner response and validates the partial WorkflowResult that comes back.
The tests use fixture JSON files from backend/data/testing/agent_inputs/ where
available, and in-memory payloads where the fixture format is simpler to build.

No real OpenAI API calls are made — all Runner.run calls are mocked.
"""

from __future__ import annotations

import asyncio
import json
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

# ---------- shared fixtures ----------

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "data" / "testing" / "agent_inputs"


def _mc_option(key: str, text: str, *, correct: bool, logic: str = "") -> OptionItem:
    return OptionItem(
        key=key,
        text=text,
        is_correct=correct,
        explanation=f"Explanation for {key}",
        # Verifier validation requires distractor_logic on all options, even correct ones.
        distractor_logic=logic or ("generic_plausible_value" if not correct else "correct_answer"),
    )


def _source_q(source_id: str = "S1") -> SourceQuestionItem:
    return SourceQuestionItem(
        source_id=source_id,
        source_raw="What is 1 + 1?",
        stem="What is 1 + 1?",
        options=[
            _mc_option("A", "2", correct=True),
            _mc_option("B", "3", correct=False),
        ],
        has_explicit_answer=True,
        question_type="multiple_choice",
        topic_tags=["arithmetic"],
        answer="A",
    )


def _generated_q(source_id: str = "S1") -> NewQuestionItem:
    return NewQuestionItem(
        id=1,
        source_id=source_id,
        stem="What is 2 + 2?",
        options=[
            _mc_option("A", "4", correct=True),
            _mc_option("B", "5", correct=False),
        ],
        topic_tags=["arithmetic"],
        generation_notes="varied numbers",
        question_type="multiple_choice",
        answer="A",
    )


# ---------- shared engine test helpers ----------

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
        pdf.write_bytes(b"%PDF-1.4 test")
        tex = self.output_dir / f"{output_basename}.tex"
        tex.write_text(latex_source, encoding="utf-8")
        return SimpleNamespace(
            pdf_filename=pdf.name,
            tex_filename=tex.name,
            pdf_path=pdf,
            tex_path=tex,
            response_id="resp",
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


def _workflow_record() -> dict:
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


def _run_isolated(
    *,
    agent: str,
    agent_input: dict,
    mock_outputs: dict[str, object],
    outputs_dir: Path,
) -> object:
    """Run a single agent in isolation and return the partial WorkflowResult."""
    workflow = _workflow_record()

    async def runner_side_effect(ag, *_a, **_k):
        stage = ag.stage
        if stage not in mock_outputs:
            raise AssertionError(f"Unexpected Runner.run call for agent '{stage}'")
        return SimpleNamespace(final_output=mock_outputs[stage])

    client = MagicMock()
    client.containers.create.return_value.id = "cntr_isolation"

    with (
        patch.object(engine, "create_agents", _fake_agents),
        patch.object(engine, "SharedWorkflowRuntime", _FakeRuntime),
        patch.object(engine, "upload_file_to_openai", return_value="fid_isolation"),
        patch.object(engine, "run_openai_output_evaluation", return_value={}),
        patch.object(engine, "Runner") as runner,
    ):
        runner.run = AsyncMock(side_effect=runner_side_effect)
        return asyncio.run(
            engine.run_workflow_async(
                client=client,
                workflow_id=f"isolation-{agent}",
                workflow=workflow,
                prompt="",
                file_paths=[],
                outputs_dir=outputs_dir,
                output_folder_label=outputs_dir.name,
                touch_workflow=lambda _: None,
                to_thread=_to_thread,
                load_rubric_model=lambda: "gpt-test",
                load_rubric_payload=lambda: {},
                resume_from_agent=agent,
                agent_input_override=agent_input,
                agent_input_filename="fixture.json",
                input_to_agent_run=True,
                run_evaluation=False,
                stop_after_agent=agent,
            )
        )


# ---------- tests ----------

class TestNormalizerIsolation(unittest.TestCase):
    def test_normalizer_returns_config(self) -> None:
        fixture_path = FIXTURE_DIR / "normalizer" / "basic_prompt.json"
        self.assertTrue(fixture_path.exists(), f"Fixture not found: {fixture_path}")
        agent_input = json.loads(fixture_path.read_text(encoding="utf-8"))
        expected = NormalizerOutput(new_questions_per_question=2, difficulty="medium")

        with tempfile.TemporaryDirectory() as tmp:
            result = _run_isolated(
                agent="normalizer",
                agent_input=agent_input,
                mock_outputs={"normalizer": expected},
                outputs_dir=Path(tmp) / "out",
            )

        self.assertEqual(result.config.new_questions_per_question, 2)
        self.assertEqual(result.config.difficulty, "medium")
        # Early stop → no downstream outputs
        self.assertEqual(result.source_questions, [])
        self.assertEqual(result.generated_questions, [])

    def test_normalizer_fixture_is_valid_schema(self) -> None:
        """Fixture parses against NormalizerInput without errors."""
        from backend.src.schemas import NormalizerInput
        fixture_path = FIXTURE_DIR / "normalizer" / "basic_prompt.json"
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        NormalizerInput.model_validate(data)  # must not raise


class TestExtracterIsolation(unittest.TestCase):
    def test_extracter_returns_source_questions(self) -> None:
        src = _source_q("Q1")
        agent_input = {
            "task": "extract_source_questions",
            "files": ["quiz.pdf"],
            "normalizer_config": NormalizerOutput(new_questions_per_question=1).model_dump(),
            "requirements": {},
        }
        expected = ExtracterOutput(
            filename="quiz.pdf",
            title="Physics Quiz",
            theme="physics",
            questions=[src],
        )

        with tempfile.TemporaryDirectory() as tmp:
            result = _run_isolated(
                agent="extracter",
                agent_input=agent_input,
                mock_outputs={"extracter": expected},
                outputs_dir=Path(tmp) / "out",
            )

        self.assertEqual(len(result.source_questions), 1)
        self.assertEqual(result.source_questions[0].source_id, "Q1")
        self.assertEqual(result.verified_questions, [])
        self.assertEqual(result.generated_questions, [])

    def test_extracter_fixture_is_valid_schema(self) -> None:
        from backend.src.schemas import ExtracterInput
        fixture_path = FIXTURE_DIR / "extracter" / "from_normalizer.json"
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        ExtracterInput.model_validate(data)


class TestVerifierIsolation(unittest.TestCase):
    def test_verifier_returns_verified_questions(self) -> None:
        src = _source_q("V1")
        agent_input = {
            "task": "verify_source_questions",
            "source_questions": ExtracterOutput(
                filename="quiz.pdf",
                questions=[src],
            ).model_dump(),
            "requirements": {},
        }
        expected_verified = VerifierOutput(questions=[src])

        with tempfile.TemporaryDirectory() as tmp:
            result = _run_isolated(
                agent="verifier",
                agent_input=agent_input,
                mock_outputs={"verifier": expected_verified},
                outputs_dir=Path(tmp) / "out",
            )

        self.assertEqual(len(result.verified_questions), 1)
        self.assertEqual(result.verified_questions[0].source_id, "V1")
        self.assertEqual(result.generated_questions, [])

    def test_verifier_fixture_is_valid_schema(self) -> None:
        from backend.src.schemas import VerifierInput
        fixture_path = FIXTURE_DIR / "verifier" / "verbose_open_ended_and_multipart.json"
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        VerifierInput.model_validate(data)


class TestGeneratorIsolation(unittest.TestCase):
    def test_generator_returns_generated_questions(self) -> None:
        src = _source_q("G1")
        gen = _generated_q("G1")
        agent_input = {
            "task": "generate_isomorphic_questions",
            "configuration": NormalizerOutput(new_questions_per_question=1).model_dump(),
            "source_questions": VerifierOutput(questions=[src]).model_dump(),
            "requirements": {},
        }
        expected = GeneratorOutput(isomorphic_questions=[gen])

        with tempfile.TemporaryDirectory() as tmp:
            result = _run_isolated(
                agent="generator",
                agent_input=agent_input,
                # Generator calls Runner.run twice (generation + style review) — same output for both
                mock_outputs={"generator": expected},
                outputs_dir=Path(tmp) / "out",
            )

        self.assertEqual(len(result.generated_questions), 1)
        self.assertEqual(result.generated_questions[0].source_id, "G1")

    def test_generator_fixture_is_valid_schema(self) -> None:
        from backend.src.schemas import GeneratorInput
        fixture_path = FIXTURE_DIR / "generator" / "verbosity_preservation_open_ended.json"
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        GeneratorInput.model_validate(data)


class TestLatexifierIsolation(unittest.TestCase):
    def test_latexifier_returns_latex_file(self) -> None:
        src = _source_q("L1")
        gen = _generated_q("L1")
        agent_input = {
            "task": "format_questions_to_latex",
            "document": {"title": "Test", "theme": "physics", "source_title": "Quiz", "course": "PHYS101"},
            "questions": [
                {
                    "source_id": "L1",
                    "stem": gen.stem,
                    "options": [o.model_dump() for o in gen.options],
                    "question_type": "multiple_choice",
                    "skipped": False,
                    "skip_reason": "",
                    "answer": "A",
                    "parts": [],
                }
            ],
            "requirements": {},
            # Include source and generated questions so coerce functions can build upstream state.
            "source_questions": ExtracterOutput(filename="quiz.pdf", questions=[src]).model_dump(),
            "generated_questions": [gen.model_dump()],
        }
        expected_latex = LaTeXOutput(
            latex_content="\\documentclass{article}\\begin{document}Q\\end{document}",
            filename="isomorphic_questions.tex",
            pdf_filename="isomorphic_questions.pdf",
        )

        with tempfile.TemporaryDirectory() as tmp:
            result = _run_isolated(
                agent="latexifier",
                agent_input=agent_input,
                mock_outputs={"latexifier": expected_latex},
                outputs_dir=Path(tmp) / "out",
            )

        self.assertTrue(result.latex_file.endswith(".tex"))

    def test_latexifier_fixture_is_valid_schema(self) -> None:
        from backend.src.schemas import LaTeXInput
        fixture_path = FIXTURE_DIR / "latex" / "mixed_question_types.json"
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        LaTeXInput.model_validate(data)


if __name__ == "__main__":
    unittest.main()
