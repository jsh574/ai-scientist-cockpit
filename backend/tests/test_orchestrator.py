from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.agent_protocol import AGENT_SPECS
from backend.app.artifact_service import ArtifactError, ArtifactService
from backend.app.contracts import HumanReviewRequest, TaskCreateRequest
from backend.app.orchestrator import Orchestrator
from backend.app.review_gate import ReviewGate


class FakeRegistry:
    def run(self, stage: str, context: dict, feedback: str | None = None) -> dict:
        payloads = {
            "question_understanding": {
                "question_card": {
                    "question_id": "q_001",
                    "core_question": "Does inflammation precede tau spread?",
                }
            },
            "knowledge_integration": {
                "literature_cards": [
                    {
                        "literature_id": "lit_001",
                        "title": "Longitudinal inflammation and tau study",
                        "doi": "10.1000/example",
                    }
                ],
                "evidence_cards": [
                    {
                        "evidence_id": "ev_001",
                        "source_literature_id": "lit_001",
                        "claim": "Inflammation precedes tau change.",
                    }
                ],
                "knowledge_gaps": [
                    {"gap_id": "gap_001", "description": "Causality is unresolved."}
                ],
            },
            "hypothesis_generation": {
                "hypothesis_cards": [
                    {
                        "hypothesis_id": "hyp_001",
                        "statement": "Inflammation accelerates tau spread.",
                        "based_on_evidence_ids": ["ev_001"],
                        "related_gap_ids": ["gap_001"],
                    }
                ]
            },
            "evidence_mapping": {
                "evidence_map": [
                    {
                        "hypothesis_id": "hyp_001",
                        "supporting_evidence_ids": ["ev_001"],
                        "opposing_evidence_ids": [],
                        "uncertain_evidence_ids": [],
                    }
                ]
            },
            "research_planning": {
                "research_plan": {
                    "plans": [
                        {
                            "hypothesis_id": "hyp_001",
                            "plan": {
                                "rationale": {
                                    "logic_chain": [
                                        {
                                            "evidence_ids": ["ev_001"],
                                            "source_ids": ["lit_001"],
                                        }
                                    ]
                                },
                                "experiments": {
                                    "metrics": ["lagged association"],
                                    "falsification_criteria": ["No temporal association"],
                                },
                            },
                        }
                    ]
                }
            },
        }
        return {
            "metadata": {
                "task_id": context["task_id"],
                "agent_id": AGENT_SPECS[stage].agent_id,
                "stage": stage,
                "iteration": context["iteration"],
                "status": "success",
            },
            "payload": payloads[stage],
            "self_review": {
                "passed": True,
                "overall_score": 0.9,
                "threshold": 0.75,
                "dimension_scores": {"quality": 0.9},
                "issues": [],
                "suggestions": [],
            },
        }


class BorderlineHypothesisRegistry(FakeRegistry):
    def run(self, stage: str, context: dict, feedback: str | None = None) -> dict:
        response = super().run(stage, context, feedback)
        if stage == "hypothesis_generation":
            response["metadata"]["status"] = "partial_success"
            response["self_review"].update(
                passed=False,
                overall_score=0.731,
                threshold=0.75,
            )
        return response


class OrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.artifacts = ArtifactService(Path(self.temp.name))
        self.orchestrator = Orchestrator(
            FakeRegistry(), self.artifacts, ReviewGate(0.75), max_iterations=3
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create(self, mode: str = "auto") -> dict:
        return self.orchestrator.create_task(
            TaskCreateRequest(
                task_id=f"task_{mode}",
                mode=mode,
                original_question="How does inflammation affect tau spread?",
            )
        )

    def test_auto_workflow_persists_complete_trace(self) -> None:
        context = self.create()
        result = self.orchestrator.run_from(context["task_id"])
        self.assertEqual(result["status"], "completed")
        latest = self.artifacts.load_context(context["task_id"])
        self.assertEqual(latest["current_stage"], "completed")
        self.assertEqual(len(latest["versions"]), 6)
        self.assertTrue(latest["final_review"]["passed"])
        self.assertGreaterEqual(len(self.artifacts.read_events(context["task_id"])), 13)
        artifact_paths = {
            item["path"] for item in self.artifacts.list_artifacts(context["task_id"])
        }
        self.assertIn("context/task_context.latest.json", artifact_paths)
        self.assertIn("stages/research_planning/latest.output.json", artifact_paths)

    def test_manual_mode_stops_and_resumes_after_review(self) -> None:
        context = self.create("manual")
        first = self.orchestrator.run_from(context["task_id"])
        self.assertEqual(first["status"], "human_review")
        self.assertEqual(first["executions"][0]["stage"], "question_understanding")
        accepted = self.orchestrator.submit_review(
            context["task_id"],
            HumanReviewRequest(
                stage="question_understanding", decision="accept", comment="Looks testable."
            ),
        )
        self.assertEqual(accepted["status"], "passed")
        self.assertIsNotNone(accepted["task_context"]["question_card"])

    def test_feedback_creates_new_iteration_and_version(self) -> None:
        context = self.create()
        self.orchestrator.run_from(context["task_id"])
        result = self.orchestrator.apply_feedback(
            context["task_id"],
            "hypothesis_generation",
            "Narrow the population and rerank hypotheses.",
            rerun_downstream=False,
        )
        self.assertEqual(result["task_context"]["iteration"], 2)
        latest = self.artifacts.load_context(context["task_id"])
        self.assertEqual(len(latest["feedback_events"]), 1)
        self.assertGreaterEqual(len(latest["versions"]), 8)

    def test_operator_can_continue_after_quality_gate_retry(self) -> None:
        self.orchestrator.registry = BorderlineHypothesisRegistry()
        context = self.create()
        result = self.orchestrator.run_from(context["task_id"])
        self.assertEqual(result["status"], "retry")
        self.assertEqual(result["executions"][-1]["stage"], "hypothesis_generation")

        accepted = self.orchestrator.submit_review(
            context["task_id"],
            HumanReviewRequest(
                stage="hypothesis_generation",
                decision="accept",
                comment="Keep this result and continue.",
            ),
        )
        self.assertEqual(accepted["status"], "passed")
        self.assertTrue(accepted["task_context"]["hypothesis_cards"])
        self.assertEqual(accepted["review"]["operator"], "human")
        self.assertAlmostEqual(accepted["review"]["overall_score"], 0.9731)

    def test_artifact_path_traversal_is_rejected(self) -> None:
        context = self.create()
        with self.assertRaises(ArtifactError):
            self.artifacts.read_text(context["task_id"], "../../.env")

    def test_version_diff_reports_changed_fields(self) -> None:
        context = self.create()
        self.orchestrator.run_stage(context["task_id"], "question_understanding")
        latest = self.artifacts.load_context(context["task_id"])
        left = latest["versions"][0]["version_id"]
        latest["iteration"] = 2
        second = self.artifacts.snapshot(
            context["task_id"],
            latest,
            stage="question_understanding",
            trigger="test",
            changed_fields=["iteration"],
        )
        diff = self.artifacts.version_diff(context["task_id"], left, second["version_id"])
        self.assertGreater(diff["change_count"], 0)
        self.assertIn("iteration", {item["path"] for item in diff["changes"]})


if __name__ == "__main__":
    unittest.main()
