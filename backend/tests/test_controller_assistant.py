from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.artifact_service import ArtifactService
from backend.app.contracts import ModelPolicy, TaskCreateRequest
from backend.app.controller_assistant import ControllerAssistant
from backend.app.orchestrator import Orchestrator
from backend.app.review_gate import ReviewGate
from backend.tests.test_orchestrator import FakeRegistry


class GenerousReviewLLM:
    def generate_json(self, **_: object) -> dict:
        return {
            "dimension_scores": {
                dimension: 0.98 for dimension in ControllerAssistant.review_weights
            },
            "strengths": ["结构完整"],
            "weaknesses": ["实验细节仍需核查"],
            "suggestions": ["补充独立验证集并明确统计功效分析。"],
            "agents_to_rerun": ["research_planning"],
        }


class ControllerAssistantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.artifacts = ArtifactService(Path(self.temp.name))
        self.orchestrator = Orchestrator(
            FakeRegistry(), self.artifacts, ReviewGate(0.75)
        )
        self.assistant = ControllerAssistant()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_status_question_routes_without_rerunning_agents(self) -> None:
        route = self.assistant.route({}, "现在进度和状态是什么？")
        self.assertEqual(route["intent"], "status_query")
        self.assertIsNone(route["target_stage"])

    def test_agent_mention_routes_to_explicit_stage(self) -> None:
        route = self.assistant.route({}, "@knowledge 请补充两篇纵向研究")
        self.assertEqual(route["intent"], "rerun_agent")
        self.assertEqual(route["target_stage"], "knowledge_integration")

    def test_experiment_feedback_only_reruns_planning(self) -> None:
        context = self.orchestrator.create_task(
            TaskCreateRequest(original_question="Test selective iteration")
        )
        context.update(
            literature_cards=[{"literature_id": "lit_1"}],
            evidence_cards=[{"evidence_id": "ev_1"}],
            hypothesis_cards=[{"hypothesis_id": "hyp_1"}],
            evidence_map=[{"hypothesis_id": "hyp_1"}],
            research_plan={"plans": [{"hypothesis_id": "hyp_1"}]},
            final_review={"passed": True},
        )
        self.artifacts.save_context(context["task_id"], context)
        evaluation, decision = self.assistant.evaluate_plan(
            context, 3, "只调整实验指标和基线", "experiment_design"
        )
        updated = self.orchestrator.apply_iteration_plan(
            context["task_id"], evaluation, decision
        )

        self.assertEqual(decision["agents_to_rerun"], ["research_planning"])
        self.assertTrue(updated["literature_cards"])
        self.assertTrue(updated["evidence_cards"])
        self.assertTrue(updated["hypothesis_cards"])
        self.assertTrue(updated["evidence_map"])
        self.assertIsNone(updated["research_plan"])
        self.assertIsNone(updated["final_review"])
        self.assertEqual(updated["extensions"]["iteration_control"]["status"], "active")

    def test_finish_iteration_persists_qa_mode(self) -> None:
        context = self.orchestrator.create_task(
            TaskCreateRequest(original_question="Test iteration finish")
        )
        context["research_plan"] = {"plans": [{"hypothesis_id": "hyp_1"}]}
        context["current_stage"] = "completed"
        self.artifacts.save_context(context["task_id"], context)

        updated = self.orchestrator.finish_iteration(context["task_id"])

        self.assertEqual(updated["current_stage"], "completed")
        self.assertEqual(
            updated["extensions"]["iteration_control"]["status"], "ended"
        )
        manifest = self.artifacts.read_json(context["task_id"], "manifest.json")
        self.assertEqual(manifest["status"], "completed")

    def test_controller_review_cannot_override_rubric_ceiling(self) -> None:
        context = self.orchestrator.create_task(
            TaskCreateRequest(original_question="Test strict controller review")
        )
        for stage in (
            "question_understanding",
            "knowledge_integration",
            "hypothesis_generation",
            "evidence_mapping",
            "research_planning",
        ):
            self.orchestrator.run_stage(context["task_id"], stage)
        completed_context = self.artifacts.load_context(context["task_id"])

        review = self.assistant.evaluate_workflow(
            completed_context, GenerousReviewLLM()
        )

        self.assertEqual(review["review_source"], "controller_agent")
        self.assertLess(review["overall_score"], 0.78)
        self.assertEqual(
            review["suggestions"],
            ["补充独立验证集并明确统计功效分析。"],
        )
        self.assertEqual(review["agents_to_rerun"], ["research_planning"])

    def test_model_policy_is_snapshotted_without_credentials(self) -> None:
        context = self.orchestrator.create_task(
            TaskCreateRequest(
                original_question="Test model policy",
                model_policy=ModelPolicy(
                    model="qwen-test",
                    reasoning="medium",
                    max_tokens=3000,
                    timeout_seconds=45,
                ),
            )
        )
        self.assertEqual(context["model_policy"]["model"], "qwen-test")
        self.assertEqual(context["model_policy"]["max_tokens"], 3000)
        self.assertNotIn("api_key", context["model_policy"])


if __name__ == "__main__":
    unittest.main()
