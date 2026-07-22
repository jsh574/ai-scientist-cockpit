import json
from pathlib import Path

from knowledge_integration_agent import KnowledgeIntegrationAdapter, KnowledgeIntegrationAgent
from knowledge_integration_agent.agent import (
    EvidenceExtractor,
    GapSynthesizer,
    LiteratureExtractor,
    RetrievalService,
    SearchQueryPlanner,
    SourceVerifier,
)
from knowledge_integration_agent.llm import LLMClient, QwenDashScopeClient


class FakeLLM(LLMClient):
    def generate_json(self, *, system_prompt, user_payload, expected_schema):
        if expected_schema == "search_strategy":
            return {
                "queries": [
                    {
                        "query": "cosmic inflation primordial gravitational waves B-mode polarization",
                        "rationale": "targets physical evidence for early-universe inflation",
                        "priority": 0.95,
                    },
                    {
                        "query": "CMB B mode polarization tensor to scalar ratio inflation",
                        "rationale": "connects observable proxies to model constraints",
                        "priority": 0.9,
                    },
                    {
                        "query": "cosmic inflation primordial gravitational waves B-mode polarization",
                        "rationale": "duplicate that code should remove",
                        "priority": 0.7,
                    },
                ]
            }
        if expected_schema == "literature_cards":
            return {
                "literature_cards": [
                    {
                        "main_findings": [
                            "B-mode polarization is a key observable proxy for primordial tensor modes.",
                            "Foreground removal remains a major methodological constraint.",
                        ],
                        "related_concepts": [
                            "cosmic inflation",
                            "primordial gravitational waves",
                            "B-mode polarization",
                        ],
                    }
                ]
            }
        if expected_schema == "evidence_cards":
            return {
                "evidence_cards": [
                    {
                        "claim": "CMB B-mode polarization can constrain primordial gravitational waves.",
                        "evidence_type": "observational_result",
                        "support_direction": "support",
                        "related_concepts": [
                            "primordial gravitational waves",
                            "B-mode polarization",
                        ],
                        "strength_score": 1.4,
                        "summary": "The abstract links B-mode observations to tensor-mode constraints.",
                        "limitations": ["foreground contamination", "instrument sensitivity"],
                    },
                    {
                        "claim": "This card has no source and must be repaired by code.",
                        "evidence_type": "review_summary",
                        "support_direction": "uncertain",
                        "related_concepts": ["cosmic inflation"],
                        "strength_score": -0.2,
                        "summary": "Missing source_literature_id on purpose.",
                        "limitations": [],
                    },
                ]
            }
        if expected_schema == "knowledge_gaps":
            return {
                "knowledge_gaps": [
                    {
                        "description": "Whether observed B-mode signals can be separated from foregrounds remains uncertain.",
                        "gap_type": "method_limitation",
                        "related_concepts": [
                            "B-mode polarization",
                            "primordial gravitational waves",
                        ],
                        "related_evidence_ids": ["ev_999"],
                        "importance_score": 0.91,
                        "why_it_matters_for_hypothesis_generation": "It motivates hypotheses about validation pipelines and discriminating observations.",
                    }
                ]
            }
        if expected_schema == "source_relevance":
            return {
                "decisions": [
                    {
                        "source_index": source["source_index"],
                        "relevance_score": 0.9,
                        "keep": True,
                        "reason": "kept by default fake LLM",
                    }
                    for source in user_payload["sources"]
                ]
            }
        if expected_schema == "self_review":
            return {
                "content_quality_score": 0.88,
                "issues": ["Need broader cross-database coverage in a real run."],
                "suggestions": ["Add NASA ADS and arXiv records for astronomy questions."],
            }
        raise AssertionError(expected_schema)


class FlakySearchStrategyLLM(FakeLLM):
    def __init__(self):
        self.search_strategy_attempts = 0

    def generate_json(self, *, system_prompt, user_payload, expected_schema):
        if expected_schema == "search_strategy":
            self.search_strategy_attempts += 1
            if self.search_strategy_attempts < 3:
                raise RuntimeError("temporary qwen failure")
        return super().generate_json(
            system_prompt=system_prompt,
            user_payload=user_payload,
            expected_schema=expected_schema,
        )


