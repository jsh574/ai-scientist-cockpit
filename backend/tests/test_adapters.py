from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.app.adapters import (
    AgentRegistry,
    ProjectPlanningWorkflowClient,
    _load_file,
    _load_package,
    canonical_question_card,
    evidence_mapping_request,
    hypothesis_request,
    knowledge_question_card,
    planning_request,
)
from backend.app.settings import Settings


class AdapterContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = {
            "task_id": "task_test",
            "iteration": 1,
            "user_input": {
                "original_question": "神经炎症如何影响 Tau 病理扩散？",
                "user_constraints": {"language": "zh", "max_hypotheses": 5},
            },
        }

    def downstream_context(self) -> dict:
        return {
            **self.context,
            "question_card": {
                "core_question": "神经炎症是否促进 Tau 扩散？",
                "research_object": {"name": "阿尔茨海默病"},
            },
            "literature_cards": [{
                "literature_id": "lit_001",
                "title": "Neuroinflammation and tau progression",
                "authors": ["Author A"],
                "year": 2024,
                "source": "Demo Journal",
                "doi": "10.0000/demo",
                "url": "https://example.org/demo",
                "literature_type": "cohort",
                "relevance_score": 0.9,
                "main_findings": ["炎症指标与后续 Tau 变化相关"],
                "related_concepts": ["神经炎症", "Tau 扩散"],
            }],
            "evidence_cards": [{
                "evidence_id": "ev_001",
                "claim": "炎症指标升高与后续 Tau 扩散相关",
                "source_literature_id": "lit_001",
                "evidence_type": "cohort",
                "support_direction": "support",
                "related_concepts": ["神经炎症", "Tau 扩散"],
                "strength_score": 0.82,
                "summary": "纵向观察支持时间关联",
            }],
            "knowledge_gaps": [{
                "gap_id": "gap_001",
                "description": "因果方向未知",
                "related_concepts": ["神经炎症", "Tau 扩散"],
            }],
            "hypothesis_cards": [{
                "hypothesis_id": "hyp_001",
                "statement": "神经炎症可能促进 Tau 扩散",
                "rationale": "纵向关联提示可能存在时间路径",
                "based_on_evidence_ids": ["ev_001"],
                "related_gap_ids": ["gap_001"],
                "target_variables": ["炎症指标", "Tau 变化"],
                "expected_observation": "炎症指标升高先于 Tau 变化",
                "validation_idea": "使用纵向公开队列进行滞后回归",
                "initial_scores": {
                    "novelty": 0.7,
                    "testability": 0.9,
                    "relevance": 0.9,
                    "risk": 0.3,
                },
            }],
        }

    def test_question_card_is_normalized_for_frontend(self) -> None:
        card = canonical_question_card(
            {
                "research_object": "阿尔茨海默病",
                "key_concepts": ["神经炎症"],
                "key_variables": [{"name": "Tau", "role": "outcome", "category": "protein"}],
                "sub_questions": ["炎症是否先于 Tau 扩散？"],
                "search_keywords": ["neuroinflammation", "tau spread"],
            }
        )
        self.assertEqual(card["research_object"]["name"], "阿尔茨海默病")
        self.assertEqual(card["key_concepts"][0]["name"], "神经炎症")
        self.assertEqual(card["sub_questions"][0]["sub_question_id"], "sq_001")
        self.assertEqual(card["search_keywords"]["zh"][0], "neuroinflammation")

    def test_problem_agent_uses_unified_envelope(self) -> None:
        class FakeLLM:
            mock = True
            model = "contract-test"

            def chat_json(self, *_args, **_kwargs):
                return {"core_question": "神经炎症如何影响 Tau 病理扩散？", "confidence": 0.9}

        with patch("backend.app.adapters.ProjectLLMClient", return_value=FakeLLM()):
            response = AgentRegistry(Settings.from_env()).run(
                "question_understanding", self.context
            )
        self.assertEqual(response["metadata"]["stage"], "question_understanding")
        self.assertEqual(response["metadata"]["status"], "success")
        self.assertIn("question_card", response["payload"])
        self.assertIn("overall_score", response["self_review"])

    def test_knowledge_agent_rejects_missing_environment_credential(self) -> None:
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "", "QWEN_API_KEY": ""}):
            response = AgentRegistry(Settings.from_env()).run(
                "knowledge_integration",
                {**self.context, "question_card": {}},
            )
        self.assertEqual(response["metadata"]["status"], "failed")
        self.assertFalse(response["self_review"]["passed"])

    def test_knowledge_parser_accepts_adapted_question_card(self) -> None:
        settings = Settings.from_env()
        module = _load_package(settings.knowledge_agent_root, "knowledge_integration_agent.agent")
        card = knowledge_question_card(
            {
                "core_question": "神经炎症是否促进 Tau 扩散？",
                "research_object": {"name": "阿尔茨海默病", "type": "disease", "aliases": []},
                "domain": ["biomedicine"],
                "key_concepts": [{"name": "神经炎症"}, {"name": "Tau 扩散"}],
                "key_variables": [{"name": "神经炎症", "type": "factor", "role": "independent"}],
                "sub_questions": [{"sub_question_id": "sq_001", "content": "时间顺序如何？"}],
                "search_keywords": {"zh": ["神经炎症"], "en": ["tau spread"]},
            }
        )
        parsed, missing = module.QuestionParser().parse(
            {"task_id": "task_test", "iteration": 1, "input": {"question_card": card}}
        )
        self.assertEqual(missing, [])
        self.assertEqual(parsed.research_object, "阿尔茨海默病")

    def test_hypothesis_agent_accepts_adapted_upstream_context(self) -> None:
        settings = Settings.from_env()
        module = _load_file(settings.hypothesis_agent_file, "hypothesis_contract_test")
        request = hypothesis_request(
            {
                **self.context,
                "question_card": {
                    "core_question": "神经炎症是否促进 Tau 扩散？",
                    "research_object": {"name": "阿尔茨海默病"},
                    "key_concepts": [{"name": "神经炎症"}, {"name": "Tau 扩散"}],
                    "key_variables": [{"name": "神经炎症"}, {"name": "Tau 扩散"}],
                },
                "evidence_cards": [{
                    "evidence_id": "ev_001",
                    "claim": "炎症与 Tau 扩散相关",
                    "related_concepts": ["神经炎症", "Tau 扩散"],
                    "summary": "纵向观察支持时间关联",
                }],
                "knowledge_gaps": [{
                    "gap_id": "gap_001",
                    "description": "因果方向未知",
                    "related_concepts": ["神经炎症", "Tau 扩散"],
                }],
            }
        )
        normalized = module.HypothesisGenerationAgent().validate_input(request)
        self.assertEqual(normalized["task_id"], "task_test")
        self.assertEqual(normalized["evidence_cards"][0]["evidence_id"], "ev_001")

    def test_evidence_mapping_request_normalizes_upstream_aliases(self) -> None:
        request = evidence_mapping_request(self.downstream_context())
        evidence = request["evidence_cards"][0]
        self.assertEqual(evidence["literature_id"], "lit_001")
        self.assertEqual(evidence["source_type"], "cohort")
        self.assertEqual(evidence["support_direction_hint"], "support")
        self.assertEqual(evidence["confidence"], 0.82)

    def test_evidence_mapping_agent_accepts_unified_context(self) -> None:
        settings = Settings.from_env()
        module = _load_package(settings.evidence_agent_root / "src", "evidence_mapping")
        response = module.EvidenceMappingAgent().run_dict(
            evidence_mapping_request(self.downstream_context())
        )
        self.assertEqual(response["metadata"]["stage"], "evidence_mapping")
        self.assertEqual(
            response["payload"]["evidence_map"][0]["hypothesis_id"], "hyp_001"
        )

    def test_planning_workflow_filters_unknown_traceability_ids(self) -> None:
        class FakePlanningLLM:
            def generate_json(self, **_kwargs):
                return {
                    "plan": {
                        "rationale": {
                            "logic_chain": [{
                                "claim": "测试因果路径",
                                "evidence_ids": ["ev_001", "ev_invented"],
                                "source_ids": ["lit_001", "lit_invented"],
                            }]
                        },
                        "references": [
                            {"source_id": "lit_001", "used_for": ["rationale"]},
                            {"source_id": "lit_invented", "used_for": ["rationale"]},
                        ],
                    }
                }

        context = self.downstream_context()
        context["evidence_map"] = [{
            "hypothesis_id": "hyp_001",
            "supporting_evidence_ids": ["ev_001"],
            "opposing_evidence_ids": [],
            "uncertain_evidence_ids": [],
            "evidence_summary": {"support": "支持", "oppose": "", "uncertain": ""},
            "evidence_strength_score": 0.82,
            "main_limitations": ["缺少干预证据"],
            "needs_more_evidence": True,
        }]
        request = planning_request(context)
        service = _load_package(Settings.from_env().planning_agent_root, "planning_agent.service")
        client = ProjectPlanningWorkflowClient(llm=FakePlanningLLM())
        response = service.run_planning_agent(
            request, dify_client=client, max_packages=1, max_parallel_calls=1
        )
        plan = response["payload"]["plans"][0]["plan"]
        logic = plan["rationale"]["logic_chain"][0]
        self.assertEqual(logic["evidence_ids"], ["ev_001"])
        self.assertEqual(logic["source_ids"], ["lit_001"])
        self.assertEqual(
            [item["source_id"] for item in plan["references"]], ["lit_001"]
        )

    def test_registry_wraps_native_planning_payload(self) -> None:
        class FakePlanningLLM:
            def generate_json(self, **_kwargs):
                return {"plan": {"problem_statement": "测试研究问题"}}

        context = self.downstream_context()
        evidence_response = AgentRegistry(Settings.from_env()).run(
            "evidence_mapping", context
        )
        context["evidence_map"] = evidence_response["payload"]["evidence_map"]
        client = ProjectPlanningWorkflowClient(llm=FakePlanningLLM())
        with patch(
            "backend.app.adapters.ProjectPlanningWorkflowClient", return_value=client
        ):
            response = AgentRegistry(Settings.from_env()).run(
                "research_planning", context
            )
        self.assertEqual(response["metadata"]["stage"], "research_planning")
        self.assertIn("research_plan", response["payload"])
        self.assertEqual(
            response["payload"]["research_plan"]["plans"][0]["plan"]["problem_statement"],
            "测试研究问题",
        )


if __name__ == "__main__":
    unittest.main()
