from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.adapters import (
    AgentRegistry,
    ProjectLLMClient,
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
            "literature_cards": [
                {
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
                }
            ],
            "evidence_cards": [
                {
                    "evidence_id": "ev_001",
                    "claim": "炎症指标升高与后续 Tau 扩散相关",
                    "source_literature_id": "lit_001",
                    "evidence_type": "cohort",
                    "support_direction": "support",
                    "related_concepts": ["神经炎症", "Tau 扩散"],
                    "strength_score": 0.82,
                    "summary": "纵向观察支持时间关联",
                }
            ],
            "knowledge_gaps": [
                {
                    "gap_id": "gap_001",
                    "description": "因果方向未知",
                    "related_concepts": ["神经炎症", "Tau 扩散"],
                }
            ],
            "hypothesis_cards": [
                {
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
                }
            ],
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

    def test_model_gateway_sends_attachment_context_in_user_message(self) -> None:
        captured: dict = {}

        class FakeCompletions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))]
                )

        client = ProjectLLMClient.__new__(ProjectLLMClient)
        client.model = "test-model"
        client.max_tokens = 4096
        client.enable_thinking = False
        client.background_context = "ATTACHMENT_SENTINEL_7F31"
        client.client = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())
        )

        result = client._complete("system", "base prompt", 0.2)

        self.assertEqual(result, '{"ok": true}')
        user_message = captured["messages"][1]["content"]
        self.assertIn("base prompt", user_message)
        self.assertIn("ATTACHMENT_SENTINEL_7F31", user_message)
        self.assertIn("uploaded attachment context", user_message)
        self.assertEqual(
            client._with_background_context("ATTACHMENT_SENTINEL_7F31").count(
                "ATTACHMENT_SENTINEL_7F31"
            ),
            1,
        )

    def test_reasoning_level_does_not_enable_thinking_without_operator_flag(self) -> None:
        context = {
            **self.context,
            "user_input": {
                **self.context["user_input"],
                "user_constraints": {"reasoning_level": "high"},
            },
        }
        with patch.dict(
            os.environ,
            {
                "DASHSCOPE_API_KEY": "test-key",
                "LLM_MAX_TOKENS": "8192",
                "LLM_MAX_RETRIES": "0",
                "QWEN_ENABLE_THINKING": "false",
            },
        ), patch("openai.OpenAI") as openai_client:
            client = ProjectLLMClient(context)

        self.assertEqual(client.max_tokens, 6144)
        self.assertFalse(client.enable_thinking)
        self.assertEqual(openai_client.call_args.kwargs["max_retries"], 0)

    def test_knowledge_agent_rejects_missing_environment_credential(self) -> None:
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "", "QWEN_API_KEY": ""}):
            response = AgentRegistry(Settings.from_env()).run(
                "knowledge_integration",
                {**self.context, "question_card": {}},
            )
        self.assertEqual(response["metadata"]["status"], "failed")
        self.assertFalse(response["self_review"]["passed"])

    def test_knowledge_agent_bridges_progress_and_feedback_contract(self) -> None:
        captured: dict = {}

        class FakeLLM:
            max_attempts = 1

        class FakeAgent:
            def __init__(self, llm_client, literature_clients):
                self.llm_client = llm_client

        class FakeAdapter:
            def __init__(self, agent, default_search_policy):
                pass

            def call(self, task_context, progress_callback=None):
                captured["task_context"] = task_context
                progress_callback(
                    {
                        "event": "retrieval_database_started",
                        "component": "RetrievalService",
                        "payload": {"database": "crossref", "query": "tau"},
                    }
                )
                return {
                    "metadata": {"status": "success"},
                    "payload": {
                        "literature_cards": [],
                        "evidence_cards": [],
                        "knowledge_gaps": [],
                    },
                    "self_review": {"passed": True, "overall_score": 1.0},
                }

        progress_events: list[dict] = []
        cancellation_checks: list[bool] = []
        package = SimpleNamespace(
            KnowledgeIntegrationAgent=FakeAgent,
            KnowledgeIntegrationAdapter=FakeAdapter,
        )
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test-key"}), patch(
            "backend.app.adapters._load_package", return_value=package
        ), patch(
            "backend.app.adapters.ProjectLLMClient", return_value=FakeLLM()
        ), patch(
            "backend.app.adapters._literature_clients", return_value=[]
        ):
            response = AgentRegistry(Settings.from_env()).run(
                "knowledge_integration",
                {**self.context, "question_card": {}},
                "补充纵向研究。",
                progress_handler=progress_events.append,
                cancellation_checker=lambda: cancellation_checks.append(True),
            )

        self.assertEqual(response["metadata"]["status"], "success")
        self.assertEqual(captured["task_context"]["_feedback"], "补充纵向研究。")
        self.assertEqual(progress_events[0]["node_id"], "source_search:crossref")
        self.assertEqual(progress_events[0]["message"], "正在检索 crossref。")
        self.assertGreaterEqual(len(cancellation_checks), 2)

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
                "evidence_cards": [
                    {
                        "evidence_id": "ev_001",
                        "claim": "炎症与 Tau 扩散相关",
                        "related_concepts": ["神经炎症", "Tau 扩散"],
                        "summary": "纵向观察支持时间关联",
                    }
                ],
                "knowledge_gaps": [
                    {
                        "gap_id": "gap_001",
                        "description": "因果方向未知",
                        "related_concepts": ["神经炎症", "Tau 扩散"],
                    }
                ],
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
        with patch.dict(os.environ, {"EVIDENCE_MAPPING_MODE": "rules"}):
            response = module.EvidenceMappingAgent().run_dict(
                evidence_mapping_request(self.downstream_context())
            )
        self.assertEqual(response["metadata"]["stage"], "evidence_mapping")
        self.assertEqual(response["payload"]["evidence_map"][0]["hypothesis_id"], "hyp_001")

    def test_evidence_mapping_adapter_uses_llm_in_auto_mode(self) -> None:
        settings = Settings.from_env()
        llm_module = _load_package(
            settings.evidence_agent_root / "src", "evidence_mapping.llm"
        )
        llm_output = {
            "bindings": [
                {
                    "evidence_id": "ev_001",
                    "support_direction": "support",
                    "binding_type": "direct_support",
                    "prediction_index": 0,
                    "directness": 0.9,
                    "reliability": 0.85,
                    "sufficiency": 0.75,
                    "applicability": 0.8,
                    "total_score": 8.2,
                    "recheck_note": "LLM contract test",
                    "limitations": [],
                }
            ],
            "evidence_summary": {
                "support": "The evidence supports the hypothesis.",
                "oppose": "No opposing evidence was found.",
                "uncertain": "No uncertain evidence was found.",
            },
            "gaps": [
                {
                    "gap_code": "why_no_oppose",
                    "description": "Confirm that contradictory evidence was searched.",
                    "suggested_evidence_type": "null_result",
                }
            ],
            "evidence_strength_score": 0.75,
            "main_limitations": [],
        }
        with patch.dict(
            os.environ,
            {"DASHSCOPE_API_KEY": "test-key", "EVIDENCE_MAPPING_MODE": "auto"},
        ), patch.object(
            llm_module.QwenCompatibleClient,
            "generate_json",
            autospec=True,
            return_value=llm_output,
        ) as generate_json:
            response = AgentRegistry(settings).run(
                "evidence_mapping", self.downstream_context()
            )

        generate_json.assert_called_once()
        self.assertEqual(response["metadata"]["status"], "success")
        self.assertEqual(
            response["self_review"]["dimension_scores"]["scoring_backend_llm"], 1.0
        )

    def test_planning_request_uses_v01_module_contract(self) -> None:
        request = planning_request(self.downstream_context(), feedback="优先降低样本量")

        self.assertEqual(request["schema_version"], "experiment_planner_input_v1")
        self.assertEqual(request["request_mode"], "single")
        self.assertIn("question_card", request)
        self.assertIn("hypothesis_cards", request)
        self.assertIn("evidence_map", request)
        self.assertEqual(request["_feedback"], "优先降低样本量")

    def test_planning_dify_output_reports_unknown_traceability_ids(self) -> None:
        class FakeDifyClient:
            configured = True

            def run_workflow(self, _inputs):
                return {
                    "plan": {
                        "problem_statement": "测试研究问题",
                        "rationale": {
                            "logic_chain": [
                                {
                                    "claim": "测试因果路径",
                                    "evidence_ids": ["ev_001", "ev_invented"],
                                    "source_ids": ["lit_001", "lit_invented"],
                                }
                            ]
                        },
                        "references": [
                            {"source_id": "lit_001", "used_for": ["rationale"]},
                            {"source_id": "lit_invented", "used_for": ["rationale"]},
                        ],
                    }
                }

        context = self.downstream_context()
        context["evidence_map"] = [
            {
                "hypothesis_id": "hyp_001",
                "supporting_evidence_ids": ["ev_001"],
                "opposing_evidence_ids": [],
                "uncertain_evidence_ids": [],
                "evidence_summary": {"support": "支持", "oppose": "", "uncertain": ""},
                "evidence_strength_score": 0.82,
                "main_limitations": ["缺少干预证据"],
                "needs_more_evidence": True,
            }
        ]
        request = planning_request(context)
        service = _load_package(Settings.from_env().planning_agent_root, "planning_agent.service")
        response = service.run_planning_agent(
            request, dify_client=FakeDifyClient(), max_packages=1, max_parallel_calls=1
        )

        self.assertEqual(response["metadata"]["status"], "partial_success")
        self.assertFalse(response["self_review"]["passed"])
        self.assertTrue(
            any("unknown source" in issue or "unknown evidence" in issue for issue in response["self_review"]["issues"])
        )

    def test_registry_uses_native_planning_dify_client(self) -> None:
        class FakeDifyClient:
            configured = True

            def __init__(self):
                self.calls = []

            def run_workflow(self, inputs):
                self.calls.append(inputs)
                return {"plan": {"problem_statement": "测试研究问题"}}

        context = self.downstream_context()
        with patch.dict(os.environ, {"EVIDENCE_MAPPING_MODE": "rules"}):
            evidence_response = AgentRegistry(Settings.from_env()).run(
                "evidence_mapping", context
            )
        context["evidence_map"] = evidence_response["payload"]["evidence_map"]
        service = _load_package(Settings.from_env().planning_agent_root, "planning_agent.service")
        client = FakeDifyClient()
        with patch.object(service, "DifyWorkflowClient", return_value=client):
            response = AgentRegistry(Settings.from_env()).run("research_planning", context)

        self.assertEqual(response["metadata"]["stage"], "research_planning")
        self.assertIn("research_plan", response["payload"])
        self.assertTrue(client.calls)
        self.assertEqual(
            response["payload"]["research_plan"]["plans"][0]["plan"]["problem_statement"],
            "测试研究问题",
        )


if __name__ == "__main__":
    unittest.main()
