from __future__ import annotations

import asyncio
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.src.api import state
from backend.src.workflow.store import WorkflowStore


class ServerStateTests(unittest.TestCase):
    def test_mutable_runtime_directory_is_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "runtime"
            target.mkdir(mode=0o755)
            state._ensure_private_runtime_directory(target)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)

    def test_shutdown_cancels_and_persists_active_runtime_work(self) -> None:
        original_workflows = dict(state.workflows)
        original_workflow_tasks = dict(state.workflow_tasks)
        original_batches = dict(state.batch_jobs)
        original_batch_tasks = dict(state.batch_tasks)
        original_eval_batches = dict(state.eval_batch_jobs)
        original_eval_batch_tasks = dict(state.eval_batch_tasks)
        original_client = state._openai_client

        class _Client:
            closed = False

            def close(self) -> None:
                self.closed = True

        client = _Client()

        async def _never_finishes() -> None:
            await asyncio.Event().wait()

        async def _drive_shutdown() -> list[asyncio.Task]:
            workflow = state._new_workflow_record()
            workflow["status"] = "running"
            workflow["agents"]["normalizer"]["status"] = "thinking"
            batch = {
                "status": "running",
                "entries": [{"status": "running", "error": ""}],
                "updated_at": state._workflow_now(),
            }
            eval_batch = {
                "status": "running",
                "entries": [{"status": "queued", "error": ""}],
                "updated_at": state._workflow_now(),
            }
            tasks = [asyncio.create_task(_never_finishes()) for _ in range(3)]
            state.workflows["workflow"] = workflow
            state.workflow_tasks["workflow"] = tasks[0]
            state.batch_jobs["batch"] = batch
            state.batch_tasks["batch"] = tasks[1]
            state.eval_batch_jobs["eval"] = eval_batch
            state.eval_batch_tasks["eval"] = tasks[2]
            state._openai_client = client
            await asyncio.sleep(0)
            await state.shutdown_runtime_tasks()
            return tasks

        try:
            state.workflows.clear()
            state.workflow_tasks.clear()
            state.batch_jobs.clear()
            state.batch_tasks.clear()
            state.eval_batch_jobs.clear()
            state.eval_batch_tasks.clear()
            with (
                patch.object(state, "_touch_workflow"),
                patch.object(state, "_touch_batch_job"),
                patch.object(state, "_touch_eval_batch"),
            ):
                tasks = asyncio.run(_drive_shutdown())

            self.assertTrue(all(task.cancelled() for task in tasks))
            self.assertEqual(state.workflows["workflow"]["status"], "cancelled")
            self.assertEqual(state.batch_jobs["batch"]["status"], "cancelled")
            self.assertEqual(state.eval_batch_jobs["eval"]["status"], "cancelled")
            self.assertFalse(state.workflow_tasks)
            self.assertFalse(state.batch_tasks)
            self.assertFalse(state.eval_batch_tasks)
            self.assertTrue(client.closed)
            self.assertIsNone(state._openai_client)
        finally:
            state._openai_client = original_client
            state.workflows.clear()
            state.workflows.update(original_workflows)
            state.workflow_tasks.clear()
            state.workflow_tasks.update(original_workflow_tasks)
            state.batch_jobs.clear()
            state.batch_jobs.update(original_batches)
            state.batch_tasks.clear()
            state.batch_tasks.update(original_batch_tasks)
            state.eval_batch_jobs.clear()
            state.eval_batch_jobs.update(original_eval_batches)
            state.eval_batch_tasks.clear()
            state.eval_batch_tasks.update(original_eval_batch_tasks)

    def test_startup_reconciliation_marks_orphaned_records_terminal(self) -> None:
        original_store = state.workflow_store
        original_workflows = dict(state.workflows)
        original_batches = dict(state.batch_jobs)
        original_eval_batches = dict(state.eval_batch_jobs)
        try:
            with tempfile.TemporaryDirectory() as temporary_directory:
                store = WorkflowStore(Path(temporary_directory) / "state.sqlite3")
                workflow = state._new_workflow_record()
                workflow["status"] = "running"
                workflow["agents"]["normalizer"]["status"] = "thinking"
                batch = {
                    "status": "running",
                    "entries": [{"status": "queued", "error": ""}],
                    "updated_at": state._workflow_now(),
                }
                store.save("workflow", "workflow", workflow)
                store.save("batch", "batch", batch)
                store.save(state.EVAL_BATCH_RECORD_KIND, "eval", batch)

                state.workflow_store = store
                state.workflows.clear()
                state.batch_jobs.clear()
                state.eval_batch_jobs.clear()
                state.reconcile_persisted_state()

                self.assertEqual(state.workflows["workflow"]["status"], "lost")
                self.assertEqual(
                    state.workflows["workflow"]["agents"]["normalizer"]["status"],
                    "errored",
                )
                self.assertEqual(state.batch_jobs["batch"]["status"], "errored")
                self.assertEqual(state.eval_batch_jobs["eval"]["status"], "errored")
                self.assertIn("restarted", state.batch_jobs["batch"]["error"])
                self.assertEqual(
                    store.load("workflow", "workflow")["status"],
                    "lost",
                )
        finally:
            state.workflow_store = original_store
            state.workflows.clear()
            state.workflows.update(original_workflows)
            state.batch_jobs.clear()
            state.batch_jobs.update(original_batches)
            state.eval_batch_jobs.clear()
            state.eval_batch_jobs.update(original_eval_batches)

    def test_register_workflow_task_consumes_failure(self) -> None:
        """A failing background workflow task must not leave its exception
        unretrieved (which asyncio would dump as a noisy traceback)."""
        original_tasks = dict(state.workflow_tasks)
        try:
            state.workflow_tasks.clear()

            async def _boom() -> None:
                raise ValueError("generator validation failed")

            async def _drive() -> asyncio.Task:
                task = state._register_workflow_task("wf-fail", _boom())
                # Let the task fail and its done-callback fire, without any
                # external code retrieving the exception first.
                for _ in range(20):
                    await asyncio.sleep(0)
                    if task.done() and "wf-fail" not in state.workflow_tasks:
                        break
                return task

            task = asyncio.run(_drive())

            # The callback removed the task from the registry and the exception
            # is retrievable (i.e. it was consumed, no "never retrieved" warning).
            self.assertNotIn("wf-fail", state.workflow_tasks)
            self.assertIsInstance(task.exception(), ValueError)
        finally:
            state.workflow_tasks.clear()
            state.workflow_tasks.update(original_tasks)

    def test_prune_marks_stale_processless_workflow_lost(self) -> None:
        original_workflows = dict(state.workflows)
        original_tasks = dict(state.workflow_tasks)
        original_reservations = dict(state.workflow_admission_reservations)
        original_owners = dict(state.workflow_admission_owners)
        original_leases = dict(state.paid_work_leases)

        class _Lease:
            closed = False

            def close(self) -> None:
                self.closed = True

        lease = _Lease()
        try:
            state.workflows.clear()
            state.workflow_tasks.clear()
            state.workflow_admission_reservations.clear()
            state.workflow_admission_owners.clear()
            state.paid_work_leases.clear()
            workflow = state._new_workflow_record()
            workflow["status"] = "running"
            workflow["updated_at"] = "2000-01-01T00:00:00Z"
            workflow["agents"]["normalizer"]["status"] = "thinking"
            state.workflows["wf-stale"] = workflow
            reservation_id = "workflow:wf-stale"
            state.workflow_admission_reservations[reservation_id] = 1
            state.workflow_admission_owners["wf-stale"] = reservation_id
            state.paid_work_leases[reservation_id] = lease

            with (
                patch.object(state, "_cleanup_artifact_directories"),
                patch.object(state, "_persist_workflow") as persist_workflow,
                patch.object(state, "ACTIVE_WORKFLOW_STALE_SECONDS", 1),
            ):
                state._prune_workflows()

            self.assertEqual(workflow["status"], state.LOST_STATUS)
            self.assertEqual(workflow["error"], "Workflow heartbeat was lost before completion.")
            self.assertEqual(
                workflow["agents"]["normalizer"]["status"],
                "errored",
            )
            self.assertFalse(state.workflow_admission_reservations)
            self.assertFalse(state.workflow_admission_owners)
            self.assertFalse(state.paid_work_leases)
            self.assertTrue(lease.closed)
            persist_workflow.assert_called_with("wf-stale", workflow)
        finally:
            state.workflows.clear()
            state.workflows.update(original_workflows)
            state.workflow_tasks.clear()
            state.workflow_tasks.update(original_tasks)
            state.workflow_admission_reservations.clear()
            state.workflow_admission_reservations.update(
                original_reservations
            )
            state.workflow_admission_owners.clear()
            state.workflow_admission_owners.update(original_owners)
            state.paid_work_leases.clear()
            state.paid_work_leases.update(original_leases)

    def test_prune_keeps_create_data_child_owned_by_live_parent_task(self) -> None:
        original_workflows = dict(state.workflows)
        original_workflow_tasks = dict(state.workflow_tasks)
        original_batch_tasks = dict(state.batch_tasks)

        async def _drive() -> dict:
            workflow = state._new_workflow_record()
            workflow["status"] = "running"
            workflow["updated_at"] = "2000-01-01T00:00:00Z"
            workflow["parentBatchId"] = "parent-batch"
            state.workflows["child-workflow"] = workflow
            parent_task = asyncio.create_task(asyncio.Event().wait())
            state.batch_tasks["parent-batch"] = parent_task
            try:
                with (
                    patch.object(state, "_persist_workflow") as persist_workflow,
                    patch.object(state, "ACTIVE_WORKFLOW_STALE_SECONDS", 1),
                ):
                    state._prune_workflows()
                self.assertEqual(workflow["status"], "running")
                persist_workflow.assert_not_called()
                return workflow
            finally:
                parent_task.cancel()
                await asyncio.gather(parent_task, return_exceptions=True)

        try:
            state.workflows.clear()
            state.workflow_tasks.clear()
            state.batch_tasks.clear()
            asyncio.run(_drive())
        finally:
            state.workflows.clear()
            state.workflows.update(original_workflows)
            state.workflow_tasks.clear()
            state.workflow_tasks.update(original_workflow_tasks)
            state.batch_tasks.clear()
            state.batch_tasks.update(original_batch_tasks)

    def test_prune_terminalizes_taskless_batches_and_releases_leases(self) -> None:
        original_batches = dict(state.batch_jobs)
        original_batch_tasks = dict(state.batch_tasks)
        original_eval_batches = dict(state.eval_batch_jobs)
        original_eval_tasks = dict(state.eval_batch_tasks)
        original_reservations = dict(state.workflow_admission_reservations)
        original_leases = dict(state.paid_work_leases)

        class _Lease:
            closed = False

            def close(self) -> None:
                self.closed = True

        create_lease = _Lease()
        eval_lease = _Lease()
        try:
            state.batch_jobs.clear()
            state.batch_tasks.clear()
            state.eval_batch_jobs.clear()
            state.eval_batch_tasks.clear()
            state.workflow_admission_reservations.clear()
            state.paid_work_leases.clear()
            state.batch_jobs["stale-create"] = {
                "id": "stale-create",
                "status": "running",
                "entries": [{"status": "running", "error": ""}],
                "workflowAdmissionReservationId": "create-data:stale-create",
                "updated_at": "2000-01-01T00:00:00Z",
            }
            state.eval_batch_jobs["stale-eval"] = {
                "id": "stale-eval",
                "status": "running",
                "entries": [{"status": "queued", "error": ""}],
                "updated_at": "2000-01-01T00:00:00Z",
            }
            state.workflow_admission_reservations[
                "create-data:stale-create"
            ] = 1
            state.paid_work_leases[
                "create-data:stale-create"
            ] = create_lease
            state.paid_work_leases["eval-batch:stale-eval"] = eval_lease

            with (
                patch.object(state, "ACTIVE_WORKFLOW_STALE_SECONDS", 1),
                patch.object(state, "_persist_batch"),
                patch.object(state, "_persist_eval_batch"),
                patch.object(state, "MAX_ACTIVE_BATCH_JOBS", 1),
            ):
                state._prune_batch_jobs()
                state._prune_eval_batch_jobs()
                state._ensure_batch_capacity()

            self.assertEqual(
                state.batch_jobs["stale-create"]["status"],
                state.LOST_STATUS,
            )
            self.assertEqual(
                state.eval_batch_jobs["stale-eval"]["status"],
                state.LOST_STATUS,
            )
            self.assertEqual(
                state.batch_jobs["stale-create"]["entries"][0]["status"],
                "errored",
            )
            self.assertEqual(
                state.eval_batch_jobs["stale-eval"]["entries"][0]["status"],
                "errored",
            )
            self.assertTrue(create_lease.closed)
            self.assertTrue(eval_lease.closed)
            self.assertFalse(state.workflow_admission_reservations)
            self.assertFalse(state.paid_work_leases)
        finally:
            state.batch_jobs.clear()
            state.batch_jobs.update(original_batches)
            state.batch_tasks.clear()
            state.batch_tasks.update(original_batch_tasks)
            state.eval_batch_jobs.clear()
            state.eval_batch_jobs.update(original_eval_batches)
            state.eval_batch_tasks.clear()
            state.eval_batch_tasks.update(original_eval_tasks)
            state.workflow_admission_reservations.clear()
            state.workflow_admission_reservations.update(
                original_reservations
            )
            state.paid_work_leases.clear()
            state.paid_work_leases.update(original_leases)


if __name__ == "__main__":
    unittest.main()
