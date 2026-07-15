from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .llm import LLMClient, QwenDashScopeClient
from .retrieval import LiteratureClient, default_literature_clients


REQUIRED_QUESTION_FIELDS = [
    "core_question",
    "research_object",
    "key_concepts",
    "key_variables",
    "sub_questions",
    "search_keywords",
]

ALLOWED_GAP_TYPES = {
    "mechanism_unknown",
    "causal_uncertain",
    "data_missing",
    "method_limitation",
    "contradiction",
}


@dataclass(frozen=True)
class ParsedQuestion:
    task_id: str
    iteration: int
    core_question: str
    research_object: str
    domain: list[str]
    key_concepts: list[str]
    key_variables: list[dict[str, Any]]
    sub_questions: list[str]
    search_keywords: list[str]
    search_policy: dict[str, Any]


class QuestionParser:
    def parse(self, request: dict[str, Any]) -> tuple[ParsedQuestion | None, list[str]]:
        question_card = request.get("input", {}).get("question_card") or {}
        missing = [
            f"question_card.{field}"
            for field in REQUIRED_QUESTION_FIELDS
            if not question_card.get(field)
        ]
        if missing:
            return None, missing

        return (
            ParsedQuestion(
                task_id=request.get("task_id", ""),
                iteration=int(request.get("iteration", 1)),
                core_question=str(question_card["core_question"]),
                research_object=str(question_card["research_object"]),
                domain=list(question_card.get("domain") or []),
                key_concepts=list(question_card["key_concepts"]),
                key_variables=list(question_card["key_variables"]),
                sub_questions=list(question_card["sub_questions"]),
                search_keywords=list(question_card["search_keywords"]),
                search_policy=dict(request.get("input", {}).get("search_policy") or {}),
            ),
            [],
        )


class SearchQueryPlanner:
    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    def build_queries(self, question: ParsedQuestion) -> list[dict[str, Any]]:
        payload = {
            "core_question": question.core_question,
            "research_object": question.research_object,
            "domain": question.domain,
            "key_concepts": question.key_concepts,
            "key_variables": question.key_variables,
            "sub_questions": question.sub_questions,
            "search_keywords": question.search_keywords,
            "instruction": (
                "Generate topic-specific scholarly search queries. Avoid fixed templates. "
                "Use professional English terms, important synonyms, observable proxies, "
                "method terms, and domain databases when useful."
            ),
        }
        result = self.llm_client.generate_json(
            system_prompt=(
                "You design retrieval strategies for ONE current scientific question "
                "selected from or inspired by the Science 125 frontier questions. "
                "Only generate search queries for the question provided in user_payload. "
                "Do not generate strategies for other Science 125 questions. "
                "Return {'queries': [{'query': string, 'rationale': string, 'priority': number}]}."
            ),
            user_payload=payload,
            expected_schema="search_strategy",
        )
        max_queries = int(question.search_policy.get("max_queries") or 10)
        queries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in sorted(
            result.get("queries") or [],
            key=lambda query: float(query.get("priority", 0.5)),
            reverse=True,
        ):
            query_text = " ".join(str(item.get("query", "")).split())
            key = query_text.lower()
            if not query_text or key in seen:
                continue
            seen.add(key)
            queries.append(
                {
                    "query": query_text,
                    "rationale": str(item.get("rationale", "")),
                    "priority": _clamp_score(item.get("priority", 0.5)),
                }
            )
            if len(queries) >= max_queries:
                break
        return queries


class RetryingLLMClient:
    def __init__(self, llm_client: LLMClient, *, max_attempts: int = 3) -> None:
        self.llm_client = llm_client
        self.max_attempts = max_attempts

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        expected_schema: str,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for _ in range(self.max_attempts):
            try:
                return self.llm_client.generate_json(
                    system_prompt=system_prompt,
                    user_payload=user_payload,
                    expected_schema=expected_schema,
                )
            except (TimeoutError, ConnectionError, RuntimeError, ValueError) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error


