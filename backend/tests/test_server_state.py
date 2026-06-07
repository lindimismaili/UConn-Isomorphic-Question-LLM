from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.src.api import state


class ServerStateTests(unittest.TestCase):
    def test_prune_marks_stale_processless_workflow_lost(self) -> None:
        original_workflows = dict(state.workflows)
        original_tasks = dict(state.workflow_tasks)
        try:
            state.workflows.clear()
            state.workflow_tasks.clear()
            workflow = state._new_workflow_record()
            workflow["status"] = "running"
            workflow["updated_at"] = "2000-01-01T00:00:00Z"
            state.workflows["wf-stale"] = workflow

            with (
                patch.object(state, "_cleanup_artifact_directories"),
                patch.object(state, "_persist_workflow") as persist_workflow,
                patch.object(state, "ACTIVE_WORKFLOW_STALE_SECONDS", 1),
            ):
                state._prune_workflows()

            self.assertEqual(workflow["status"], state.LOST_STATUS)
            self.assertEqual(workflow["error"], "Workflow heartbeat was lost before completion.")
            persist_workflow.assert_called_with("wf-stale", workflow)
        finally:
            state.workflows.clear()
            state.workflows.update(original_workflows)
            state.workflow_tasks.clear()
            state.workflow_tasks.update(original_tasks)


if __name__ == "__main__":
    unittest.main()
