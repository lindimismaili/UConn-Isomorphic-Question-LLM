"""Hardening coverage for the workflow engine's Phase-1A refactor.

These tests exercise the new seams added to ``engine.py`` in isolation from a
full workflow run:

* ``run_llm_stage`` non-streaming, streaming (token callback), and error paths,
* lazy registry accessors + ``_max_turns`` defaults,
* the container-expiry heuristic,
* the guardrail-blocked finalize path (``_finalize_blocked`` + a blocking
  ``check_input_guardrails`` stub), which existing tests never hit because the
  real stubs always allow.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from agents.exceptions import MaxTurnsExceeded, ModelRefusalError

from backend.src.workflow import engine
from backend.src.workflow.guardrails import GuardrailDecision


def _delta_event(delta: str) -> SimpleNamespace:
    """Fake a raw_response_event carrying an output-text delta."""
    return SimpleNamespace(
        type="raw_response_event",
        data=SimpleNamespace(type="response.output_text.delta", delta=delta),
    )


def _noise_event() -> SimpleNamespace:
    """A non-delta event the stage must ignore."""
    return SimpleNamespace(type="run_item_stream_event", data=SimpleNamespace(type="other"))


class _FakeStreamResult:
    def __init__(self, events, final_output):
        self._events = events
        self.final_output = final_output

    async def stream_events(self):
        for event in self._events:
            yield event


class RunLlmStageTests(unittest.TestCase):
    def test_non_streaming_returns_final_output(self) -> None:
        agent = SimpleNamespace(name="Verifier")
        run_config = MagicMock()
        with patch.object(engine, "Runner") as runner:
            runner.run = AsyncMock(return_value=SimpleNamespace(final_output="OUT"))
            result = asyncio.run(
                engine.run_llm_stage(
                    agent,
                    "input",
                    run_config=run_config,
                    max_turns=6,
                    stage_label="verifier",
                )
            )
        self.assertEqual(result, "OUT")
        # No emit_token -> uses the plain Runner.run path, not run_streamed.
        runner.run.assert_awaited_once()
        self.assertEqual(runner.run.await_args.kwargs["max_turns"], 6)

    def test_streaming_forwards_deltas_and_returns_final_output(self) -> None:
        events = [_delta_event("Hel"), _noise_event(), _delta_event("lo")]
        stream = _FakeStreamResult(events, final_output="FINAL")
        seen: list[tuple[str, str]] = []
        with patch.object(engine, "Runner") as runner:
            runner.run_streamed = MagicMock(return_value=stream)
            result = asyncio.run(
                engine.run_llm_stage(
                    SimpleNamespace(name="Gen"),
                    "input",
                    run_config=MagicMock(),
                    max_turns=6,
                    stage_label="generator",
                    emit_token=lambda key, text: seen.append((key, text)),
                )
            )
        self.assertEqual(result, "FINAL")
        self.assertEqual(seen, [("generator", "Hel"), ("generator", "lo")])
        # run_streamed is called synchronously (not awaited).
        runner.run_streamed.assert_called_once()

    def test_streaming_callback_failure_never_breaks_the_run(self) -> None:
        stream = _FakeStreamResult([_delta_event("x")], final_output="OK")

        def _boom(_key, _text):
            raise RuntimeError("callback exploded")

        with patch.object(engine, "Runner") as runner:
            runner.run_streamed = MagicMock(return_value=stream)
            result = asyncio.run(
                engine.run_llm_stage(
                    SimpleNamespace(name="Gen"),
                    "input",
                    run_config=MagicMock(),
                    max_turns=6,
                    stage_label="generator",
                    emit_token=_boom,
                )
            )
        self.assertEqual(result, "OK")

    def test_sdk_errors_become_valueerror_with_stage_label(self) -> None:
        for exc in (MaxTurnsExceeded("too many"), ModelRefusalError("nope")):
            with self.subTest(exc=type(exc).__name__):
                with patch.object(engine, "Runner") as runner:
                    runner.run = AsyncMock(side_effect=exc)
                    with self.assertRaises(ValueError) as ctx:
                        asyncio.run(
                            engine.run_llm_stage(
                                SimpleNamespace(name="N"),
                                "input",
                                run_config=MagicMock(),
                                max_turns=6,
                                stage_label="normalizer",
                            )
                        )
                message = str(ctx.exception)
                self.assertIn("normalizer stage failed", message)
                self.assertIn(type(exc).__name__, message)


class LazyAccessorTests(unittest.TestCase):
    def test_max_turns_defaults(self) -> None:
        # No maxTurns in agents.json -> latexifier=12, others=6.
        self.assertEqual(engine._max_turns("latexifier"), 12)
        self.assertEqual(engine._max_turns("normalizer"), 6)
        self.assertEqual(engine._max_turns("generator"), 6)

    def test_default_resume_agent_and_flow(self) -> None:
        self.assertEqual(engine._default_resume_agent(), "normalizer")
        flow = engine._agent_flow()
        self.assertIn("normalizer", flow)
        self.assertEqual(len(flow["normalizer"]), 2)

    def test_eval_reasoning_from_registry(self) -> None:
        self.assertIsInstance(engine._eval_reasoning(), str)
        self.assertTrue(engine._eval_reasoning())


class ContainerExpiryHeuristicTests(unittest.TestCase):
    def test_detects_container_errors(self) -> None:
        self.assertTrue(engine._is_container_expired_error(RuntimeError("Container cntr_x has expired")))
        self.assertTrue(engine._is_container_expired_error(RuntimeError("container not found")))
        err = SimpleNamespace(status_code=404)
        exc = type("HttpErr", (Exception,), {})("container cntr_1 does not exist")
        exc.status_code = 404
        self.assertTrue(engine._is_container_expired_error(exc))
        self.assertIs(err.status_code, 404)

    def test_ignores_unrelated_errors(self) -> None:
        self.assertFalse(engine._is_container_expired_error(ValueError("bad latex syntax")))
        self.assertFalse(engine._is_container_expired_error(RuntimeError("network timeout")))


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


class BlockedInputFinalizeTests(unittest.TestCase):
    def test_blocked_input_finalizes_without_creating_container(self) -> None:
        blocked = GuardrailDecision(
            allowed=False,
            category="moderation",
            reason="disallowed content",
            stage="input",
        )
        client = MagicMock()
        workflow = _new_workflow_record()
        with tempfile.TemporaryDirectory() as tmp:
            outputs_dir = Path(tmp) / "blocked"
            with patch.object(
                engine, "check_input_guardrails", AsyncMock(return_value=blocked)
            ):
                result = asyncio.run(
                    engine.run_workflow_async(
                        client=client,
                        workflow_id="wf-blocked",
                        workflow=workflow,
                        prompt="do something bad",
                        file_paths=[],
                        outputs_dir=outputs_dir,
                        output_folder_label=outputs_dir.name,
                        touch_workflow=lambda _w: None,
                        to_thread=_to_thread,
                        load_rubric_model=lambda: "gpt-test",
                        load_rubric_payload=lambda: {"criteria": []},
                    )
                )
            self.assertEqual(workflow["status"], "blocked")
            self.assertEqual(
                workflow["blocked"],
                {"category": "moderation", "reason": "disallowed content", "stage": "input"},
            )
            # The container was never created (blocked before container.create).
            client.containers.create.assert_not_called()
            # A minimal final_result.json was written and stages were cancelled.
            self.assertTrue((outputs_dir / "final_result.json").exists())
            self.assertEqual(workflow["agents"]["normalizer"]["status"], "cancelled")
            self.assertEqual(result.source_questions, [])
            self.assertEqual(result.generated_questions, [])


if __name__ == "__main__":
    unittest.main()