class RetrievalService:
    def __init__(self, clients: list[LiteratureClient] | None = None) -> None:
        self.clients = clients or default_literature_clients()

    def retrieve(
        self, question: ParsedQuestion, queries: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        max_papers = int(question.search_policy.get("max_papers") or 20)
        per_client_limit = max(1, int(question.search_policy.get("per_client_limit") or 3))
        sources: list[dict[str, Any]] = []
        for query in queries:
            for client in self.clients:
                try:
                    sources.extend(client.search(query["query"], limit=per_client_limit))
                except Exception:
                    continue
            if len(sources) >= max_papers * 3:
                break
        return _dedupe_sources(sources)[:max_papers]


class SourceRelevanceFilter:
    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    def filter(
        self,
        question: ParsedQuestion,
        sources: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not sources:
            return []
        threshold = _clamp_score(question.search_policy.get("relevance_threshold", 0.5))
        result = self.llm_client.generate_json(
            system_prompt=(
                "You are a strict post-retrieval relevance filter. Judge whether each "
                "retrieved scholarly source is directly useful for the CURRENT question "
                "only. Remove off-topic or weakly related sources. Return "
                "{'decisions': [{'source_index': number, 'relevance_score': number, "
                "'keep': boolean, 'reason': string}]}."
            ),
            user_payload={
                "question": {
                    "core_question": question.core_question,
                    "research_object": question.research_object,
                    "domain": question.domain,
                    "key_concepts": question.key_concepts,
                    "sub_questions": question.sub_questions,
                },
                "sources": [
                    {
                        "source_index": index,
                        "title": source.get("title", ""),
                        "source": source.get("source", ""),
                        "year": source.get("year", 0),
                        "abstract": source.get("abstract", ""),
                        "database": source.get("database", ""),
                    }
                    for index, source in enumerate(sources)
                ],
            },
            expected_schema="source_relevance",
        )
        keep_indexes: set[int] = set()
        for decision in result.get("decisions", []):
            source_index = str(decision.get("source_index", ""))
            if not source_index.isdigit():
                continue
            if not bool(decision.get("keep")):
                continue
            if _clamp_score(decision.get("relevance_score", 0.0)) < threshold:
                continue
            keep_indexes.add(int(source_index))
        return [source for index, source in enumerate(sources) if index in keep_indexes]


class SourceVerifier:
    def verify(
        self, sources: list[dict[str, Any]], must_verify: bool = True
    ) -> list[dict[str, Any]]:
        if not must_verify:
            return sources
        return [source for source in sources if self._is_verifiable(source)]

    @staticmethod
    def _is_verifiable(source: dict[str, Any]) -> bool:
        doi = str(source.get("doi") or "").strip()
        url = str(source.get("url") or "").strip()
        title = str(source.get("title") or "").strip()
        has_external_id = doi.startswith("10.") or url.startswith(("https://", "http://"))
        return bool(title and has_external_id)


class LiteratureExtractor:
    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    def extract(self, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cards: list[dict[str, Any]] = []
        for index, source in enumerate(sources, start=1):
            llm_card = self._extract_llm_fields(source)
            cards.append(
                {
                    "literature_id": f"lit_{index:03d}",
                    "title": str(source.get("title", "")),
                    "authors": list(source.get("authors") or []),
                    "year": int(source.get("year") or 0),
                    "source": str(source.get("source", "")),
                    "doi": str(source.get("doi", "")),
                    "url": str(source.get("url", "")),
                    "literature_type": str(source.get("literature_type", "other")),
                    "relevance_score": _clamp_score(source.get("relevance_score", 0.75)),
                    "main_findings": _string_list(llm_card.get("main_findings")),
                    "related_concepts": _string_list(llm_card.get("related_concepts")),
                }
            )
        return cards

    def _extract_llm_fields(self, source: dict[str, Any]) -> dict[str, Any]:
        result = self.llm_client.generate_json(
            system_prompt=(
                "Extract literature card semantic fields from a verified scholarly source. "
                "Return {'literature_cards': [{'main_findings': string[], "
                "'related_concepts': string[]}]} only."
            ),
            user_payload={
                "title": source.get("title"),
                "abstract": source.get("abstract"),
                "source": source.get("source"),
                "database": source.get("database"),
            },
            expected_schema="literature_cards",
        )
        cards = result.get("literature_cards") or [{}]
        return cards[0] if isinstance(cards[0], dict) else {}


class EvidenceExtractor:
    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    def extract(
        self, sources: list[dict[str, Any]], literature_cards: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        evidence_cards: list[dict[str, Any]] = []
        evidence_index = 1
        for source, literature in zip(sources, literature_cards):
            result = self.llm_client.generate_json(
                system_prompt=(
                    "Extract evidence cards from source text. Return "
                    "{'evidence_cards': [{'claim': string, 'evidence_type': string, "
                    "'support_direction': 'support|oppose|uncertain', "
                    "'related_concepts': string[], 'strength_score': number, "
                    "'summary': string, 'limitations': string[]}]} only."
                ),
                user_payload={
                    "source_literature_id": literature["literature_id"],
                    "title": literature["title"],
                    "abstract": source.get("abstract", ""),
                    "main_findings": literature.get("main_findings", []),
                    "related_concepts": literature.get("related_concepts", []),
                },
                expected_schema="evidence_cards",
            )
            for item in result.get("evidence_cards") or []:
                if not isinstance(item, dict):
                    continue
                claim = str(item.get("claim", "")).strip()
                if not claim:
                    continue
                evidence_cards.append(
                    {
                        "evidence_id": f"ev_{evidence_index:03d}",
                        "claim": claim,
                        "source_literature_id": literature["literature_id"],
                        "evidence_type": str(item.get("evidence_type") or "literature_finding"),
                        "support_direction": _support_direction(item.get("support_direction")),
                        "related_concepts": _string_list(item.get("related_concepts")),
                        "strength_score": _clamp_score(item.get("strength_score", 0.5)),
                        "summary": str(item.get("summary", "")),
                        "limitations": _string_list(item.get("limitations")),
                    }
                )
                evidence_index += 1
        literature_ids = {card["literature_id"] for card in literature_cards}
        return [
            card
            for card in evidence_cards
            if card["source_literature_id"] in literature_ids
        ]


class GapSynthesizer:
    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    def synthesize(
        self,
        question: ParsedQuestion,
        evidence_cards: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result = self.llm_client.generate_json(
            system_prompt=(
                "Identify knowledge gaps that can drive hypothesis generation. Return "
                "{'knowledge_gaps': [{'description': string, 'gap_type': string, "
                "'related_concepts': string[], 'related_evidence_ids': string[], "
                "'importance_score': number, "
                "'why_it_matters_for_hypothesis_generation': string}]} only."
            ),
            user_payload={
                "question": {
                    "core_question": question.core_question,
                    "research_object": question.research_object,
                    "domain": question.domain,
                    "key_concepts": question.key_concepts,
                    "sub_questions": question.sub_questions,
                },
                "evidence_cards": evidence_cards,
            },
            expected_schema="knowledge_gaps",
        )
        valid_evidence_ids = {card["evidence_id"] for card in evidence_cards}
        gaps: list[dict[str, Any]] = []
        for index, item in enumerate(result.get("knowledge_gaps") or [], start=1):
            if not isinstance(item, dict):
                continue
            related_ids = [
                evidence_id
                for evidence_id in _string_list(item.get("related_evidence_ids"))
                if evidence_id in valid_evidence_ids
            ]
            if not related_ids and evidence_cards:
                related_ids = [evidence_cards[0]["evidence_id"]]
            related_concepts = _string_list(item.get("related_concepts"))
            if not related_concepts:
                related_concepts = question.key_concepts[:2]
            why = str(item.get("why_it_matters_for_hypothesis_generation", "")).strip()
            description = str(item.get("description", "")).strip()
            if not description or not why:
                continue
            gaps.append(
                {
                    "gap_id": f"gap_{index:03d}",
                    "description": description,
                    "gap_type": _gap_type(item.get("gap_type")),
                    "related_concepts": related_concepts,
                    "related_evidence_ids": related_ids,
                    "importance_score": _clamp_score(item.get("importance_score", 0.5)),
                    "why_it_matters_for_hypothesis_generation": why,
                }
            )
        return gaps


class QualityReviewer:
    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    def review(
        self,
        literature_cards: list[dict[str, Any]],
        evidence_cards: list[dict[str, Any]],
        knowledge_gaps: list[dict[str, Any]],
        question: ParsedQuestion,
    ) -> dict[str, Any]:
        literature_ids = {card["literature_id"] for card in literature_cards}
        traceable_evidence = [
            card
            for card in evidence_cards
            if card.get("source_literature_id") in literature_ids
        ]
        traceability = len(traceable_evidence) / len(evidence_cards) if evidence_cards else 0.0
        source_verifiability = 1.0 if literature_cards else 0.0
        gap_value = min(1.0, len(knowledge_gaps) / 3)

        llm_result = self.llm_client.generate_json(
            system_prompt=(
                "Evaluate content quality for a knowledge integration stage. Return "
                "{'content_quality_score': number, 'issues': string[], 'suggestions': string[]}."
            ),
            user_payload={
                "question": question.core_question,
                "literature_cards": literature_cards,
                "evidence_cards": evidence_cards,
                "knowledge_gaps": knowledge_gaps,
            },
            expected_schema="self_review",
        )
        content_quality = _clamp_score(llm_result.get("content_quality_score", 0.5))
        overall = round(
            (
                source_verifiability
                + traceability
                + gap_value
                + content_quality
            )
            / 4,
            2,
        )
        threshold = 0.7
        issues = _string_list(llm_result.get("issues"))
        suggestions = _string_list(llm_result.get("suggestions"))
        if not literature_cards:
            issues.append("no verified literature cards")
        if not evidence_cards:
            issues.append("no traceable evidence cards")
        if not knowledge_gaps:
            issues.append("no knowledge gaps")

        return {
            "passed": overall >= threshold,
            "overall_score": overall,
            "threshold": threshold,
            "dimension_scores": {
                "source_verifiability": round(source_verifiability, 2),
                "evidence_traceability": round(traceability, 2),
                "gap_value": round(gap_value, 2),
                "content_quality": content_quality,
            },
            "issues": issues,
            "suggestions": suggestions,
        }


class KnowledgeIntegrationAgent:
    agent_id = "knowledge_integration_agent"
    stage = "knowledge_integration"

    def __init__(
        self,
        *,
        llm_client: LLMClient | None = None,
        literature_clients: list[LiteratureClient] | None = None,
    ) -> None:
        self.llm_client = RetryingLLMClient(llm_client or QwenDashScopeClient())
        self.question_parser = QuestionParser()
        self.search_query_planner = SearchQueryPlanner(self.llm_client)
        self.retrieval_service = RetrievalService(literature_clients)
        self.source_relevance_filter = SourceRelevanceFilter(self.llm_client)
        self.source_verifier = SourceVerifier()
        self.literature_extractor = LiteratureExtractor(self.llm_client)
        self.evidence_extractor = EvidenceExtractor(self.llm_client)
        self.gap_synthesizer = GapSynthesizer(self.llm_client)
        self.quality_reviewer = QualityReviewer(self.llm_client)

    def run(self, request: dict[str, Any]) -> dict[str, Any]:
        question, missing_fields = self.question_parser.parse(request)
        if missing_fields:
            return self._failure_response(
                request,
                requested_resources=missing_fields,
                issues=["missing required question_card fields"],
            )

        assert question is not None
        try:
            queries = self.search_query_planner.build_queries(question)
            retrieved_sources = self.retrieval_service.retrieve(question, queries)
            verified_sources = self.source_verifier.verify(
                retrieved_sources,
                bool(question.search_policy.get("must_verify_sources", True)),
            )
            relevant_sources = self.source_relevance_filter.filter(
                question, verified_sources
            )
            literature_cards = self.literature_extractor.extract(relevant_sources)
            evidence_cards = self.evidence_extractor.extract(
                relevant_sources, literature_cards
            )
            knowledge_gaps = self.gap_synthesizer.synthesize(question, evidence_cards)
            self_review = self.quality_reviewer.review(
                literature_cards, evidence_cards, knowledge_gaps, question
            )
        except Exception as exc:
            return self._failure_response(
                request,
                requested_resources=[],
                issues=[f"knowledge integration failed: {exc}"],
            )

        status = "success" if self_review["passed"] else "failed"
        return {
            "metadata": self._metadata(request, status=status),
            "payload": {
                "literature_cards": literature_cards,
                "evidence_cards": evidence_cards,
                "knowledge_gaps": knowledge_gaps,
            },
            "self_review": self_review,
        }

    def _failure_response(
        self,
        request: dict[str, Any],
        requested_resources: list[str],
        issues: list[str],
    ) -> dict[str, Any]:
        return {
            "metadata": self._metadata(request, status="failed"),
            "payload": {
                "literature_cards": [],
                "evidence_cards": [],
                "knowledge_gaps": [],
            },
            "self_review": {
                "passed": False,
                "overall_score": 0.0,
                "threshold": 0.75,
                "dimension_scores": {
                    "source_verifiability": 0.0,
                    "evidence_traceability": 0.0,
                    "gap_value": 0.0,
                    "content_quality": 0.0,
                },
                "issues": issues,
                "suggestions": ["retry after fixing upstream input or service configuration"],
            },
            "requested_resources": requested_resources,
        }

    def _metadata(self, request: dict[str, Any], status: str) -> dict[str, Any]:
        return {
            "task_id": request.get("task_id", ""),
            "agent_id": self.agent_id,
            "stage": self.stage,
            "iteration": int(request.get("iteration", 1)),
            "status": status,
        }


def _dedupe_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        doi = str(source.get("doi") or "").lower().strip()
        title = " ".join(str(source.get("title") or "").lower().split())
        key = doi or title
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(source)
    return deduped


def _string_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _clamp_score(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 0.0
    return round(max(0.0, min(1.0, numeric)), 2)


def _support_direction(value: Any) -> str:
    direction = str(value or "uncertain").lower()
    if direction in {"support", "oppose", "uncertain"}:
        return direction
    return "uncertain"


def _gap_type(value: Any) -> str:
    gap_type = str(value or "").strip()
    if gap_type in ALLOWED_GAP_TYPES:
        return gap_type
    return "mechanism_unknown"

