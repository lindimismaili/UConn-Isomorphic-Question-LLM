from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.src import server


class ServerStateTests(unittest.TestCase):
    def test_prune_marks_stale_processless_workflow_lost(self) -> None:
        original_workflows = dict(server.workflows)
        original_tasks = dict(server.workflow_tasks)
        try:
            server.workflows.clear()
            server.workflow_tasks.clear()
            workflow = server._new_workflow_record()
            workflow["status"] = "running"
            workflow["updated_at"] = "2000-01-01T00:00:00Z"
            server.workflows["wf-stale"] = workflow

            with (
                patch.object(server, "_cleanup_artifact_directories"),
                patch.object(server, "_persist_workflow") as persist_workflow,
                patch.object(server, "ACTIVE_WORKFLOW_STALE_SECONDS", 1),
            ):
                server._prune_workflows()

            self.assertEqual(workflow["status"], server.LOST_STATUS)
            self.assertEqual(workflow["error"], "Workflow heartbeat was lost before completion.")
            persist_workflow.assert_called_with("wf-stale", workflow)
        finally:
            server.workflows.clear()
            server.workflows.update(original_workflows)
            server.workflow_tasks.clear()
            server.workflow_tasks.update(original_tasks)


if __name__ == "__main__":
    unittest.main()