class AlwaysFailLLM(FakeLLM):
    def __init__(self):
        self.search_strategy_attempts = 0

    def generate_json(self, *, system_prompt, user_payload, expected_schema):
        if expected_schema == "search_strategy":
            self.search_strategy_attempts += 1
            raise RuntimeError("persistent qwen failure")
        return super().generate_json(
            system_prompt=system_prompt,
            user_payload=user_payload,
            expected_schema=expected_schema,
        )


class ManyEvidenceCardsLLM(FakeLLM):
    def generate_json(self, *, system_prompt, user_payload, expected_schema):
        if expected_schema == "evidence_cards":
            return {
                "evidence_cards": [
                    {
                        "claim": f"Evidence claim {index}",
                        "evidence_type": "observational_result",
                        "support_direction": "support",
                        "related_concepts": ["test concept"],
                        "strength_score": 0.8,
                        "summary": f"Evidence summary {index}",
                        "limitations": [],
                    }
                    for index in range(1, 9)
                ]
            }
        return super().generate_json(
            system_prompt=system_prompt,
            user_payload=user_payload,
            expected_schema=expected_schema,
        )


class FailingSecondEvidenceLLM(FakeLLM):
    def generate_json(self, *, system_prompt, user_payload, expected_schema):
        if expected_schema == "evidence_cards":
            if user_payload["source_literature_id"] == "lit_002":
                raise RuntimeError("evidence timeout for lit_002")
            return super().generate_json(
                system_prompt=system_prompt,
                user_payload=user_payload,
                expected_schema=expected_schema,
            )
        return super().generate_json(
            system_prompt=system_prompt,
            user_payload=user_payload,
            expected_schema=expected_schema,
        )


class AlwaysFailEvidenceLLM(FakeLLM):
    def generate_json(self, *, system_prompt, user_payload, expected_schema):
        if expected_schema == "evidence_cards":
            raise RuntimeError(
                f"evidence timeout for {user_payload['source_literature_id']}"
            )
        return super().generate_json(
            system_prompt=system_prompt,
            user_payload=user_payload,
            expected_schema=expected_schema,
        )


class RelevanceFilterLLM(FakeLLM):
    def generate_json(self, *, system_prompt, user_payload, expected_schema):
        if expected_schema == "source_relevance":
            decisions = []
            for source in user_payload["sources"]:
                title = source["title"].lower()
                relevant = "consciousness" in title or "b-mode" in title
                decisions.append(
                    {
                        "source_index": source["source_index"],
                        "relevance_score": 0.9 if relevant else 0.1,
                        "keep": relevant,
                        "reason": "topic match" if relevant else "off topic",
                    }
                )
            return {"decisions": decisions}
        return super().generate_json(
            system_prompt=system_prompt,
            user_payload=user_payload,
            expected_schema=expected_schema,
        )


class FakeLiteratureClient:
    name = "fake_api"

    def search(self, query, *, limit):
        return [
            {
                "title": "B-mode polarization and primordial gravitational waves",
                "authors": ["A. Researcher", "B. Scientist"],
                "year": 2024,
                "source": "Astrophysical Journal",
                "doi": "10.1234/example.1",
                "url": "https://doi.org/10.1234/example.1",
                "literature_type": "research_article",
                "abstract": "CMB B-mode polarization can constrain primordial gravitational waves but foreground removal is difficult.",
                "database": self.name,
            },
            {
                "title": "Unverified local note",
                "authors": [],
                "year": 2024,
                "source": "Unknown",
                "doi": "",
                "url": "",
                "literature_type": "other",
                "abstract": "No verifiable source.",
                "database": self.name,
            },
        ][:limit]


class FailingLiteratureClient:
    name = "failing_api"

    def search(self, query, *, limit):
        raise RuntimeError("database unavailable")


