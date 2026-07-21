from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from backend.app.artifact_service import ArtifactService
from backend.app.contracts import TaskCreateRequest
from backend.app.orchestrator import Orchestrator
from backend.app.review_gate import ReviewGate
from backend.app.workflow_runs import WorkflowRunManager
from backend.tests.test_orchestrator import FakeRegistry as RunRegistry


class BlockingRegistry(RunRegistry):
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def run(self, stage: str, context: dict, feedback: str | None = None) -> dict:
        if stage == "question_understanding":
            self.started.set()
            self.release.wait(timeout=3)
        return super().run(stage, context, feedback)


class InstructionRegistry(BlockingRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.feedback_by_stage: dict[str, str | None] = {}

    def run(self, stage: str, context: dict, feedback: str | None = None) -> dict:
        self.feedback_by_stage[stage] = feedback
        return super().run(stage, context, feedback)


class CooperativeRegistry(RunRegistry):
    def __init__(self) -> None:
        self.started = threading.Event()

    def run(
        self,
        stage: str,
        context: dict,
        feedback: str | None = None,
        *,
        progress_handler=None,
        cancellation_checker=None,
    ) -> dict:
        if stage == "knowledge_integration":
            if progress_handler:
                progress_handler(
                    {
                        "node_id": "source_search:crossref",
                        "kind": "started",
                        "message": "Searching Crossref.",
                        "progress": 0.2,
                    }
                )
            self.started.set()
            while True:
                if cancellation_checker:
                    cancellation_checker()
                time.sleep(0.01)
        return super().run(stage, context, feedback)


class WorkflowRunManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.artifacts = ArtifactService(Path(self.temp.name))
        self.registry = RunRegistry()
        self.orchestrator = Orchestrator(
            self.registry,
            self.artifacts,
            ReviewGate(0.75),
        )
        self.manager = WorkflowRunManager(
            self.orchestrator,
            self.artifacts,
            max_workers=1,
        )

    def tearDown(self) -> None:
        self.manager.shutdown(wait=True)
        self.temp.cleanup()

    def create_task(self, task_id: str) -> None:
        self.orchestrator.create_task(
            TaskCreateRequest(task_id=task_id, original_question="Test question")
        )

    def wait_for(self, run_id: str, statuses: set[str]) -> dict:
        deadline = time.time() + 5
        while time.time() < deadline:
            record = self.manager.get(run_id)
            if record["status"] in statuses:
                return record
            time.sleep(0.01)
        self.fail(f"Run {run_id} did not reach {statuses}")

    def test_background_run_completes_and_persists_node_events(self) -> None:
        self.create_task("task_background")
        created = self.manager.start("task_background")
        completed = self.wait_for(created["run_id"], {"completed"})

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["iteration_id"], 1)
        self.assertEqual(len(completed["node_results"]), 6)
        manifest = self.artifacts.read_json("task_background", "manifest.json")
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["current_stage"], "completed")
        self.assertIsNone(manifest["active_run_id"])
        stored = self.artifacts.read_json(
            "task_background", f"runs/{created['run_id']}.json"
        )
        self.assertEqual(stored["status"], "completed")
        node_events = [
            event
            for event in self.artifacts.read_events("task_background")
            if event["type"].startswith("node_")
        ]
        self.assertTrue(node_events)
        self.assertEqual(
            sorted(event["data"]["sequence"] for event in node_events),
            [event["data"]["sequence"] for event in node_events],
        )

    def test_pause_then_cancel(self) -> None:
        blocking = BlockingRegistry()
        self.orchestrator.registry = blocking
        self.create_task("task_control")
        created = self.manager.start("task_control")
        self.assertTrue(blocking.started.wait(timeout=2))

        pausing = self.manager.pause(created["run_id"])
        self.assertEqual(pausing["status"], "pausing")
        blocking.release.set()
        paused = self.wait_for(created["run_id"], {"paused"})
        self.assertEqual(paused["current_stage"], "knowledge_integration")

        self.manager.cancel(created["run_id"])
        cancelled = self.wait_for(created["run_id"], {"cancelled"})
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(len(cancelled["node_results"]), 1)
        manifest = self.artifacts.read_json("task_control", "manifest.json")
        self.assertEqual(manifest["status"], "cancelled")
        self.assertIsNone(manifest["active_run_id"])

    def test_cancel_is_immediate_and_discards_late_agent_output(self) -> None:
        blocking = BlockingRegistry()
        self.orchestrator.registry = blocking
        self.create_task("task_immediate_cancel")
        created = self.manager.start("task_immediate_cancel")
        self.assertTrue(blocking.started.wait(timeout=2))

        cancelled = self.manager.cancel(created["run_id"])

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNotNone(cancelled["finished_at"])
        manifest = self.artifacts.read_json("task_immediate_cancel", "manifest.json")
        self.assertEqual(manifest["status"], "cancelled")
        self.assertIsNone(manifest["active_run_id"])
        blocking.release.set()
        deadline = time.time() + 2
        while self.manager.get(created["run_id"])["cancel_requested"]:
            if time.time() >= deadline:
                self.fail("Cancelled worker did not discard its late output")
            time.sleep(0.01)

        finished = self.manager.get(created["run_id"])
        self.assertEqual(finished["status"], "cancelled")
        self.assertEqual(finished["node_results"], [])
        self.assertFalse(
            (
                self.artifacts.task_root("task_immediate_cancel")
                / "stages/question_understanding/latest.output.json"
            ).exists()
        )

    def test_active_run_is_marked_interrupted_on_recovery(self) -> None:
        self.create_task("task_recovery")
        record = {
            "schema_version": "workflow_run_v1",
            "run_id": "run_recovery",
            "task_id": "task_recovery",
            "status": "running",
            "current_stage": "knowledge_integration",
            "current_node": "knowledge_integration",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        self.artifacts.write_json(
            "task_recovery", "runs/run_recovery.json", record
        )
        recovered = WorkflowRunManager(
            self.orchestrator,
            self.artifacts,
            max_workers=1,
        )
        try:
            loaded = recovered.get("run_recovery")
            self.assertEqual(loaded["status"], "interrupted")
            self.assertIn("restarted", loaded["error"])
        finally:
            recovered.shutdown(wait=True)

    def test_cooperative_node_cancel_finishes_within_two_seconds(self) -> None:
        cooperative = CooperativeRegistry()
        self.orchestrator.registry = cooperative
        self.create_task("task_cooperative_cancel")
        created = self.manager.start("task_cooperative_cancel")
        self.assertTrue(cooperative.started.wait(timeout=2))

        started_at = time.perf_counter()
        self.manager.cancel(created["run_id"])
        cancelled = self.wait_for(created["run_id"], {"cancelled"})

        self.assertLess(time.perf_counter() - started_at, 2)
        self.assertEqual(cancelled["current_node"], "source_search:crossref")

    def test_instruction_is_applied_at_target_stage_boundary(self) -> None:
        instruction_registry = InstructionRegistry()
        self.orchestrator.registry = instruction_registry
        self.create_task("task_instruction")
        created = self.manager.start("task_instruction")
        self.assertTrue(instruction_registry.started.wait(timeout=2))

        queued = self.manager.add_instruction(
            created["run_id"],
            comment="Prioritize longitudinal evidence.",
            target_stage="knowledge_integration",
        )
        self.assertEqual(len(queued["pending_instructions"]), 1)
        instruction_registry.release.set()
        self.wait_for(created["run_id"], {"completed"})

        self.assertIn(
            "Prioritize longitudinal evidence.",
            instruction_registry.feedback_by_stage["knowledge_integration"] or "",
        )
        finished = self.manager.get(created["run_id"])
        self.assertFalse(finished["pending_instructions"])
        self.assertEqual(len(finished["applied_instructions"]), 1)

    def test_iteration_plan_instruction_reaches_selected_agent(self) -> None:
        instruction_registry = InstructionRegistry()
        instruction_registry.release.set()
        self.orchestrator.registry = instruction_registry
        self.create_task("task_iteration_instruction")
        context = self.artifacts.load_context("task_iteration_instruction")
        context["mode"] = "auto"
        context["iteration"] = 2
        context["iteration_plans"] = [
            {
                "iteration": 2,
                "instructions_by_agent": {
                    "research_planning": "Strengthen falsification criteria."
                },
            }
        ]
        self.artifacts.save_context("task_iteration_instruction", context)

        created = self.manager.start(
            "task_iteration_instruction", start_stage="research_planning"
        )
        self.wait_for(created["run_id"], {"completed", "human_review", "retry"})

        self.assertIn(
            "Strengthen falsification criteria.",
            instruction_registry.feedback_by_stage["research_planning"] or "",
        )


if __name__ == "__main__":
    unittest.main()
