"""Focused regressions for API lifecycle, persistence, and upload hardening."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import threading
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from agents.exceptions import ModelBehaviorError
from fastapi import HTTPException

from backend.src import eval_pipeline
from backend.src.api import batch as batch_module
from backend.src.api import eval_batch as eval_batch_module
from backend.src.api import files as files_module
from backend.src.api import state
from backend.src.api import workflows as workflows_module
from backend.src.auth import AccessBoundaryConfig
from backend.src.safety import SanitizedProviderError
from backend.src.schemas import ExtracterOutput, SourceQuestionItem
from backend.src.server import create_app
from backend.src.workflow.guardrails import GuardrailDecision
from backend.tests.support import IdempotentTestClient as TestClient

_PDF = b"%PDF-1.4\ntrailer<</Root 1 0 R>>\n%%EOF\n"


class BlockingWorkerLifecycleTests(unittest.TestCase):
    def test_central_thread_helper_drains_worker_before_cancellation(self) -> None:
        worker_started = threading.Event()
        worker_may_finish = threading.Event()
        worker_finished = threading.Event()

        def blocking_operation() -> None:
            worker_started.set()
            self.assertTrue(worker_may_finish.wait(timeout=5))
            worker_finished.set()

        async def drive() -> None:
            task = asyncio.create_task(
                state._to_thread_limited(blocking_operation)
            )
            self.assertTrue(await asyncio.to_thread(worker_started.wait, 5))
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            worker_may_finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(drive())
        self.assertTrue(worker_finished.is_set())


class ApiHardeningBase(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(
            create_app(
                access_config=AccessBoundaryConfig(
                    rate_limit_requests=1_000,
                    rate_limit_window_seconds=60,
                )
            )
        )
        self._saved_workflows = dict(state.workflows)
        self._saved_workflow_tasks = dict(state.workflow_tasks)
        self._saved_batches = dict(state.batch_jobs)
        self._saved_batch_tasks = dict(state.batch_tasks)
        self._saved_eval_batches = dict(state.eval_batch_jobs)
        self._saved_eval_tasks = dict(state.eval_batch_tasks)
        self._saved_workflow_reservations = dict(
            state.workflow_admission_reservations
        )
        self._saved_workflow_owners = dict(state.workflow_admission_owners)
        state.workflows.clear()
        state.workflow_tasks.clear()
        state.batch_jobs.clear()
        state.batch_tasks.clear()
        state.eval_batch_jobs.clear()
        state.eval_batch_tasks.clear()
        state.workflow_admission_reservations.clear()
        state.workflow_admission_owners.clear()

    def tearDown(self) -> None:
        state.workflows.clear()
        state.workflows.update(self._saved_workflows)
        state.workflow_tasks.clear()
        state.workflow_tasks.update(self._saved_workflow_tasks)
        state.batch_jobs.clear()
        state.batch_jobs.update(self._saved_batches)
        state.batch_tasks.clear()
        state.batch_tasks.update(self._saved_batch_tasks)
        state.eval_batch_jobs.clear()
        state.eval_batch_jobs.update(self._saved_eval_batches)
        state.eval_batch_tasks.clear()
        state.eval_batch_tasks.update(self._saved_eval_tasks)
        state.workflow_admission_reservations.clear()
        state.workflow_admission_reservations.update(
            self._saved_workflow_reservations
        )
        state.workflow_admission_owners.clear()
        state.workflow_admission_owners.update(self._saved_workflow_owners)


class WorkflowLifecycleTests(ApiHardeningBase):
    def test_stop_is_idempotent_cancels_task_and_persists(self) -> None:
        workflow_id = "workflow-stop"
        workflow = state._new_workflow_record()
        workflow["status"] = "running"
        state.workflows[workflow_id] = workflow

        async def drive() -> tuple[dict, dict, asyncio.Task]:
            task = asyncio.create_task(asyncio.sleep(60))
            state.workflow_tasks[workflow_id] = task
            first = await workflows_module.stop_workflow(workflow_id)
            await asyncio.sleep(0)
            second = await workflows_module.stop_workflow(workflow_id)
            return first, second, task

        with patch.object(state, "_persist_workflow") as persist:
            first, second, task = asyncio.run(drive())

        self.assertEqual(first["status"], "cancelled")
        self.assertEqual(second["status"], "cancelled")
        self.assertTrue(task.cancelled())
        persist.assert_called_once_with(workflow_id, workflow)

    def test_stop_unknown_workflow_is_404(self) -> None:
        response = self.client.post("/api/workflow/not-found/stop")
        self.assertEqual(response.status_code, 404)

    def test_stop_cancels_paid_tasks_even_when_persistence_fails(self) -> None:
        async def drive() -> list[asyncio.Task]:
            workflow_id = "workflow-persist-failure"
            batch_id = "batch-persist-failure"
            eval_id = "eval-persist-failure"
            state.workflows[workflow_id] = {
                "status": "running",
                "agents": {},
            }
            state.batch_jobs[batch_id] = {
                "id": batch_id,
                "status": "running",
                "entries": [],
            }
            state.eval_batch_jobs[eval_id] = {
                "id": eval_id,
                "status": "running",
                "entries": [],
            }
            tasks = [
                asyncio.create_task(asyncio.sleep(60))
                for _ in range(3)
            ]
            state.workflow_tasks[workflow_id] = tasks[0]
            state.batch_tasks[batch_id] = tasks[1]
            state.eval_batch_tasks[eval_id] = tasks[2]

            await workflows_module.stop_workflow(workflow_id)
            await batch_module.stop_create_data(batch_id)
            await eval_batch_module.stop_eval_batch(eval_id)
            await asyncio.gather(*tasks, return_exceptions=True)
            return tasks

        failure = RuntimeError("sqlite unavailable")
        with (
            patch.object(
                workflows_module,
                "_mark_workflow_cancelled",
                side_effect=failure,
            ),
            patch.object(
                batch_module,
                "_touch_batch_job",
                side_effect=failure,
            ),
            patch.object(
                eval_batch_module,
                "_touch_eval_batch",
                side_effect=failure,
            ),
        ):
            tasks = asyncio.run(drive())

        self.assertTrue(all(task.cancelled() for task in tasks))

    def test_rerun_rejects_runtime_and_guardrail_nodes(self) -> None:
        with patch.object(workflows_module, "get_openai_client", return_value=object()):
            for agent_key in ("setup", "input_guardrail", "content_guardrail", "output"):
                with self.subTest(agent_key=agent_key):
                    response = self.client.post(
                        "/api/workflow/rerun-from-agent",
                        data={"sourceOutputFolder": "unused", "startAgent": agent_key},
                    )
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("startAgent must be one of", response.json()["detail"])

    def test_evaluation_replay_requires_explicit_paid_opt_in(self) -> None:
        with patch.object(workflows_module, "get_openai_client", return_value=object()):
            rerun_response = self.client.post(
                "/api/workflow/rerun-from-agent",
                data={"sourceOutputFolder": "unused", "startAgent": "evaluation"},
            )
            input_response = self.client.post(
                "/api/workflow/start-from-agent-input",
                data={"startAgent": "evaluation"},
                files={"agentInput": ("input.json", b"{}", "application/json")},
            )

        self.assertEqual(rerun_response.status_code, 400)
        self.assertIn("runEvaluation=true", rerun_response.json()["detail"])
        self.assertEqual(input_response.status_code, 400)
        self.assertIn("runEvaluation=true", input_response.json()["detail"])

    def test_evaluation_fanout_requires_separate_cost_confirmation(self) -> None:
        with (
            patch.object(workflows_module, "max_evaluation_items", return_value=2),
            patch.object(
                workflows_module,
                "question_level_criteria",
                return_value=[{}, {}, {}],
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                workflows_module._require_evaluation_cost_confirmation(
                    run_evaluation=True,
                    confirmed=False,
                )
            projected = workflows_module._require_evaluation_cost_confirmation(
                run_evaluation=True,
                confirmed=True,
            )
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("6 maximum grader calls", raised.exception.detail)
        self.assertEqual(projected, 6)

    def test_internal_workflow_default_keeps_evaluation_disabled(self) -> None:
        workflow_id = "default-no-eval"
        state.workflows[workflow_id] = state._new_workflow_record()
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "run"
            execute = AsyncMock()
            with (
                patch.object(
                    state,
                    "create_unique_run_dir",
                    return_value=(output_dir, "run"),
                ),
                patch.object(state, "_execute_workflow", execute),
            ):
                asyncio.run(state.run_workflow_async(workflow_id, "", []))
        self.assertFalse(execute.await_args.kwargs["run_evaluation"])

    def test_start_rejects_combined_source_count_before_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sources = []
            for index in range(state.MAX_UPLOAD_FILES + 1):
                source = Path(tmp) / f"source-{index}.pdf"
                source.write_bytes(_PDF)
                sources.append(str(source))

            with (
                patch.object(
                    workflows_module,
                    "_resolve_evaluation_fixture_paths",
                    return_value=sources,
                ),
                patch.object(
                    workflows_module,
                    "get_openai_client",
                    side_effect=AssertionError(
                        "invalid source fanout must not create a provider"
                    ),
                ) as provider,
            ):
                response = self.client.post(
                    "/api/workflow/start",
                    data={"testFiles": json.dumps(["fixture.pdf"])},
                )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Too many source files", response.text)
        provider.assert_not_called()

    def test_active_workflow_admission_cap_rejects_new_work(self) -> None:
        state.workflows["active"] = {"status": "running"}
        with patch.object(state, "MAX_ACTIVE_WORKFLOWS", 1):
            with self.assertRaises(HTTPException) as raised:
                state._ensure_workflow_capacity()
        self.assertEqual(raised.exception.status_code, 429)

    def test_concurrent_reservations_cannot_exceed_workflow_cap(self) -> None:
        async def drive() -> list[object]:
            results = await asyncio.gather(
                state._reserve_workflow_capacity("first"),
                state._reserve_workflow_capacity("second"),
                return_exceptions=True,
            )
            self.assertEqual(
                sum(state.workflow_admission_reservations.values()),
                1,
            )
            await state._release_workflow_reservation("first")
            await state._release_workflow_reservation("second")
            return results

        with patch.object(state, "MAX_ACTIVE_WORKFLOWS", 1):
            results = asyncio.run(drive())

        failures = [result for result in results if isinstance(result, HTTPException)]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].status_code, 429)
        self.assertFalse(state.workflow_admission_reservations)

    def test_create_data_pool_blocks_concurrent_direct_start(self) -> None:
        async def drive() -> list[object]:
            batch_id = "reserved-create-data"
            reservation_id = f"create-data:{batch_id}"
            batch = {
                "id": batch_id,
                "status": "starting",
                "entries": [],
            }
            await state._admit_create_data_batch(
                batch_id,
                batch,
                reservation_id=reservation_id,
                workflow_slots=2,
            )
            results = await asyncio.gather(
                state._admit_workflow_record(
                    "batch-child-one",
                    state._new_workflow_record(),
                    reservation_id=reservation_id,
                ),
                state._admit_workflow_record(
                    "batch-child-two",
                    state._new_workflow_record(),
                    reservation_id=reservation_id,
                ),
                state._reserve_workflow_capacity("workflow:direct"),
                return_exceptions=True,
            )
            state.workflows["batch-child-one"]["status"] = "completed"
            state.workflows["batch-child-two"]["status"] = "completed"
            await state._release_workflow_reservation(reservation_id)
            await state._release_workflow_reservation("workflow:direct")
            return results

        with (
            patch.object(state, "MAX_ACTIVE_WORKFLOWS", 2),
            patch.object(state, "_persist_batch"),
            patch.object(state, "_persist_workflow"),
        ):
            results = asyncio.run(drive())

        self.assertIsNone(results[0])
        self.assertIsNone(results[1])
        self.assertIsInstance(results[2], HTTPException)
        self.assertEqual(results[2].status_code, 429)
        self.assertFalse(state.workflow_admission_reservations)

    def test_agent_input_upload_gap_is_covered_by_atomic_reservation(self) -> None:
        class FakeUpload:
            filename = "input.json"

            async def close(self) -> None:
                return None

        read_count = 0

        async def delayed_read(*_args, **_kwargs) -> bytes:
            nonlocal read_count
            read_count += 1
            await asyncio.sleep(0.01)
            return b"{}"

        def discard_registered_task(_workflow_id: str, coroutine) -> None:
            coroutine.close()

        async def drive() -> list[object]:
            first_key = str(uuid.uuid4())
            second_key = str(uuid.uuid4())
            results = await asyncio.gather(
                workflows_module.start_workflow_from_agent_input(
                    startAgent="generator",
                    agentInput=FakeUpload(),
                    idempotency_key=first_key,
                    prompt="",
                    runEvaluation="false",
                    confirmEvaluationCost="false",
                ),
                workflows_module.start_workflow_from_agent_input(
                    startAgent="generator",
                    agentInput=FakeUpload(),
                    idempotency_key=second_key,
                    prompt="",
                    runEvaluation="false",
                    confirmEvaluationCost="false",
                ),
                return_exceptions=True,
            )
            for reservation_id in list(
                state.workflow_admission_reservations
            ):
                await state._release_workflow_reservation(reservation_id)
            return results

        with (
            patch.object(state, "MAX_ACTIVE_WORKFLOWS", 1),
            patch.object(
                workflows_module,
                "get_openai_client",
                return_value=object(),
            ),
            patch.object(
                workflows_module,
                "_read_upload_limited",
                new=delayed_read,
            ),
            patch.object(
                workflows_module,
                "_parse_agent_input_upload",
                return_value={},
            ),
            patch.object(workflows_module, "_validate_input_to_agent_payload"),
            patch.object(state, "_persist_workflow"),
            patch.object(
                workflows_module,
                "_register_workflow_task",
                side_effect=discard_registered_task,
            ),
        ):
            results = asyncio.run(drive())

        successes = [result for result in results if isinstance(result, dict)]
        failures = [result for result in results if isinstance(result, HTTPException)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].status_code, 429)
        self.assertEqual(read_count, 1)
        self.assertEqual(len(state.workflows), 1)

    def test_cancelled_reserved_task_releases_capacity_and_owner(self) -> None:
        async def drive() -> None:
            reservation_id = "workflow:cancel-before-provider"
            workflow_id = "cancel-before-provider"
            await state._reserve_workflow_capacity(reservation_id)
            await state._admit_workflow_record(
                workflow_id,
                state._new_workflow_record(),
                reservation_id=reservation_id,
            )

            async def provider_work() -> None:
                await asyncio.sleep(60)

            task = state._register_workflow_task(
                workflow_id,
                state._run_with_workflow_reservation(
                    reservation_id,
                    provider_work,
                ),
            )
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)

        with patch.object(state, "_persist_workflow"):
            asyncio.run(drive())

        self.assertFalse(state.workflow_admission_reservations)
        self.assertFalse(state.workflow_admission_owners)

    def test_global_workflow_semaphore_serializes_engine_runs(self) -> None:
        async def drive() -> int:
            current = 0
            max_seen = 0

            async def fake_engine(**_kwargs) -> None:
                nonlocal current, max_seen
                current += 1
                max_seen = max(max_seen, current)
                await asyncio.sleep(0.01)
                current -= 1

            first = state._new_workflow_record()
            second = state._new_workflow_record()
            semaphore = asyncio.Semaphore(1)
            with (
                tempfile.TemporaryDirectory() as tmp,
                patch.object(state, "get_openai_client", return_value=object()),
                patch.object(state, "_engine_run_workflow_async", new=fake_engine),
                patch.object(state, "workflow_execution_semaphore", semaphore),
                patch.object(state, "_persist_workflow"),
            ):
                root = Path(tmp)
                await asyncio.gather(
                    state._execute_workflow(
                        "one",
                        first,
                        "",
                        [],
                        outputs_dir=root / "one",
                        output_folder_label="one",
                    ),
                    state._execute_workflow(
                        "two",
                        second,
                        "",
                        [],
                        outputs_dir=root / "two",
                        output_folder_label="two",
                    ),
                )
            return max_seen

        self.assertEqual(asyncio.run(drive()), 1)

    def test_workflow_provider_error_is_sanitized_in_state_and_logs(self) -> None:
        secret = "WORKFLOW_PROVIDER_SECRET_78b2"

        async def drive() -> dict:
            workflow = state._new_workflow_record()

            async def fake_engine(**_kwargs) -> None:
                raise SanitizedProviderError(
                    operation="normalizer stage",
                    cause=ModelBehaviorError(secret),
                )

            with (
                tempfile.TemporaryDirectory() as tmp,
                patch.object(state, "get_openai_client", return_value=object()),
                patch.object(state, "_engine_run_workflow_async", new=fake_engine),
                patch.object(state, "_persist_workflow"),
            ):
                with self.assertLogs(state.logger.name, level="ERROR") as captured:
                    with self.assertRaises(SanitizedProviderError):
                        await state._execute_workflow(
                            "provider-error",
                            workflow,
                            "",
                            [],
                            outputs_dir=Path(tmp),
                            output_folder_label="provider-error",
                        )
            self.assertNotIn(secret, "\n".join(captured.output))
            return workflow

        workflow = asyncio.run(drive())
        self.assertEqual(
            workflow["error"],
            "Workflow failed (ModelBehaviorError)",
        )
        self.assertNotIn(secret, json.dumps(workflow))

    def test_registered_workflow_task_logs_only_exception_category(self) -> None:
        secret = "BACKGROUND_TASK_SECRET_34ac"

        async def drive() -> list[str]:
            async def fail() -> None:
                raise ModelBehaviorError(secret)

            with self.assertLogs(state.logger.name, level="DEBUG") as captured:
                task = state._register_workflow_task("background-error", fail())
                with self.assertRaises(ModelBehaviorError):
                    await task
                await asyncio.sleep(0)
            return captured.output

        logs = "\n".join(asyncio.run(drive()))
        self.assertIn("ModelBehaviorError", logs)
        self.assertNotIn(secret, logs)
        self.assertNotIn("background-error", state.workflow_tasks)

    def test_workflow_token_streaming_is_opt_in(self) -> None:
        async def drive(enabled: bool) -> tuple[object, bool]:
            captured: dict[str, object] = {}

            async def fake_engine(**kwargs) -> None:
                captured.update(kwargs)
                captured["queue_registered"] = "stream-test" in state.workflow_token_queues

            with (
                tempfile.TemporaryDirectory() as tmp,
                patch.object(state, "get_openai_client", return_value=object()),
                patch.object(state, "_engine_run_workflow_async", new=fake_engine),
                patch.object(state, "WORKFLOW_TOKEN_STREAMING_ENABLED", enabled),
                patch.object(state, "_persist_workflow"),
            ):
                await state._execute_workflow(
                    "stream-test",
                    state._new_workflow_record(),
                    "",
                    [],
                    outputs_dir=Path(tmp),
                    output_folder_label="stream-test",
                )
                self.assertNotIn("stream-test", state.workflow_token_queues)
            return captured.get("emit_token"), bool(captured.get("queue_registered"))

        disabled_emitter, disabled_queue = asyncio.run(drive(False))
        enabled_emitter, enabled_queue = asyncio.run(drive(True))

        self.assertIsNone(disabled_emitter)
        self.assertFalse(disabled_queue)
        self.assertTrue(callable(enabled_emitter))
        self.assertTrue(enabled_queue)


class PureReadRouteTests(ApiHardeningBase):
    def test_status_result_and_download_do_not_persist_or_touch(self) -> None:
        workflow_id = "pure-read-workflow"
        workflow = state._new_workflow_record()
        workflow.update(
            {
                "status": "completed",
                "result": {"generated_questions": []},
                "outputFolder": "run",
                "files": [{"name": "final_result.json", "downloadable": True}],
            }
        )
        original_updated_at = workflow["updated_at"]
        state.workflows[workflow_id] = workflow

        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            run_dir = output_root / "run"
            run_dir.mkdir()
            (run_dir / "final_result.json").write_text("{}", encoding="utf-8")
            with (
                patch.object(files_module, "OUTPUTS_DIR", output_root),
                patch.object(state, "_persist_workflow") as persist,
            ):
                self.assertEqual(
                    self.client.get(f"/api/workflow/{workflow_id}/status").status_code,
                    200,
                )
                self.assertEqual(
                    self.client.get(f"/api/workflow/{workflow_id}/result").status_code,
                    200,
                )
                self.assertEqual(
                    self.client.get(
                        f"/api/download/{workflow_id}/final_result.json"
                    ).status_code,
                    200,
                )

        persist.assert_not_called()
        self.assertEqual(workflow["updated_at"], original_updated_at)

    def test_batch_and_eval_status_reads_do_not_refresh_or_persist(self) -> None:
        batch_id = "pure-batch"
        eval_id = "pure-eval"
        batch = {
            "id": batch_id,
            "status": "completed",
            "totalFiles": 0,
            "completedFiles": 0,
            "failedFiles": 0,
            "cancelledFiles": 0,
            "blockedFiles": 0,
            "doneFiles": 0,
            "entries": [],
            "updated_at": "2026-07-27T12:00:00Z",
        }
        eval_batch = {
            **batch,
            "id": eval_id,
            "mode": "prior_runs",
        }
        state.batch_jobs[batch_id] = batch
        state.eval_batch_jobs[eval_id] = eval_batch

        with (
            patch.object(state, "_persist_batch") as persist_batch,
            patch.object(state, "_persist_eval_batch") as persist_eval,
        ):
            self.assertEqual(
                self.client.get(f"/api/create-data/{batch_id}/status").status_code,
                200,
            )
            self.assertEqual(
                self.client.get(f"/api/create-data/{batch_id}/result").status_code,
                200,
            )
            self.assertEqual(
                self.client.get(f"/api/eval-batch/{eval_id}/status").status_code,
                200,
            )
            self.assertEqual(
                self.client.get(f"/api/eval-batch/{eval_id}/result").status_code,
                200,
            )

        persist_batch.assert_not_called()
        persist_eval.assert_not_called()
        self.assertEqual(batch["updated_at"], "2026-07-27T12:00:00Z")
        self.assertEqual(eval_batch["updated_at"], "2026-07-27T12:00:00Z")


class UploadSelectionTests(ApiHardeningBase):
    def test_upload_session_ids_have_at_least_128_bits_of_entropy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(files_module, "UPLOADS_DIR", root):
                first = self.client.post(
                    "/api/upload",
                    files={"files": ("one.txt", b"one", "text/plain")},
                ).json()["sessionId"]
                second = self.client.post(
                    "/api/upload",
                    files={"files": ("two.txt", b"two", "text/plain")},
                ).json()["sessionId"]

            self.assertNotEqual(first, second)
            for session_id in (first, second):
                decoded = base64.urlsafe_b64decode(
                    session_id + ("=" * (-len(session_id) % 4))
                )
                self.assertGreaterEqual(len(decoded), 16)
                self.assertNotIn("/", session_id)
                self.assertTrue((root / session_id).is_dir())

    def test_session_requires_explicit_known_file_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = root / "session"
            session.mkdir()
            source = session / "source.txt"
            source.write_text("physics", encoding="utf-8")
            (session / state.UPLOAD_MANIFEST_FILENAME).write_text(
                json.dumps(
                    {
                        "files": [
                            {
                                "fileId": "known",
                                "storedName": source.name,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(state, "UPLOADS_DIR", root):
                with self.assertRaises(HTTPException) as missing:
                    state._source_paths_from_session("session", [])
                with self.assertRaises(HTTPException) as unknown:
                    state._source_paths_from_session("session", ["unknown"])
                selected = state._source_paths_from_session("session", ["known"])

        self.assertEqual(missing.exception.status_code, 400)
        self.assertEqual(unknown.exception.status_code, 400)
        self.assertEqual(selected, [str(source.resolve())])

    def test_workflow_start_rejects_session_without_file_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = root / "session"
            session.mkdir()
            (session / state.UPLOAD_MANIFEST_FILENAME).write_text(
                '{"files":[]}',
                encoding="utf-8",
            )
            with (
                patch.object(state, "UPLOADS_DIR", root),
                patch.object(workflows_module, "get_openai_client", return_value=object()),
            ):
                response = self.client.post(
                    "/api/workflow/start",
                    data={"sessionId": "session"},
                )
        self.assertEqual(response.status_code, 400)
        self.assertIn("fileIds", response.json()["detail"])

    def test_workflow_start_rejects_prompt_only_paid_dead_end(self) -> None:
        with patch.object(
            workflows_module,
            "get_openai_client",
            return_value=object(),
        ):
            response = self.client.post(
                "/api/workflow/start",
                data={"prompt": "Make a question"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("source file", response.json()["detail"])

    def test_workflow_tracks_active_upload_session_for_cleanup_protection(self) -> None:
        def discard_task(_workflow_id: str, coroutine) -> None:
            coroutine.close()

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.pdf"
            source.write_bytes(_PDF)
            with (
                patch.object(workflows_module, "get_openai_client", return_value=object()),
                patch.object(
                    workflows_module,
                    "_source_paths_from_session",
                    return_value=[str(source)],
                ),
                patch.object(state, "_persist_workflow"),
                patch.object(
                    workflows_module,
                    "_register_workflow_task",
                    side_effect=discard_task,
                ),
            ):
                response = self.client.post(
                    "/api/workflow/start",
                    data={
                        "sessionId": "upload-session",
                        "fileIds": '["known"]',
                    },
                )
        self.assertEqual(response.status_code, 200, response.text)
        workflow = state.workflows[response.json()["workflowId"]]
        self.assertEqual(workflow["uploadFolder"], "upload-session")
        asyncio.run(
            state._release_workflow_reservation(
                f"workflow:{response.json()['workflowId']}"
            )
        )

    def test_chunked_reader_enforces_limit_before_unbounded_read(self) -> None:
        class ChunkedUpload:
            def __init__(self) -> None:
                self.chunks = [b"1234", b"5678", b""]

            async def read(self, _size: int) -> bytes:
                return self.chunks.pop(0)

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(
                state._read_upload_limited(
                    ChunkedUpload(),
                    max_bytes=6,
                    label="Agent input",
                )
            )
        self.assertEqual(raised.exception.status_code, 413)


class EvalBatchHardeningTests(ApiHardeningBase):
    def test_eval_tracing_defaults_off_and_requires_explicit_opt_in(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(state._eval_batch_tracing_disabled())
        with patch.dict(
            os.environ,
            {"OPENAI_AGENTS_DISABLE_TRACING": "false"},
            clear=True,
        ):
            self.assertFalse(state._eval_batch_tracing_disabled())

    def test_eval_records_use_distinct_durable_kind(self) -> None:
        payload = {"id": "eval", "updated_at": "2026-07-27T12:00:00Z"}
        with patch.object(state.workflow_store, "save") as save:
            state._persist_eval_batch("eval", payload)
        save.assert_called_once_with(state.EVAL_BATCH_RECORD_KIND, "eval", payload)
        self.assertNotEqual(state.EVAL_BATCH_RECORD_KIND, "batch")

    def test_eval_upload_rejects_non_pdf_and_cleans_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.object(eval_batch_module, "UPLOADS_DIR", root),
                patch.object(eval_batch_module, "get_openai_client", return_value=object()),
            ):
                response = self.client.post(
                    "/api/eval-batch/start",
                    data={"mode": "upload", "confirm_grader_calls": "true"},
                    files=[
                        ("source_file", ("source.txt", b"not pdf", "text/plain")),
                        ("generated_files", ("generated.pdf", _PDF, "application/pdf")),
                    ],
                )
            self.assertEqual(list(root.iterdir()), [])
        self.assertEqual(response.status_code, 400)

    def test_eval_upload_enforces_aggregate_cap_and_cleans_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.object(eval_batch_module, "UPLOADS_DIR", root),
                patch.object(eval_batch_module, "get_openai_client", return_value=object()),
                patch.object(eval_batch_module, "MAX_UPLOAD_BYTES", 100),
                patch.object(eval_batch_module, "MAX_UPLOAD_TOTAL_BYTES", len(_PDF) + 4),
            ):
                response = self.client.post(
                    "/api/eval-batch/start",
                    data={"mode": "upload", "confirm_grader_calls": "true"},
                    files=[
                        ("source_file", ("source.pdf", _PDF, "application/pdf")),
                        ("generated_files", ("generated.pdf", _PDF, "application/pdf")),
                    ],
                )
            self.assertEqual(list(root.iterdir()), [])
        self.assertEqual(response.status_code, 413)

    def test_eval_upload_uses_collision_safe_names_and_persists_id(self) -> None:
        async def noop(_batch_id: str) -> None:
            return None

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch.object(eval_batch_module, "UPLOADS_DIR", root),
                patch.object(eval_batch_module, "get_openai_client", return_value=object()),
                patch.object(eval_batch_module, "run_eval_batch_async", new=noop),
                patch.object(eval_batch_module, "_persist_eval_batch") as persist,
            ):
                response = self.client.post(
                    "/api/eval-batch/start",
                    data={"mode": "upload", "confirm_grader_calls": "true"},
                    files=[
                        ("source_file", ("same.pdf", _PDF, "application/pdf")),
                        ("generated_files", ("same.pdf", _PDF, "application/pdf")),
                    ],
                )
                body = response.json()
                batch = state.eval_batch_jobs[body["batchId"]]

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(batch["id"], body["batchId"])
        self.assertEqual(batch["source_name"], "same.pdf")
        self.assertEqual(batch["entries"][0]["name"], "same-2.pdf")
        self.assertEqual(persist.call_count, 1)
        persist.assert_called_with(body["batchId"], batch)

    def test_prior_run_listing_supports_dated_labels_and_skips_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            root = workspace / "outputs"
            root.mkdir()
            dated = root / "07-27-2026" / "12-00-00"
            batch_child = root / "07-27-2026" / "12-30-00-batch" / "question-abc"
            legacy = root / "legacy"
            outside = workspace / "outside"
            for run in (dated, batch_child, legacy, outside):
                run.mkdir(parents=True)
                (run / "final_result.json").write_text(
                    '{"generated_questions":[]}',
                    encoding="utf-8",
                )
            (root / "linked-outside").symlink_to(outside, target_is_directory=True)

            with patch.object(state, "OUTPUTS_DIR", root):
                labels = {item["folder"] for item in state._list_output_runs()}

        self.assertEqual(
            labels,
            {
                "07-27-2026/12-00-00",
                "07-27-2026/12-30-00-batch/question-abc",
                "legacy",
            },
        )

    def test_eval_evaluation_runs_through_bounded_thread_helper(self) -> None:
        async def drive() -> tuple[list[str], list[dict], dict, object]:
            calls: list[str] = []
            run_configs: list[dict] = []

            async def fake_to_thread(func, /, *_args, **_kwargs):
                calls.append(func.__name__)
                return {
                    "status": "complete",
                    "criterion_scores": {},
                    "items_evaluated": 0,
                }

            def fake_run_config(**kwargs):
                run_configs.append(kwargs)
                return object()

            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source_run = root / "source-run"
                source_run.mkdir()
                (source_run / "final_result.json").write_text(
                    '{"generated_questions":[{"source_id":"S1"}]}',
                    encoding="utf-8",
                )
                batch_id = "eval-offload"
                batch = {
                    "id": batch_id,
                    "status": "starting",
                    "mode": "prior_runs",
                    "totalFiles": 1,
                    "completedFiles": 0,
                    "failedFiles": 0,
                    "cancelledFiles": 0,
                    "blockedFiles": 0,
                    "doneFiles": 0,
                    "evaluationItemBudget": 100,
                    "reservedEvaluationItems": 0,
                    "entries": [
                        {
                            "name": "source-run",
                            "folder": "source-run",
                            "status": "queued",
                        }
                    ],
                    "created_at": "2026-07-27T12:00:00Z",
                    "updated_at": "2026-07-27T12:00:00Z",
                }
                state.eval_batch_jobs[batch_id] = batch
                with (
                    patch.object(state, "OUTPUTS_DIR", root),
                    patch.object(state, "get_openai_client", return_value=object()),
                    patch.object(state, "_to_thread_limited", new=fake_to_thread),
                    patch.object(state, "_persist_eval_batch"),
                    patch("agents.RunConfig", side_effect=fake_run_config),
                    patch.object(
                        eval_pipeline,
                        "EvalPdfExtractionRuntime",
                    ) as extraction_runtime_type,
                ):
                    await state.run_eval_batch_async(batch_id)
                return calls, run_configs, batch, extraction_runtime_type

        calls, run_configs, batch, extraction_runtime_type = asyncio.run(drive())
        self.assertEqual(calls, ["run_openai_output_evaluation"])
        self.assertEqual(len(run_configs), 1)
        self.assertTrue(run_configs[0]["tracing_disabled"])
        self.assertFalse(run_configs[0]["trace_include_sensitive_data"])
        self.assertEqual(batch["status"], "completed")
        self.assertEqual(batch["completedFiles"], 1)
        extraction_runtime_type.assert_not_called()

    def test_eval_upload_bounds_source_and_generated_pdf_extraction_lifecycle(
        self,
    ) -> None:
        async def drive() -> tuple[dict, AsyncMock, list[object]]:
            runtime_instances: list[object] = []
            client = object()
            source = ExtracterOutput(
                filename="source.pdf",
                theme="physics",
                questions=[
                    SourceQuestionItem(
                        source_id="S1",
                        source_raw="source",
                        stem="source",
                        options=[],
                        has_explicit_answer=False,
                        question_type="open_ended",
                        answer="",
                    )
                ],
            )
            generated = source.model_copy(
                update={"filename": "generated.pdf"},
                deep=True,
            )
            extract = AsyncMock(side_effect=[source, generated])

            class FakeRuntime:
                def __init__(self, **kwargs) -> None:
                    self.client = kwargs["client"]
                    self.run_config = kwargs["run_config"]
                    self.output_dir = kwargs["output_dir"]
                    self.to_thread = kwargs["to_thread"]
                    self.max_files = kwargs["max_files"]
                    self.close_count = 0
                    runtime_instances.append(self)

                async def aclose(self) -> None:
                    self.close_count += 1

            async def fake_to_thread(func, /, *_args, **_kwargs):
                self.assertEqual(func.__name__, "run_openai_output_evaluation")
                return {
                    "status": "complete",
                    "criterion_scores": {},
                    "items_evaluated": 1,
                }

            with tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                outputs = workspace / "outputs"
                outputs.mkdir()
                source_pdf = workspace / "source.pdf"
                generated_pdf = workspace / "generated.pdf"
                source_pdf.write_bytes(_PDF)
                generated_pdf.write_bytes(_PDF)
                batch_id = "eval-shared-extraction-runtime"
                batch = {
                    "id": batch_id,
                    "status": "starting",
                    "mode": "upload",
                    "source_pdf_path": str(source_pdf),
                    "evaluationItemBudget": 1,
                    "totalFiles": 1,
                    "completedFiles": 0,
                    "failedFiles": 0,
                    "cancelledFiles": 0,
                    "blockedFiles": 0,
                    "doneFiles": 0,
                    "reservedEvaluationItems": 0,
                    "entries": [
                        {
                            "name": generated_pdf.name,
                            "pdf_path": str(generated_pdf),
                            "status": "queued",
                        }
                    ],
                    "created_at": "2026-07-27T12:00:00Z",
                    "updated_at": "2026-07-27T12:00:00Z",
                }
                state.eval_batch_jobs[batch_id] = batch
                with (
                    patch.object(state, "OUTPUTS_DIR", outputs),
                    patch.object(state, "get_openai_client", return_value=client),
                    patch.object(state, "_to_thread_limited", new=fake_to_thread),
                    patch.object(state, "_persist_eval_batch"),
                    patch.object(
                        eval_pipeline,
                        "EvalPdfExtractionRuntime",
                        new=FakeRuntime,
                    ),
                    patch.object(
                        eval_pipeline,
                        "extract_questions_from_pdf",
                        new=extract,
                    ),
                ):
                    await state.run_eval_batch_async(batch_id)
                return batch, extract, runtime_instances

        batch, extract, runtime_instances = asyncio.run(drive())
        self.assertEqual(batch["status"], "completed")
        self.assertEqual(extract.await_count, 2)
        self.assertEqual(len(runtime_instances), 1)
        runtime = runtime_instances[0]
        self.assertEqual(runtime.max_files, 2)
        self.assertEqual(runtime.close_count, 1)
        self.assertIs(extract.await_args_list[0].kwargs["runtime"], runtime)
        self.assertIs(extract.await_args_list[1].kwargs["runtime"], runtime)
        self.assertIs(
            extract.await_args_list[0].kwargs["run_config"],
            extract.await_args_list[1].kwargs["run_config"],
        )

    def test_eval_batch_exception_is_sanitized_in_state_and_artifact(self) -> None:
        secret = "EVAL_BATCH_PROVIDER_SECRET_f29d"

        async def drive() -> tuple[dict, dict]:
            async def fake_to_thread(_func, /, *_args, **_kwargs):
                raise ModelBehaviorError(secret)

            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source_run = root / "source-run"
                source_run.mkdir()
                (source_run / "final_result.json").write_text(
                    '{"generated_questions":[{"source_id":"S1"}]}',
                    encoding="utf-8",
                )
                batch_id = "eval-provider-error"
                batch = {
                    "id": batch_id,
                    "status": "starting",
                    "mode": "prior_runs",
                    "totalFiles": 1,
                    "completedFiles": 0,
                    "failedFiles": 0,
                    "cancelledFiles": 0,
                    "blockedFiles": 0,
                    "doneFiles": 0,
                    "evaluationItemBudget": 100,
                    "reservedEvaluationItems": 0,
                    "entries": [
                        {
                            "name": "source-run",
                            "folder": "source-run",
                            "status": "queued",
                        }
                    ],
                    "created_at": "2026-07-27T12:00:00Z",
                    "updated_at": "2026-07-27T12:00:00Z",
                }
                state.eval_batch_jobs[batch_id] = batch
                with (
                    patch.object(state, "OUTPUTS_DIR", root),
                    patch.object(state, "get_openai_client", return_value=object()),
                    patch.object(state, "_to_thread_limited", new=fake_to_thread),
                    patch.object(state, "_persist_eval_batch"),
                    patch("agents.RunConfig", return_value=object()),
                ):
                    await state.run_eval_batch_async(batch_id)
                [error_path] = list(
                    (root / batch["outputFolder"]).glob("*/error.json")
                )
                error_artifact = json.loads(error_path.read_text(encoding="utf-8"))
                return batch, error_artifact

        batch, artifact = asyncio.run(drive())
        serialized = json.dumps({"batch": batch, "artifact": artifact})
        self.assertEqual(batch["status"], "errored")
        self.assertEqual(
            batch["entries"][0]["error"],
            "Eval batch entry failed (ModelBehaviorError)",
        )
        self.assertEqual(artifact["error_type"], "ModelBehaviorError")
        self.assertNotIn(secret, serialized)

    def test_eval_report_failures_and_cancellation_propagate_to_batch(self) -> None:
        async def drive(report: dict, batch_id: str) -> dict:
            async def fake_to_thread(_func, /, *_args, **_kwargs):
                return dict(report)

            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source_run = root / "source-run"
                source_run.mkdir()
                (source_run / "final_result.json").write_text(
                    '{"generated_questions":[{"source_id":"S1"}]}',
                    encoding="utf-8",
                )
                batch = {
                    "id": batch_id,
                    "status": "starting",
                    "mode": "prior_runs",
                    "totalFiles": 1,
                    "completedFiles": 0,
                    "failedFiles": 0,
                    "cancelledFiles": 0,
                    "blockedFiles": 0,
                    "doneFiles": 0,
                    "evaluationItemBudget": 100,
                    "reservedEvaluationItems": 0,
                    "entries": [
                        {
                            "name": "source-run",
                            "folder": "source-run",
                            "status": "queued",
                        }
                    ],
                    "created_at": "2026-07-27T12:00:00Z",
                    "updated_at": "2026-07-27T12:00:00Z",
                }
                state.eval_batch_jobs[batch_id] = batch
                with (
                    patch.object(state, "OUTPUTS_DIR", root),
                    patch.object(state, "get_openai_client", return_value=object()),
                    patch.object(state, "_to_thread_limited", new=fake_to_thread),
                    patch.object(state, "_persist_eval_batch"),
                    patch("agents.RunConfig", return_value=object()),
                ):
                    await state.run_eval_batch_async(batch_id)
                return batch

        cases = (
            (
                {"status": "failed", "reason": "grader failed", "criterion_scores": {}},
                "errored",
                "errored",
                "grader failed",
            ),
            (
                {"status": "incomplete", "reason": "coverage mismatch", "criterion_scores": {}},
                "errored",
                "errored",
                "coverage mismatch",
            ),
            (
                {"status": "cancelled", "reason": "stopped remotely", "criterion_scores": {}},
                "cancelled",
                "cancelled",
                "stopped remotely",
            ),
        )
        for index, (report, entry_status, batch_status, error) in enumerate(cases):
            with self.subTest(report_status=report["status"]):
                batch = asyncio.run(drive(report, f"eval-status-{index}"))
                self.assertEqual(batch["entries"][0]["status"], entry_status)
                self.assertEqual(batch["status"], batch_status)
                self.assertIn(error, batch["entries"][0]["error"])
                self.assertNotEqual(batch["status"], "completed")

    def test_eval_upload_source_guardrail_block_prevents_all_grading(self) -> None:
        async def drive() -> tuple[dict, AsyncMock, list[str]]:
            graded: list[str] = []

            async def fail_if_threaded(func, /, *_args, **_kwargs):
                graded.append(func.__name__)
                raise AssertionError("grading must not run after a guardrail block")

            blocked = eval_pipeline.EvalPdfGuardrailBlockedError(
                GuardrailDecision(
                    allowed=False,
                    category="prompt_injection",
                    reason="Prompt injection detected in source PDF.",
                    stage="content",
                )
            )
            extract = AsyncMock(side_effect=blocked)

            with tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                outputs = workspace / "outputs"
                outputs.mkdir()
                source_pdf = workspace / "source.pdf"
                generated_pdf = workspace / "generated.pdf"
                source_pdf.write_bytes(_PDF)
                generated_pdf.write_bytes(_PDF)
                batch_id = "eval-source-blocked"
                batch = {
                    "id": batch_id,
                    "status": "starting",
                    "mode": "upload",
                    "source_pdf_path": str(source_pdf),
                    "evaluationItemBudget": 1,
                    "totalFiles": 1,
                    "completedFiles": 0,
                    "failedFiles": 0,
                    "cancelledFiles": 0,
                    "blockedFiles": 0,
                    "doneFiles": 0,
                    "entries": [
                        {
                            "name": generated_pdf.name,
                            "pdf_path": str(generated_pdf),
                            "status": "queued",
                        }
                    ],
                    "created_at": "2026-07-27T12:00:00Z",
                    "updated_at": "2026-07-27T12:00:00Z",
                }
                state.eval_batch_jobs[batch_id] = batch
                with (
                    patch.object(state, "OUTPUTS_DIR", outputs),
                    patch.object(state, "get_openai_client", return_value=object()),
                    patch.object(state, "_to_thread_limited", new=fail_if_threaded),
                    patch.object(state, "_persist_eval_batch"),
                    patch.object(
                        eval_pipeline,
                        "extract_questions_from_pdf",
                        new=extract,
                    ),
                    patch("agents.RunConfig", return_value=object()),
                ):
                    await state.run_eval_batch_async(batch_id)
                return batch, extract, graded

        batch, extract, graded = asyncio.run(drive())
        self.assertEqual(batch["status"], "blocked")
        self.assertEqual(batch["blockedFiles"], 1)
        self.assertEqual(batch["entries"][0]["status"], "blocked")
        self.assertIn("Prompt injection", batch["entries"][0]["error"])
        self.assertEqual(extract.await_count, 1)
        self.assertEqual(graded, [])

    def test_eval_upload_generated_guardrail_block_prevents_grading(self) -> None:
        async def drive() -> tuple[dict, AsyncMock, list[str]]:
            graded: list[str] = []

            async def fail_if_threaded(func, /, *_args, **_kwargs):
                graded.append(func.__name__)
                raise AssertionError("grading must not run after a guardrail block")

            source_extraction = ExtracterOutput(
                filename="source.pdf",
                theme="physics",
                questions=[
                    SourceQuestionItem(
                        source_id="1",
                        source_raw="A ball falls from rest.",
                        stem="How fast is the ball after one second?",
                        options=[],
                        has_explicit_answer=False,
                        question_type="open_ended",
                        answer="",
                    )
                ],
            )
            blocked = eval_pipeline.EvalPdfGuardrailBlockedError(
                GuardrailDecision(
                    allowed=False,
                    category="off_topic",
                    reason="Generated PDF is not physics-oriented.",
                    stage="content",
                )
            )
            extract = AsyncMock(side_effect=[source_extraction, blocked])

            with tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                outputs = workspace / "outputs"
                outputs.mkdir()
                source_pdf = workspace / "source.pdf"
                generated_pdf = workspace / "generated.pdf"
                source_pdf.write_bytes(_PDF)
                generated_pdf.write_bytes(_PDF)
                batch_id = "eval-generated-blocked"
                batch = {
                    "id": batch_id,
                    "status": "starting",
                    "mode": "upload",
                    "source_pdf_path": str(source_pdf),
                    "evaluationItemBudget": 1,
                    "totalFiles": 1,
                    "completedFiles": 0,
                    "failedFiles": 0,
                    "cancelledFiles": 0,
                    "blockedFiles": 0,
                    "doneFiles": 0,
                    "entries": [
                        {
                            "name": generated_pdf.name,
                            "pdf_path": str(generated_pdf),
                            "status": "queued",
                        }
                    ],
                    "created_at": "2026-07-27T12:00:00Z",
                    "updated_at": "2026-07-27T12:00:00Z",
                }
                state.eval_batch_jobs[batch_id] = batch
                with (
                    patch.object(state, "OUTPUTS_DIR", outputs),
                    patch.object(state, "get_openai_client", return_value=object()),
                    patch.object(state, "_to_thread_limited", new=fail_if_threaded),
                    patch.object(state, "_persist_eval_batch"),
                    patch.object(
                        eval_pipeline,
                        "extract_questions_from_pdf",
                        new=extract,
                    ),
                    patch("agents.RunConfig", return_value=object()),
                ):
                    await state.run_eval_batch_async(batch_id)
                return batch, extract, graded

        batch, extract, graded = asyncio.run(drive())
        self.assertEqual(batch["status"], "blocked")
        self.assertEqual(batch["blockedFiles"], 1)
        self.assertEqual(batch["entries"][0]["status"], "blocked")
        self.assertIn("not physics-oriented", batch["entries"][0]["error"])
        self.assertEqual(extract.await_count, 2)
        self.assertEqual(graded, [])


class PruningAndBatchMappingTests(ApiHardeningBase):
    def test_destructive_runtime_settings_reject_zero_and_non_finite_values(self) -> None:
        with patch.dict(os.environ, {"OUTPUT_RETENTION_SECONDS": "0"}):
            with self.assertRaisesRegex(ValueError, "OUTPUT_RETENTION_SECONDS"):
                state._bounded_int_from_env(
                    "OUTPUT_RETENTION_SECONDS",
                    default=3600,
                    minimum=3600,
                    maximum=7200,
                )
        with patch.dict(os.environ, {"OPENAI_STATUS_TIMEOUT_SECONDS": "inf"}):
            with self.assertRaisesRegex(ValueError, "OPENAI_STATUS_TIMEOUT_SECONDS"):
                state._bounded_float_from_env(
                    "OPENAI_STATUS_TIMEOUT_SECONDS",
                    default=5,
                    minimum=1,
                    maximum=60,
                )

    def test_active_eval_inputs_are_protected_artifact_dependencies(self) -> None:
        state.workflows["active-upload"] = {
            "status": "running",
            "uploadFolder": "upload-session",
        }
        state.eval_batch_jobs["prior"] = {
            "status": "running",
            "mode": "prior_runs",
            "outputFolder": "07-27-2026/13-00-00",
            "entries": [{"folder": "07-26-2026/12-00-00"}],
        }
        state.eval_batch_jobs["uploads"] = {
            "status": "running",
            "mode": "upload",
            "uploadFolder": "eval_batch_uploads",
            "entries": [],
        }

        self.assertIn("07-26-2026/12-00-00", state._active_output_labels())
        active_uploads = state._active_upload_labels()
        self.assertIn("upload-session", active_uploads)
        self.assertIn("eval_batch_uploads", active_uploads)

    def test_cancelled_jobs_protect_artifacts_until_their_tasks_finish(self) -> None:
        workflow_task = MagicMock()
        workflow_task.done.return_value = False
        batch_task = MagicMock()
        batch_task.done.return_value = False
        eval_task = MagicMock()
        eval_task.done.return_value = False

        state.workflows["workflow-cancelled"] = {
            "status": "cancelled",
            "outputFolder": "07-27-2026/workflow-output",
            "uploadFolder": "workflow-upload",
        }
        state.workflow_tasks["workflow-cancelled"] = workflow_task
        state.batch_jobs["batch-cancelled"] = {
            "status": "cancelled",
            "outputFolder": "07-27-2026/batch-output",
        }
        state.batch_tasks["batch-cancelled"] = batch_task
        state.eval_batch_jobs["eval-cancelled"] = {
            "status": "cancelled",
            "mode": "prior_runs",
            "outputFolder": "07-27-2026/eval-output",
            "uploadFolder": "eval-upload",
            "entries": [{"folder": "07-26-2026/eval-input"}],
        }
        state.eval_batch_tasks["eval-cancelled"] = eval_task

        active_outputs = state._active_output_labels()
        active_uploads = state._active_upload_labels()

        self.assertTrue(
            {
                "07-27-2026/workflow-output",
                "07-27-2026/batch-output",
                "07-27-2026/eval-output",
                "07-26-2026/eval-input",
            }.issubset(active_outputs)
        )
        self.assertTrue(
            {"workflow-upload", "eval-upload"}.issubset(active_uploads)
        )

    def test_stopped_create_data_holds_batch_capacity_until_task_done(self) -> None:
        async def drive() -> None:
            batch_id = "batch-cancel-cleanup"
            batch = {
                "id": batch_id,
                "status": "running",
                "entries": [],
            }
            task = asyncio.create_task(asyncio.sleep(60))
            state.batch_jobs[batch_id] = batch
            state.batch_tasks[batch_id] = task

            await batch_module.stop_create_data(batch_id)
            self.assertEqual(batch["status"], "cancelled")
            self.assertFalse(task.done())
            with patch.object(state, "MAX_ACTIVE_BATCH_JOBS", 1):
                with self.assertRaises(HTTPException) as raised:
                    state._ensure_batch_capacity()
            self.assertEqual(raised.exception.status_code, 429)
            await asyncio.gather(task, return_exceptions=True)

        with patch.object(state, "_persist_batch"):
            asyncio.run(drive())

    def test_stopped_eval_batch_holds_batch_capacity_until_task_done(self) -> None:
        async def drive() -> None:
            batch_id = "eval-cancel-cleanup"
            batch = {
                "id": batch_id,
                "status": "running",
                "entries": [],
            }
            task = asyncio.create_task(asyncio.sleep(60))
            state.eval_batch_jobs[batch_id] = batch
            state.eval_batch_tasks[batch_id] = task

            await eval_batch_module.stop_eval_batch(batch_id)
            self.assertEqual(batch["status"], "cancelled")
            self.assertFalse(task.done())
            with patch.object(state, "MAX_ACTIVE_BATCH_JOBS", 1):
                with self.assertRaises(HTTPException) as raised:
                    state._ensure_batch_capacity()
            self.assertEqual(raised.exception.status_code, 429)
            await asyncio.gather(task, return_exceptions=True)

        with patch.object(state, "_persist_eval_batch"):
            asyncio.run(drive())

    def test_pruners_keep_terminal_records_with_unfinished_tasks(self) -> None:
        cases = (
            (
                "workflow",
                state.workflows,
                state.workflow_tasks,
                state._prune_workflows,
            ),
            (
                "batch",
                state.batch_jobs,
                state.batch_tasks,
                state._prune_batch_jobs,
            ),
            (
                "eval_batch",
                state.eval_batch_jobs,
                state.eval_batch_tasks,
                state._prune_eval_batch_jobs,
            ),
        )
        for kind, records, tasks, prune in cases:
            with self.subTest(kind=kind):
                live_id = f"{kind}-live-cleanup"
                removable_id = f"{kind}-removable"
                records[live_id] = {
                    "status": "cancelled",
                    "updated_at": "2999-01-01T00:00:00+00:00",
                }
                records[removable_id] = {
                    "status": "completed",
                    "updated_at": "3000-01-01T00:00:00+00:00",
                }
                live_task = MagicMock()
                live_task.done.return_value = False
                tasks[live_id] = live_task

                with (
                    patch.object(state, "WORKFLOW_MAX_ENTRIES", 1),
                    patch.object(state.workflow_store, "delete"),
                ):
                    prune()

                self.assertIn(live_id, records)
                self.assertIn(live_id, tasks)
                self.assertNotIn(removable_id, records)
                live_task.cancel.assert_not_called()
                records.clear()
                tasks.clear()

    def test_artifact_cleanup_never_deletes_active_upload_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs = root / "outputs"
            uploads = root / "uploads"
            outputs.mkdir()
            uploads.mkdir()
            active = uploads / "active-session"
            inactive = uploads / "inactive-session"
            active.mkdir()
            inactive.mkdir()
            os.utime(active, (1, 1))
            os.utime(inactive, (1, 1))

            with (
                patch.object(state, "OUTPUTS_DIR", outputs),
                patch.object(state, "UPLOADS_DIR", uploads),
                patch.object(state, "OUTPUT_RETENTION_SECONDS", 1),
                patch.object(state, "UPLOAD_RETENTION_SECONDS", 1),
                patch.object(state, "OUTPUT_MAX_RUNS", 100),
                patch.object(state, "OUTPUT_MAX_BYTES", 1024 * 1024),
                patch.object(state, "UPLOAD_MAX_BYTES", 1024 * 1024),
                patch.object(state, "ARTIFACT_CLEANUP_INTERVAL_SECONDS", 1),
                patch.object(state, "_last_artifact_cleanup_ts", 0),
            ):
                state._cleanup_artifact_directories(set(), {"active-session"})

            self.assertTrue(active.is_dir())
            self.assertFalse(inactive.exists())

    def test_z_timestamp_parser_preserves_utc_offset(self) -> None:
        expected = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(state._timestamp_seconds("2026-07-27T12:00:00Z"), expected)
        self.assertEqual(
            state._timestamp_seconds("2026-07-27T08:00:00-04:00"),
            expected,
        )

    def test_pruning_mutates_registries_on_loop_and_only_offloads_filesystem(self) -> None:
        async def drive() -> tuple[list[str], list[str]]:
            direct: list[str] = []
            offloaded: list[str] = []
            sleeps = 0

            async def fake_sleep(_seconds: float) -> None:
                nonlocal sleeps
                sleeps += 1
                if sleeps > 1:
                    raise asyncio.CancelledError

            async def fake_to_thread(func, /, *_args, **_kwargs):
                offloaded.append(func.__name__)

            def record(name: str):
                def inner(*_args, **_kwargs):
                    direct.append(name)
                    return set() if name == "active" else None

                return inner

            with (
                patch.object(state.asyncio, "sleep", new=fake_sleep),
                patch.object(state.asyncio, "to_thread", new=fake_to_thread),
                patch.object(state, "_prune_workflows", new=record("workflows")),
                patch.object(state, "_prune_batch_jobs", new=record("batches")),
                patch.object(state, "_prune_eval_batch_jobs", new=record("eval_batches")),
                patch.object(state, "_active_output_labels", new=record("active")),
                patch.object(state, "_active_upload_labels", new=record("uploads")),
                patch.object(
                    state,
                    "_cleanup_artifact_directories",
                    new=record("filesystem"),
                ),
            ):
                with self.assertRaises(asyncio.CancelledError):
                    await state.pruning_loop()
            return direct, offloaded

        direct, offloaded = asyncio.run(drive())
        self.assertEqual(
            direct,
            ["workflows", "batches", "eval_batches", "active", "uploads"],
        )
        self.assertEqual(offloaded, ["prune_idempotency", "inner"])

    def test_create_data_maps_guardrail_block_to_blocked_entry(self) -> None:
        async def drive() -> tuple[dict, dict]:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                outputs = root / "outputs"
                evaluation = root / "evaluation"
                outputs.mkdir()
                evaluation.mkdir()
                (evaluation / "question.pdf").write_bytes(_PDF)
                batch_id = "blocked-batch"
                batch = {
                    "id": batch_id,
                    "status": "starting",
                    "prompt": "",
                    "totalFiles": 1,
                    "completedFiles": 0,
                    "failedFiles": 0,
                    "cancelledFiles": 0,
                    "blockedFiles": 0,
                    "doneFiles": 0,
                    "outputFolder": "",
                    "entries": [
                        {
                            "name": "question.pdf",
                            "relativePath": "question.pdf",
                            "folderName": "question",
                            "status": "queued",
                            "workflowId": "",
                            "outputFolder": "",
                            "error": "",
                        }
                    ],
                    "created_at": "2026-07-27T12:00:00Z",
                    "updated_at": "2026-07-27T12:00:00Z",
                }
                state.batch_jobs[batch_id] = batch

                async def fake_execute(
                    _workflow_id: str,
                    workflow: dict,
                    *_args,
                    **_kwargs,
                ) -> None:
                    workflow["status"] = "blocked"
                    workflow["blocked"] = {
                        "category": "off_topic",
                        "reason": "Not a physics question",
                    }

                with (
                    patch.object(state, "OUTPUTS_DIR", outputs),
                    patch.object(state, "EVALUATION_DIR", evaluation),
                    patch.object(state, "_execute_workflow", new=fake_execute),
                    patch.object(state, "_persist_workflow"),
                    patch.object(state, "_persist_batch"),
                ):
                    await state.run_create_data_batch_async(batch_id)
                summary = json.loads(
                    (
                        outputs
                        / batch["outputFolder"]
                        / batch["summaryFile"]
                    ).read_text(encoding="utf-8")
                )
                return batch, summary

        batch, summary = asyncio.run(drive())
        self.assertEqual(batch["status"], "blocked")
        self.assertEqual(batch["entries"][0]["status"], "blocked")
        self.assertEqual(batch["blockedFiles"], 1)
        self.assertEqual(batch["doneFiles"], 1)
        self.assertIn("blocked", batch["error"])
        self.assertEqual(summary["status"], "blocked")
        self.assertEqual(summary["error"], batch["error"])

    def test_create_data_provider_error_is_sanitized_and_marks_batch_errored(
        self,
    ) -> None:
        secret = "CREATE_DATA_PROVIDER_SECRET_4e77"

        async def drive() -> tuple[dict, dict]:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                outputs = root / "outputs"
                evaluation = root / "evaluation"
                outputs.mkdir()
                evaluation.mkdir()
                (evaluation / "question.pdf").write_bytes(_PDF)
                batch_id = "errored-batch"
                batch = {
                    "id": batch_id,
                    "status": "starting",
                    "prompt": "",
                    "totalFiles": 1,
                    "completedFiles": 0,
                    "failedFiles": 0,
                    "cancelledFiles": 0,
                    "blockedFiles": 0,
                    "doneFiles": 0,
                    "outputFolder": "",
                    "entries": [
                        {
                            "name": "question.pdf",
                            "relativePath": "question.pdf",
                            "folderName": "question",
                            "status": "queued",
                            "workflowId": "",
                            "outputFolder": "",
                            "error": "",
                        }
                    ],
                    "created_at": "2026-07-27T12:00:00Z",
                    "updated_at": "2026-07-27T12:00:00Z",
                }
                state.batch_jobs[batch_id] = batch

                async def fake_execute(*_args, **_kwargs) -> None:
                    raise ModelBehaviorError(secret)

                with (
                    patch.object(state, "OUTPUTS_DIR", outputs),
                    patch.object(state, "EVALUATION_DIR", evaluation),
                    patch.object(state, "_execute_workflow", new=fake_execute),
                    patch.object(state, "_persist_workflow"),
                    patch.object(state, "_persist_batch"),
                ):
                    await state.run_create_data_batch_async(batch_id)
                summary = json.loads(
                    (
                        outputs
                        / batch["outputFolder"]
                        / batch["summaryFile"]
                    ).read_text(encoding="utf-8")
                )
                return batch, summary

        batch, summary = asyncio.run(drive())
        serialized = json.dumps({"batch": batch, "summary": summary})
        self.assertEqual(batch["status"], "errored")
        self.assertEqual(batch["failedFiles"], 1)
        self.assertEqual(
            batch["entries"][0]["error"],
            "Create-data entry failed (ModelBehaviorError)",
        )
        self.assertEqual(summary["status"], "errored")
        self.assertEqual(summary["error"], batch["error"])
        self.assertNotIn(secret, serialized)


if __name__ == "__main__":
    unittest.main()