class TwoVerifiedLiteratureClient:
    name = "two_verified_api"

    def search(self, query, *, limit):
        return [
            {
                "title": "B-mode polarization and primordial gravitational waves",
                "authors": ["A. Researcher"],
                "year": 2024,
                "source": "Astrophysical Journal",
                "doi": "10.1234/example.1",
                "url": "https://doi.org/10.1234/example.1",
                "literature_type": "research_article",
                "abstract": "CMB B-mode polarization can constrain primordial gravitational waves.",
                "database": self.name,
            },
            {
                "title": "Foreground removal methods for B-mode observations",
                "authors": ["B. Scientist"],
                "year": 2025,
                "source": "Cosmology Journal",
                "doi": "10.1234/example.2",
                "url": "https://doi.org/10.1234/example.2",
                "literature_type": "research_article",
                "abstract": "Foreground removal limits B-mode interpretation.",
                "database": self.name,
            },
        ][:limit]


class PromptCapturingLLM(FakeLLM):
    def __init__(self):
        self.knowledge_gap_prompt = ""

    def generate_json(self, *, system_prompt, user_payload, expected_schema):
        if expected_schema == "knowledge_gaps":
            self.knowledge_gap_prompt = system_prompt
        return super().generate_json(
            system_prompt=system_prompt,
            user_payload=user_payload,
            expected_schema=expected_schema,
        )


class MixedRelevanceLiteratureClient:
    name = "mixed_relevance_api"

    def search(self, query, *, limit):
        return [
            {
                "title": "Consciousness disorders and network connectivity",
                "authors": ["A. Neuroscientist"],
                "year": 2025,
                "source": "Neuroscience Journal",
                "doi": "10.1234/consciousness",
                "url": "https://doi.org/10.1234/consciousness",
                "literature_type": "research_article",
                "abstract": "Brain network connectivity is related to consciousness disorders.",
                "database": self.name,
            },
            {
                "title": "Basic-patch mutations in bacteriophage T4 Rad50",
                "authors": ["A. Biochemist"],
                "year": 2025,
                "source": "Biochemistry Journal",
                "doi": "10.1234/rad50",
                "url": "https://doi.org/10.1234/rad50",
                "literature_type": "research_article",
                "abstract": "Rad50 nuclease activity in bacteriophage DNA repair.",
                "database": self.name,
            },
        ][:limit]


def make_request(question_card=None):
    return {
        "task_id": "task_001",
        "stage": "knowledge_integration",
        "iteration": 1,
        "input": {
            "question_card": question_card
            or {
                "question_id": "q_001",
                "core_question": "How can primordial gravitational waves be detected?",
                "research_object": "early-universe inflation",
                "domain": ["cosmology", "astronomy"],
                "key_concepts": [
                    "cosmic inflation",
                    "primordial gravitational waves",
                    "B-mode polarization",
                ],
                "key_variables": [
                    {"name": "tensor-to-scalar ratio", "type": "observable_proxy"}
                ],
                "sub_questions": [
                    "Which observations can distinguish primordial signals from foregrounds?"
                ],
                "search_keywords": ["CMB B-mode", "inflation gravitational waves"],
            },
            "search_policy": {
                "max_papers": 5,
                "max_queries": 4,
                "must_verify_sources": True,
                "forbidden_actions": ["invent_references", "invent_dataset_url"],
            },
        },
        "output_schema": "knowledge_integration.schema.json",
    }


def test_search_query_planner_uses_llm_expansion_then_code_dedupes_and_limits():
    agent = KnowledgeIntegrationAgent(
        llm_client=FakeLLM(), literature_clients=[FakeLiteratureClient()]
    )
    question, missing = agent.question_parser.parse(make_request())

    queries = agent.search_query_planner.build_queries(question)

    assert missing == []
    assert queries == [
        {
            "query": "cosmic inflation primordial gravitational waves B-mode polarization",
            "rationale": "targets physical evidence for early-universe inflation",
            "priority": 0.95,
        },
        {
            "query": "CMB B mode polarization tensor to scalar ratio inflation",
            "rationale": "connects observable proxies to model constraints",
            "priority": 0.9,
        },
    ]


