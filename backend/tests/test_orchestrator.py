from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.agent_protocol import AGENT_SPECS, STAGE_ORDER
from backend.app.artifact_service import ArtifactError, ArtifactService
from backend.app.contracts import HumanReviewRequest, TaskCreateRequest
from backend.app.controller_assistant import ControllerAssistant
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


class FailingHypothesisRegistry(FakeRegistry):
    def run(self, stage: str, context: dict, feedback: str | None = None) -> dict:
        if stage == "hypothesis_generation":
            raise AssertionError("Hypothesis generation should be blocked before agent execution")
        return super().run(stage, context, feedback)


class UnknownGapHypothesisRegistry(FakeRegistry):
    def run(self, stage: str, context: dict, feedback: str | None = None) -> dict:
        response = super().run(stage, context, feedback)
        if stage == "hypothesis_generation":
            response["payload"]["hypothesis_cards"][0]["related_gap_ids"] = ["gap_missing"]
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
        self.assertFalse(latest["final_review"]["passed"])
        self.assertLess(latest["final_review"]["overall_score"], 0.78)
        self.assertEqual(
            set(latest["final_review"]["dimension_scores"]),
            set(ControllerAssistant.review_weights),
        )
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

    def test_duplicate_human_review_approval_is_idempotent(self) -> None:
        context = self.create("manual")
        first = self.orchestrator.run_from(context["task_id"])
        self.assertEqual(first["status"], "human_review")
        request = HumanReviewRequest(
            stage="question_understanding",
            decision="accept",
            comment="Looks testable.",
            approval_id="approval_question_understanding_msg_001_accept",
        )

        accepted = self.orchestrator.submit_review(context["task_id"], request)
        after_first = self.artifacts.load_context(context["task_id"])
        review_count = len(after_first["reviews"])
        version_count = len(after_first["versions"])
        event_count = len(self.artifacts.read_events(context["task_id"]))

        duplicate = self.orchestrator.submit_review(context["task_id"], request)
        after_second = self.artifacts.load_context(context["task_id"])

        self.assertEqual(accepted["status"], "passed")
        self.assertEqual(duplicate["status"], "passed")
        self.assertTrue(duplicate["idempotent"])
        self.assertEqual(
            duplicate["approval_id"], "approval_question_understanding_msg_001_accept"
        )
        self.assertEqual(len(after_second["reviews"]), review_count)
        self.assertEqual(len(after_second["versions"]), version_count)
        self.assertEqual(len(self.artifacts.read_events(context["task_id"])), event_count)
        self.assertEqual(
            after_second["extensions"]["processed_approvals"][
                "approval_question_understanding_msg_001_accept"
            ]["status"],
            "passed",
        )

    def test_final_review_is_persisted_before_human_review(self) -> None:
        context = self.create()
        for stage in AGENT_SPECS:
            if stage == "final_review":
                break
            result = self.orchestrator.run_stage(context["task_id"], stage)
            self.assertEqual(result["status"], "passed")

        latest = self.artifacts.load_context(context["task_id"])
        latest["mode"] = "hybrid"
        self.artifacts.save_context(context["task_id"], latest)

        result = self.orchestrator.run_stage(context["task_id"], "final_review")
        persisted = self.artifacts.load_context(context["task_id"])

        self.assertEqual(result["status"], "human_review")
        self.assertEqual(persisted["current_stage"], "human_review")
        self.assertIsNotNone(persisted["final_review"])
        self.assertLess(persisted["final_review"]["overall_score"], 0.78)
        self.assertIn(
            "final_review",
            self.artifacts.latest_stage_output(context["task_id"], "final_review")["payload"],
        )

    def test_feedback_rerun_stays_in_current_iteration(self) -> None:
        context = self.create()
        self.orchestrator.run_from(context["task_id"])
        result = self.orchestrator.apply_feedback(
            context["task_id"],
            "hypothesis_generation",
            "Narrow the population and rerank hypotheses.",
            rerun_downstream=False,
        )
        self.assertEqual(result["task_context"]["iteration"], 1)
        latest = self.artifacts.load_context(context["task_id"])
        self.assertEqual(len(latest["feedback_events"]), 1)
        self.assertGreaterEqual(len(latest["versions"]), 8)

    def test_stage_history_replaces_local_rerun_within_iteration(self) -> None:
        context = self.create()
        self.orchestrator.run_from(context["task_id"])
        self.orchestrator.apply_feedback(
            context["task_id"],
            "research_planning",
            "Tighten the experiment design.",
            rerun_downstream=False,
        )
        self.orchestrator.run_from(context["task_id"], "research_planning")

        history = self.artifacts.list_stage_history(
            context["task_id"], list(STAGE_ORDER)
        )
        identities = [
            (item["metadata"]["iteration"], item["metadata"]["stage"])
            for item in history
        ]

        self.assertEqual(len(identities), len(set(identities)))
        self.assertIn((1, "research_planning"), identities)
        self.assertIn((1, "final_review"), identities)
        self.assertNotIn((2, "research_planning"), identities)
        self.assertNotIn((2, "final_review"), identities)

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

    def test_hypothesis_generation_is_blocked_without_knowledge_gaps(self) -> None:
        self.orchestrator.registry = FailingHypothesisRegistry()
        context = self.create()
        context.update(
            {
                "question_card": {"question_id": "q_001", "core_question": "test"},
                "evidence_cards": [{"evidence_id": "ev_001"}],
                "knowledge_gaps": [],
                "current_stage": "hypothesis_generation",
            }
        )
        self.artifacts.save_context(context["task_id"], context)

        result = self.orchestrator.run_stage(context["task_id"], "hypothesis_generation")

        self.assertEqual(result["status"], "retry")
        self.assertEqual(result["response"]["payload"]["hypothesis_cards"], [])
        self.assertIn("knowledge_gaps is empty", " ".join(result["review"]["issues"]))
        events = self.artifacts.read_events(context["task_id"])
        self.assertIn("stage_preflight_blocked", {event["type"] for event in events})

    def test_hypothesis_review_requires_known_gap_references(self) -> None:
        self.orchestrator.registry = UnknownGapHypothesisRegistry()
        context = self.create()
        self.assertEqual(
            self.orchestrator.run_stage(context["task_id"], "question_understanding")[
                "status"
            ],
            "passed",
        )
        self.assertEqual(
            self.orchestrator.run_stage(context["task_id"], "knowledge_integration")[
                "status"
            ],
            "passed",
        )

        result = self.orchestrator.run_stage(context["task_id"], "hypothesis_generation")

        self.assertEqual(result["status"], "retry")
        self.assertIn("Unknown knowledge gap IDs", " ".join(result["review"]["issues"]))

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

    def test_node_run_history_preserves_input_output_and_review(self) -> None:
        context = self.create()
        result = self.orchestrator.run_stage(
            context["task_id"], "question_understanding"
        )

        runs = self.artifacts.list_node_runs(
            context["task_id"], "question_understanding"
        )
        self.assertEqual(len(runs), 1)
        detail = self.artifacts.get_node_run(
            context["task_id"],
            "question_understanding",
            result["node_run_id"],
        )
        self.assertEqual(detail["metadata"]["status"], "passed")
        self.assertEqual(detail["input"]["task_id"], context["task_id"])
        self.assertIsNotNone(detail["output"])
        self.assertIsNotNone(detail["review"])

    def test_namespaced_agent_extensions_survive_context_merge(self) -> None:
        from backend.app.agent_protocol import get_agent_spec, merge_payload, slice_context

        context = self.create()
        spec = get_agent_spec("question_understanding")
        merged = merge_payload(
            context,
            spec,
            {
                "question_card": {"core_question": "test"},
                "extensions": {"diagnostic_trace": "trace-1"},
            },
        )

        self.assertEqual(
            merged["extensions"][spec.agent_id]["diagnostic_trace"], "trace-1"
        )
        sliced = slice_context(merged, spec)
        self.assertEqual(sliced["extensions"]["diagnostic_trace"], "trace-1")

    def test_operator_override_is_audited_without_replacing_source_context(self) -> None:
        context = self.create()
        original = context["user_input"]
        override = {
            "user_input": {
                **original,
                "original_question": "Operator override question",
            }
        }
        result = self.orchestrator.run_stage(
            context["task_id"],
            "question_understanding",
            input_override=override,
        )
        detail = self.artifacts.get_node_run(
            context["task_id"],
            "question_understanding",
            result["node_run_id"],
        )
        stored_context = self.artifacts.load_context(context["task_id"])

        self.assertEqual(
            detail["input"]["user_input"]["original_question"],
            "Operator override question",
        )
        self.assertEqual(
            stored_context["user_input"]["original_question"],
            original["original_question"],
        )
        overrides = [
            artifact
            for artifact in self.artifacts.list_artifacts(context["task_id"])
            if artifact["path"].startswith("operator_overrides/")
        ]
        self.assertEqual(len(overrides), 1)

    def test_stage_input_receives_retrieved_attachment_chunks(self) -> None:
        context = self.create()
        self.artifacts.add_attachment(
            context["task_id"],
            "background.md",
            (
                "Inflammation markers and cytokines should be measured before tau PET follow-up. "
                "This protocol note is relevant to the question."
            ).encode("utf-8"),
            "text/markdown",
            context_char_limit=10_000,
            message_id="msg_background",
        )

        result = self.orchestrator.run_stage(context["task_id"], "question_understanding")
        detail = self.artifacts.get_node_run(
            context["task_id"],
            "question_understanding",
            result["node_run_id"],
        )
        attachment_context = detail["input"]["attachment_context"]

        self.assertEqual(
            attachment_context["schema_version"],
            "attachment_retrieval_v1",
        )
        self.assertEqual(attachment_context["retrieval_mode"], "stage_scoped_chunks")
        self.assertGreaterEqual(len(attachment_context["chunks"]), 1)
        self.assertIn("citation_id", attachment_context["chunks"][0])
        self.assertIn(
            "cytokines",
            detail["input"]["user_input"]["retrieved_attachment_chunks"][0]["text"],
        )

    def test_node_run_diff_compares_persisted_outputs(self) -> None:
        context = self.create()
        first = self.artifacts.begin_node_run(
            context["task_id"], "question_understanding", 1, {"value": 1}
        )
        second = self.artifacts.begin_node_run(
            context["task_id"], "question_understanding", 1, {"value": 2}
        )
        self.artifacts.finish_node_run(
            context["task_id"], "question_understanding", first,
            status="passed", output={"payload": {"score": 1}},
        )
        self.artifacts.finish_node_run(
            context["task_id"], "question_understanding", second,
            status="passed", output={"payload": {"score": 2}},
        )
        diff = self.artifacts.node_run_diff(
            context["task_id"], "question_understanding", first, second
        )
        self.assertEqual(diff["change_count"], 1)
        self.assertEqual(diff["changes"][0]["path"], "payload.score")


if __name__ == "__main__":
    unittest.main()
