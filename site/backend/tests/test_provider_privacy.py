"""Provider-retention defaults for agent and container calls."""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.src import container, helpers
from backend.src.schemas import AGENT_OUTPUT_TYPES
from backend.src.workflow import agents as workflow_agents
from backend.src.workflow import guardrails


def test_every_workflow_agent_disables_provider_response_storage() -> None:
    created = workflow_agents.create_agents(container_id="cntr_owned")

    assert set(created) == set(AGENT_OUTPUT_TYPES)
    assert all(agent.model_settings.store is False for agent in created.values())


def test_every_workflow_agent_has_registry_owned_output_token_cap() -> None:
    created = workflow_agents.create_agents(container_id="cntr_owned")

    assert {
        key: agent.model_settings.max_tokens
        for key, agent in created.items()
    } == {
        "normalizer": 2048,
        "extracter": 32768,
        "verifier": 32768,
        "generator": 65536,
        "latexifier": 65536,
    }


def test_code_interpreter_agents_reject_unowned_auto_container_by_default() -> None:
    with pytest.raises(ValueError, match="explicit owned container_id"):
        workflow_agents.create_agents()


def test_code_interpreter_tool_binds_only_to_the_explicit_owned_container() -> None:
    created = workflow_agents.create_agents(
        container_id="cntr_owned",
        agent_keys={"extracter"},
    )

    assert created["extracter"].tools[0].tool_config["container"] == "cntr_owned"


def test_pre_guardrail_agent_set_has_no_implicit_code_interpreter() -> None:
    created = workflow_agents.create_agents(code_interpreter_enabled=False)

    assert created["extracter"].tools == []
    assert created["verifier"].tools == []
    assert created["generator"].tools == []


def test_container_scopes_build_only_requested_agents() -> None:
    extraction = workflow_agents.create_agents(
        container_id="cntr_extract",
        agent_keys={"extracter"},
    )
    downstream = workflow_agents.create_agents(
        container_id="cntr_downstream",
        agent_keys={"verifier", "generator", "latexifier"},
    )

    assert set(extraction) == {"extracter"}
    assert set(downstream) == {"verifier", "generator", "latexifier"}
    assert "extracter" not in downstream


def test_guardrail_classifier_disables_provider_response_storage() -> None:
    assert guardrails._reasoning_settings().store is False


def test_guardrail_classifier_has_registry_owned_output_token_cap() -> None:
    assert guardrails._reasoning_settings().max_tokens == 2048


def test_container_shell_response_disables_provider_storage() -> None:
    client = MagicMock()
    client.responses.create.return_value = SimpleNamespace(id="resp_test")

    container._run_shell_task(
        client,
        model="gpt-test",
        container_id="cntr_test",
        prompt="echo safe",
    )

    assert client.responses.create.call_args.kwargs["store"] is False
    assert client.responses.create.call_args.kwargs["timeout"] == 600
    assert client.responses.create.call_args.kwargs["max_output_tokens"] == 4096


def test_latex_compile_tool_has_fail_closed_deadline() -> None:
    runtime = container.SharedWorkflowRuntime(
        client=MagicMock(),
        container_id="cntr_test",
        output_dir=Path("/tmp"),
        allowed_domains=["example.com"],
    )

    tool = runtime.build_compile_latex_tool()

    assert tool.timeout_seconds == 600
    assert tool.timeout_behavior == "raise_exception"


def test_latex_compile_tool_drains_worker_before_cancellation() -> None:
    runtime = container.SharedWorkflowRuntime(
        client=MagicMock(),
        container_id="cntr_test",
        output_dir=Path("/tmp"),
        allowed_domains=["example.com"],
    )
    worker_started = threading.Event()
    worker_may_finish = threading.Event()
    worker_finished = threading.Event()

    def blocking_compile(_latex_source: str, _output_basename: str):
        worker_started.set()
        assert worker_may_finish.wait(timeout=5)
        worker_finished.set()
        return MagicMock(as_dict=lambda: {"pdf_filename": "output.pdf"})

    async def blocking_compile_async(
        latex_source: str,
        output_basename: str,
    ):
        return await container.await_cancellation_safe_blocking_call(
            asyncio.to_thread,
            blocking_compile,
            latex_source,
            output_basename,
        )

    runtime.compile_latex_async = blocking_compile_async  # type: ignore[method-assign]
    tool = runtime.build_compile_latex_tool()

    async def _exercise() -> None:
        task = asyncio.create_task(
            tool.on_invoke_tool._invoke_tool_impl(  # type: ignore[attr-defined]
                SimpleNamespace(tool_name=tool.name),
                json.dumps(
                    {
                        "latex_source": "\\documentclass{article}",
                        "output_basename": "output",
                    }
                ),
            )
        )
        assert await asyncio.to_thread(worker_started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        worker_may_finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_exercise())
    assert worker_finished.is_set()


def test_workflow_file_upload_has_crash_safe_provider_expiry() -> None:
    client = MagicMock()
    client.files.create.return_value = SimpleNamespace(id="file_test")
    with tempfile.TemporaryDirectory() as temporary_directory:
        path = Path(temporary_directory) / "source.pdf"
        path.write_bytes(b"%PDF-1.4\n")
        helpers.upload_file_to_openai(client, path, MagicMock())

    assert client.files.create.call_args.kwargs["purpose"] == "assistants"
    assert client.files.create.call_args.kwargs["expires_after"] == {
        "anchor": "created_at",
        "seconds": 86400,
    }


def test_provider_file_expiry_is_strictly_bounded() -> None:
    with patch.dict("os.environ", {"OPENAI_FILE_EXPIRES_SECONDS": "3600"}):
        assert helpers.openai_file_expires_after()["seconds"] == 3600
    for value in ("", "3599", "2592001", "not-a-number"):
        with patch.dict("os.environ", {"OPENAI_FILE_EXPIRES_SECONDS": value}):
            try:
                helpers.openai_file_expires_after()
            except ValueError:
                pass
            else:
                raise AssertionError(f"expected invalid expiry to fail: {value!r}")