def test_agent_uses_api_sources_llm_extractors_and_code_validators():
    response = KnowledgeIntegrationAgent(
        llm_client=FakeLLM(), literature_clients=[FakeLiteratureClient()]
    ).run(make_request())

    assert response["metadata"]["status"] == "success"
    payload = response["payload"]
    assert len(payload["literature_cards"]) == 1
    assert payload["literature_cards"][0]["main_findings"]
    assert payload["literature_cards"][0]["related_concepts"]
    assert payload["evidence_cards"][0]["source_literature_id"] == "lit_001"
    assert payload["evidence_cards"][0]["strength_score"] == 1.0
    assert payload["knowledge_gaps"][0]["related_evidence_ids"] == ["ev_001"]
    assert response["self_review"]["dimension_scores"]["content_quality"] == 0.88


def test_retrieval_reports_each_database_search_as_json_serializable_events():
    agent = KnowledgeIntegrationAgent(
        llm_client=FakeLLM(),
        literature_clients=[FakeLiteratureClient(), FailingLiteratureClient()],
    )
    question, missing = agent.question_parser.parse(make_request())
    events = []

    sources = RetrievalService(
        [FakeLiteratureClient(), FailingLiteratureClient()]
    ).retrieve(
        question,
        [{"query": "B-mode polarization"}],
        progress_callback=events.append,
    )

    assert missing == []
    assert sources
    assert [event["event"] for event in events] == [
        "retrieval_database_started",
        "retrieval_database_completed",
        "retrieval_database_started",
        "retrieval_database_failed",
    ]
    assert [events[0]["payload"]["database"], events[2]["payload"]["database"]] == [
        "fake_api",
        "failing_api",
    ]
    json.dumps(events, ensure_ascii=False)


def test_agent_emits_four_completed_stage_outputs_without_changing_final_payload():
    events = []
    agent = KnowledgeIntegrationAgent(
        llm_client=FakeLLM(), literature_clients=[FakeLiteratureClient()]
    )

    response = agent.run(make_request(), progress_callback=events.append)

    completed_event_names = {
        "retrieval_completed",
        "literature_extraction_completed",
        "evidence_extraction_completed",
        "gap_synthesis_completed",
    }
    completed_events = [
        event
        for event in events
        if event["event"] in completed_event_names
    ]
    assert [event["event"] for event in completed_events] == [
        "retrieval_completed",
        "literature_extraction_completed",
        "evidence_extraction_completed",
        "gap_synthesis_completed",
    ]
    assert completed_events[0]["payload"].keys() == {"retrieved_sources"}
    assert completed_events[1]["payload"] == {
        "literature_cards": response["payload"]["literature_cards"]
    }
    assert completed_events[2]["payload"] == {
        "evidence_cards": response["payload"]["evidence_cards"]
    }
    assert completed_events[3]["payload"] == {
        "knowledge_gaps": response["payload"]["knowledge_gaps"]
    }
    assert all(event["metadata"]["status"] == "in_progress" for event in events)
    assert response.keys() == {"metadata", "payload", "self_review"}
    assert response["payload"].keys() == {
        "literature_cards",
        "evidence_cards",
        "knowledge_gaps",
    }
    json.dumps(events, ensure_ascii=False)


def test_gap_synthesizer_prompt_requests_directions_without_concrete_hypotheses():
    llm = PromptCapturingLLM()
    agent = KnowledgeIntegrationAgent(
        llm_client=llm, literature_clients=[FakeLiteratureClient()]
    )
    question, missing = agent.question_parser.parse(make_request())

    gaps = GapSynthesizer(llm).synthesize(
        question,
        [
            {
                "evidence_id": "ev_001",
                "claim": "Foregrounds complicate B-mode interpretation.",
            }
        ],
    )

    prompt = llm.knowledge_gap_prompt.lower()
    assert missing == []
    assert gaps[0].keys() == {
        "gap_id",
        "description",
        "gap_type",
        "related_concepts",
        "related_evidence_ids",
        "importance_score",
        "why_it_matters_for_hypothesis_generation",
    }
    assert "do not propose or state any concrete hypothesis" in prompt
    assert "research directions and recommendations" in prompt
    assert "why_it_matters_for_hypothesis_generation" in prompt


