from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.artifact_service import ArtifactError, ArtifactService
from backend.app.contracts import TaskCreateRequest
from backend.app.orchestrator import Orchestrator
from backend.app.review_gate import ReviewGate
from backend.app.settings import Settings


class NoopRegistry:
    def run(self, stage: str, context: dict, feedback: str | None = None) -> dict:
        raise AssertionError(f"Unexpected Agent execution: {stage}, {feedback}, {context}")


class PersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.artifacts = ArtifactService(self.root / "artifacts")
        self.orchestrator = Orchestrator(
            NoopRegistry(), self.artifacts, ReviewGate(0.75), max_iterations=4
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create(self, task_id: str) -> dict:
        return self.orchestrator.create_task(
            TaskCreateRequest(
                task_id=task_id,
                original_question="How should this scientific question be tested?",
            )
        )

    def test_archived_tasks_are_hidden_by_default_and_can_be_restored(self) -> None:
        self.create("task_active")
        self.create("task_archived")

        archived = self.artifacts.set_archived("task_archived", True)
        self.assertTrue(archived["archived"])
        self.assertEqual(
            {item["task_id"] for item in self.artifacts.list_tasks()},
            {"task_active"},
        )
        self.assertEqual(
            {item["task_id"] for item in self.artifacts.list_tasks(include_archived=True)},
            {"task_active", "task_archived"},
        )

        restored = self.artifacts.set_archived("task_archived", False)
        self.assertFalse(restored["archived"])
        self.assertEqual(len(self.artifacts.list_tasks()), 2)

    def test_attachment_is_persisted_and_injected_into_task_context(self) -> None:
        self.create("task_attachment")
        item, context = self.artifacts.add_attachment(
            "task_attachment",
            "background.md",
            "# Background\nA longitudinal cohort is required.".encode("utf-8"),
            "text/markdown",
            context_char_limit=10_000,
        )

        self.assertEqual(item["name"], "background.md")
        self.assertTrue((self.artifacts.task_root("task_attachment") / item["path"]).is_file())
        self.assertEqual(len(self.artifacts.list_attachments("task_attachment")), 1)
        self.assertIn("longitudinal cohort", context["user_input"]["question_description"])
        self.assertEqual(context["user_input"]["attachments"][0]["name"], "background.md")

        with self.assertRaises(ArtifactError):
            self.artifacts.add_attachment(
                "task_attachment",
                "unsafe.pdf",
                b"pdf",
                "application/pdf",
                context_char_limit=10_000,
            )
        with self.assertRaises(ArtifactError):
            self.artifacts.add_attachment(
                "task_attachment",
                "invalid.txt",
                b"\xff\xfe\xfd",
                "text/plain",
                context_char_limit=10_000,
            )

    def test_feedback_updates_runtime_controls_without_creating_a_new_task(self) -> None:
        context = self.create("task_controls")
        updated = self.orchestrator.record_feedback(
            context["task_id"],
            "research_planning",
            "Use a stricter falsification criterion.",
            mode="manual",
            reasoning_level="ultra",
            memory_level="high",
        )

        self.assertEqual(updated["task_id"], context["task_id"])
        self.assertEqual(updated["mode"], "manual")
        self.assertEqual(updated["iteration"], 2)
        constraints = updated["user_input"]["user_constraints"]
        self.assertEqual(constraints["reasoning_level"], "ultra")
        self.assertEqual(constraints["memory_level"], "high")

    def test_feedback_invalidates_target_and_downstream_results(self) -> None:
        context = self.create("task_invalidation")
        context.update(
            {
                "question_card": {"core_question": "upstream question"},
                "literature_cards": [{"literature_id": "lit_001"}],
                "evidence_cards": [{"evidence_id": "ev_001"}],
                "knowledge_gaps": [{"gap_id": "gap_001"}],
                "hypothesis_cards": [{"hypothesis_id": "hyp_001"}],
                "evidence_map": [{"hypothesis_id": "hyp_001"}],
                "research_plan": {"plan_id": "plan_001"},
                "final_review": {"overall_score": 0.9},
                "reviews": [
                    {"stage": "question_understanding", "decision": "accept"},
                    {"stage": "hypothesis_generation", "decision": "accept"},
                    {"stage": "research_planning", "decision": "accept"},
                ],
            }
        )
        self.artifacts.save_context(context["task_id"], context)
        self.artifacts.update_manifest(
            context["task_id"],
            status="completed",
            current_stage="completed",
            stage_status={
                "question_understanding": "passed",
                "knowledge_integration": "passed",
                "hypothesis_generation": "passed",
                "evidence_mapping": "passed",
                "research_planning": "passed",
                "final_review": "completed",
            },
        )

        updated = self.orchestrator.record_feedback(
            context["task_id"],
            "hypothesis_generation",
            "Generate a narrower hypothesis.",
        )

        self.assertEqual(updated["iteration"], 2)
        self.assertIsNotNone(updated["question_card"])
        self.assertEqual(len(updated["literature_cards"]), 1)
        self.assertEqual(updated["hypothesis_cards"], [])
        self.assertEqual(updated["evidence_map"], [])
        self.assertIsNone(updated["research_plan"])
        self.assertIsNone(updated["final_review"])
        self.assertEqual(
            [review["stage"] for review in updated["reviews"]],
            ["question_understanding"],
        )

        manifest = self.artifacts.read_json(context["task_id"], "manifest.json")
        self.assertEqual(manifest["iteration"], 2)
        self.assertEqual(manifest["current_stage"], "hypothesis_generation")
        self.assertEqual(manifest["stage_status"]["knowledge_integration"], "passed")
        self.assertEqual(manifest["stage_status"]["hypothesis_generation"], "retrying")
        self.assertEqual(manifest["stage_status"]["evidence_mapping"], "queued")
        self.assertEqual(manifest["stage_status"]["final_review"], "queued")

    def test_memory_level_controls_feedback_history(self) -> None:
        context = self.create("task_memory")
        context["feedback_events"] = [
            {
                "target": {"stage": "research_planning"},
                "input_summary": f"historical feedback {index}",
            }
            for index in range(5)
        ]
        context["reviews"] = [
            {"stage": "research_planning", "issues": [f"review issue {index}"]}
            for index in range(3)
        ]

        context["user_input"]["user_constraints"]["memory_level"] = "low"
        self.assertEqual(
            self.orchestrator._feedback_with_memory(context, "research_planning", "current"),
            "current",
        )

        context["user_input"]["user_constraints"]["memory_level"] = "medium"
        medium = self.orchestrator._feedback_with_memory(
            context, "research_planning", "current"
        )
        self.assertIn("historical feedback 4", medium or "")
        self.assertNotIn("historical feedback 0", medium or "")
        self.assertIn("review issue 2", medium or "")

        context["user_input"]["user_constraints"]["memory_level"] = "high"
        high = self.orchestrator._feedback_with_memory(context, "research_planning", "current")
        self.assertIn("historical feedback 0", high or "")
        self.assertIn("review issue 0", high or "")

    def test_agent_readiness_requires_credentials_when_declared(self) -> None:
        problem = self.root / "problem"
        knowledge = self.root / "knowledge"
        evidence = self.root / "evidence" / "src" / "evidence_mapping"
        planning = self.root / "planning"
        hypothesis = self.root / "hypothesis.py"
        for directory in (problem, knowledge, evidence, planning):
            directory.mkdir(parents=True)
        hypothesis.write_text("# test", encoding="utf-8")
        settings = Settings(
            problem_agent_root=problem,
            knowledge_agent_root=knowledge,
            hypothesis_agent_file=hypothesis,
            evidence_agent_root=self.root / "evidence",
            planning_agent_root=planning,
            artifacts_root=self.root / "runtime" / "tasks",
            review_threshold=0.75,
            max_iterations=4,
            attachment_max_bytes=2_000_000,
            attachment_context_chars=30_000,
            cors_origins=("http://localhost:5173",),
        )
        (self.root / "runtime").mkdir()

        with patch.dict(
            os.environ,
            {"DASHSCOPE_API_KEY": "", "QWEN_API_KEY": "", "LLM_API_KEY": ""},
        ):
            status = settings.source_status()

        self.assertTrue(status["question_understanding"]["available"])
        self.assertFalse(status["question_understanding"]["ready"])
        self.assertFalse(status["knowledge_integration"]["ready"])
        self.assertFalse(status["hypothesis_generation"]["ready"])
        self.assertTrue(status["evidence_mapping"]["ready"])


if __name__ == "__main__":
    unittest.main()