def test_llm_calls_retry_three_times_before_success():
    llm = FlakySearchStrategyLLM()

    response = KnowledgeIntegrationAgent(
        llm_client=llm, literature_clients=[FakeLiteratureClient()]
    ).run(make_request())

    assert llm.search_strategy_attempts == 3
    assert response["metadata"]["status"] == "success"


def test_llm_calls_report_failure_after_three_failed_attempts():
    llm = AlwaysFailLLM()

    response = KnowledgeIntegrationAgent(
        llm_client=llm, literature_clients=[FakeLiteratureClient()]
    ).run(make_request())

    assert llm.search_strategy_attempts == 3
    assert response["metadata"]["status"] == "failed"
    assert "persistent qwen failure" in response["self_review"]["issues"][0]


def test_relevance_filter_removes_low_relevance_retrieved_sources():
    response = KnowledgeIntegrationAgent(
        llm_client=RelevanceFilterLLM(),
        literature_clients=[MixedRelevanceLiteratureClient()],
    ).run(make_request())

    titles = {card["title"] for card in response["payload"]["literature_cards"]}
    assert titles == {"Consciousness disorders and network connectivity"}


def test_self_review_score_excludes_question_coverage_dimension():
    response = KnowledgeIntegrationAgent(
        llm_client=FakeLLM(), literature_clients=[FakeLiteratureClient()]
    ).run(make_request())

    dimension_scores = response["self_review"]["dimension_scores"]
    assert "question_coverage" not in dimension_scores
    expected = round(
        (
            dimension_scores["source_verifiability"]
            + dimension_scores["evidence_traceability"]
            + dimension_scores["gap_value"]
            + dimension_scores["content_quality"]
        )
        / 4,
        2,
    )
    assert response["self_review"]["overall_score"] == expected


def test_missing_required_question_fields_returns_failure_with_requested_resources():
    request = make_request(
        question_card={
            "question_id": "q_001",
            "research_object": "early-universe inflation",
            "key_concepts": ["B-mode polarization"],
            "key_variables": [],
            "sub_questions": [],
            "search_keywords": [],
        }
    )

    response = KnowledgeIntegrationAgent(
        llm_client=FakeLLM(), literature_clients=[FakeLiteratureClient()]
    ).run(request)

    assert response["metadata"]["status"] == "failed"
    assert response["payload"] == {
        "literature_cards": [],
        "evidence_cards": [],
        "knowledge_gaps": [],
    }
    assert response["self_review"]["passed"] is False
    assert "question_card.core_question" in response["requested_resources"]


def test_source_verifier_filters_sources_without_doi_or_url():
    sources = [
        {"title": "verified", "doi": "10.1000/test", "url": ""},
        {"title": "also verified", "doi": "", "url": "https://example.org/paper"},
        {"title": "unverified", "doi": "", "url": ""},
    ]

    verified = SourceVerifier().verify(sources, must_verify=True)

    assert [source["title"] for source in verified] == ["verified", "also verified"]


def test_literature_extractor_enforces_data_contract_even_when_llm_omits_fields():
    source = {
        "title": "General Science Paper",
        "authors": ["A"],
        "year": 2025,
        "source": "Science",
        "doi": "10.1126/example",
        "url": "https://doi.org/10.1126/example",
        "literature_type": "research_article",
        "abstract": "A general abstract.",
    }

    cards = LiteratureExtractor(FakeLLM()).extract([source])

    assert cards[0].keys() == {
        "literature_id",
        "title",
        "authors",
        "year",
        "source",
        "doi",
        "url",
        "literature_type",
        "relevance_score",
        "main_findings",
        "related_concepts",
    }


def test_qwen_client_defaults_to_180_second_timeout():
    client = QwenDashScopeClient(api_key="test-key")

    assert client.timeout_seconds == 180


def test_evidence_extractor_limits_each_literature_to_six_cards():
    source = {
        "title": "General Science Paper",
        "abstract": "A general abstract.",
    }
    literature_card = {
        "literature_id": "lit_001",
        "title": "General Science Paper",
        "main_findings": ["finding"],
        "related_concepts": ["concept"],
    }

    cards = EvidenceExtractor(ManyEvidenceCardsLLM()).extract(
        [source], [literature_card]
    )

    assert len(cards) == 6
    assert [card["evidence_id"] for card in cards] == [
        "ev_001",
        "ev_002",
        "ev_003",
        "ev_004",
        "ev_005",
        "ev_006",
    ]


def test_agent_skips_failed_evidence_source_and_keeps_successful_evidence():
    response = KnowledgeIntegrationAgent(
        llm_client=FailingSecondEvidenceLLM(),
        literature_clients=[TwoVerifiedLiteratureClient()],
    ).run(make_request())

    assert response["metadata"]["status"] == "success"
    assert len(response["payload"]["literature_cards"]) == 2
    assert response["payload"]["evidence_cards"]
    assert {
        card["source_literature_id"] for card in response["payload"]["evidence_cards"]
    } == {"lit_001"}
    assert any(
        "lit_002" in issue and "evidence timeout for lit_002" in issue
        for issue in response["self_review"]["issues"]
    )


def test_agent_fails_only_when_all_evidence_extraction_fails_and_preserves_literature():
    response = KnowledgeIntegrationAgent(
        llm_client=AlwaysFailEvidenceLLM(),
        literature_clients=[TwoVerifiedLiteratureClient()],
    ).run(make_request())

    assert response["metadata"]["status"] == "failed"
    assert len(response["payload"]["literature_cards"]) == 2
    assert response["payload"]["evidence_cards"] == []
    assert response["payload"]["knowledge_gaps"] == []
    assert any(
        "no evidence cards were extracted" in issue
        for issue in response["self_review"]["issues"]
    )
    assert any("lit_001" in issue for issue in response["self_review"]["issues"])
    assert any("lit_002" in issue for issue in response["self_review"]["issues"])


def test_adapter_slices_task_context_for_total_control_layer():
    task_context = {
        "task_id": "task_001",
        "iteration": 1,
        "question_card": make_request()["input"]["question_card"],
        "literature_cards": [{"should_not": "be passed back in"}],
        "hypothesis_cards": [{"should_not": "be visible to module 2"}],
    }

    request = KnowledgeIntegrationAdapter().build_request(task_context)

    assert request["task_id"] == "task_001"
    assert request["stage"] == "knowledge_integration"
    assert request["input"].keys() == {"question_card", "search_policy"}
    assert request["input"]["question_card"] == task_context["question_card"]
    assert "literature_cards" not in request["input"]
    assert "hypothesis_cards" not in request["input"]


def test_adapter_forwards_progress_json_to_total_control_callback():
    events = []
    adapter = KnowledgeIntegrationAdapter(
        agent=KnowledgeIntegrationAgent(
            llm_client=FakeLLM(), literature_clients=[FakeLiteratureClient()]
        )
    )
    task_context = {
        "task_id": "task_001",
        "iteration": 1,
        "question_card": make_request()["input"]["question_card"],
    }

    response = adapter.call(task_context, progress_callback=events.append)

    assert response["metadata"]["status"] == "success"
    assert any(event["event"] == "retrieval_database_started" for event in events)
    assert any(event["event"] == "gap_synthesis_completed" for event in events)


def test_knowledge_integration_schema_declares_total_control_payload_fields():
    schema_path = Path("schemas/knowledge_integration.schema.json")

    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["required"] == ["literature_cards", "evidence_cards", "knowledge_gaps"]
    assert "source_literature_id" in schema["properties"]["evidence_cards"]["items"]["required"]
