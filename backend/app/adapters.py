from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

from .agent_protocol import (
    AGENT_SPECS,
    CancellationChecker,
    CancellationRequested,
    ProgressHandler,
    get_agent_spec,
)
from .settings import Settings

REAL_AGENT_STAGES = {
    "question_understanding",
    "knowledge_integration",
    "hypothesis_generation",
    "evidence_mapping",
    "research_planning",
}


class AgentIntegrationError(RuntimeError):
    pass


class ProjectLLMClient:
    """One OpenAI-compatible client shared by all integrated Agents."""

    mock = False

    def __init__(self, task_context: dict[str, Any] | None = None) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AgentIntegrationError(
                "缺少 openai 依赖，请安装 backend/requirements.txt"
            ) from exc

        self.api_key = (
            os.getenv("DASHSCOPE_API_KEY")
            or os.getenv("QWEN_API_KEY")
            or os.getenv("LLM_API_KEY")
            or ""
        )
        if not self.api_key:
            raise AgentIntegrationError("未配置项目模型 API 密钥")
        self.base_url = (
            os.getenv("DASHSCOPE_BASE_URL")
            or os.getenv("LLM_BASE_URL")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self.model = os.getenv("QWEN_MODEL") or os.getenv("LLM_MODEL") or "qwen3.7-max"
        user_input = (task_context or {}).get("user_input") or {}
        constraints = user_input.get("user_constraints") or {}
        policy = (task_context or {}).get("model_policy") or {}
        self.background_context = str(user_input.get("question_description") or "").strip()
        self.reasoning_level = str(policy.get("reasoning") or constraints.get("reasoning_level") or "high")
        configured_max_tokens = int(os.getenv("LLM_MAX_TOKENS", "8192"))
        token_limits = {
            "low": 2048,
            "medium": 4096,
            "high": 6144,
            "ultra": configured_max_tokens,
        }
        self.max_tokens = int(policy.get("max_tokens") or min(configured_max_tokens, token_limits.get(self.reasoning_level, configured_max_tokens)))
        env_thinking = os.getenv("QWEN_ENABLE_THINKING", "false").lower() == "true"
        self.enable_thinking = bool(policy.get("thinking_enabled", env_thinking and self.reasoning_level in {"high", "ultra"}))
        self.model = str(policy.get("model") or self.model)
        self.temperature = float(policy.get("temperature", 0.2))
        self.response_format = str(policy.get("response_format") or "json_object")
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=float(policy.get("timeout_seconds") or os.getenv("LLM_TIMEOUT_SECONDS", "120")),
            max_retries=int(policy.get("max_retries", os.getenv("LLM_MAX_RETRIES", "0"))),
        )

    def _with_background_context(self, user_prompt: str) -> str:
        if not self.background_context or self.background_context in user_prompt:
            return user_prompt
        return (
            f"{user_prompt}\n\n[Task background and uploaded attachment context]\n"
            f"{self.background_context}"
        )

    def _complete(self, system_prompt: str, user_prompt: str, temperature: float) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": self._with_background_context(user_prompt)},
            ],
            response_format={"type": getattr(self, "response_format", "json_object")},
            temperature=float(getattr(self, "temperature", temperature)),
            max_tokens=self.max_tokens,
            extra_body={"enable_thinking": self.enable_thinking},
        )
        if not response.choices or not response.choices[0].message.content:
            raise AgentIntegrationError("项目模型 API 返回了空内容")
        return response.choices[0].message.content.strip()

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, flags=re.DOTALL)
            if not match:
                raise
            parsed = json.loads(match.group(0))
        if not isinstance(parsed, dict):
            raise AgentIntegrationError("项目模型 API 必须返回 JSON object")
        return parsed

    def chat_json(
        self, system_prompt: str, user_prompt: str, temperature: float = 0.2
    ) -> dict[str, Any]:
        return self._parse_json(self._complete(system_prompt, user_prompt, temperature))

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        expected_schema: str,
    ) -> dict[str, Any]:
        system = (
            f"{system_prompt}\nReturn strict JSON only for schema: {expected_schema}. "
            "Do not include markdown fences or explanatory text."
        )
        return self.chat_json(system, json.dumps(user_payload, ensure_ascii=False))

    def generate_text(self, prompt: str) -> str:
        return self._complete(
            "You are a scientific hypothesis generation model. Return one valid JSON object only.",
            prompt,
            0.3,
        )


@contextmanager
def _python_path(path: Path) -> Iterator[None]:
    value = str(path.resolve())
    inserted = value not in sys.path
    if inserted:
        sys.path.insert(0, value)
    try:
        yield
    finally:
        if inserted and value in sys.path:
            sys.path.remove(value)


def _load_package(root: Path, package_name: str) -> ModuleType:
    if not root.is_dir():
        raise AgentIntegrationError(f"Agent directory does not exist: {root}")
    with _python_path(root):
        return importlib.import_module(package_name)


def _load_file(path: Path, module_name: str) -> ModuleType:
    if not path.is_file():
        raise AgentIntegrationError(f"Agent file does not exist: {path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AgentIntegrationError(f"Cannot load Agent module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _string_items(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        for key in ("name", "normalized_name", "content", "description"):
            if value.get(key):
                return [str(value[key])]
        return []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_string_items(item))
        return list(dict.fromkeys(result))
    return []


def canonical_question_card(card: dict[str, Any], language: str = "zh") -> dict[str, Any]:
    research_object = card.get("research_object")
    if isinstance(research_object, dict):
        normalized_object = {
            "name": str(research_object.get("name") or research_object.get("object") or ""),
            "type": str(research_object.get("type") or "unknown"),
            "aliases": _string_items(research_object.get("aliases")),
        }
    else:
        normalized_object = {
            "name": str(research_object or ""),
            "type": "unknown",
            "aliases": [],
        }

    concepts = []
    for item in card.get("key_concepts") or []:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("normalized_name") or "")
            concepts.append(
                {
                    "name": name,
                    "normalized_name": str(item.get("normalized_name") or name),
                    "category": str(item.get("category") or "concept"),
                }
            )
        elif item:
            concepts.append(
                {"name": str(item), "normalized_name": str(item), "category": "concept"}
            )

    variables = []
    for item in card.get("key_variables") or []:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("variable") or "")
            variables.append(
                {
                    "name": name,
                    "type": str(item.get("type") or item.get("category") or "factor"),
                    "role": str(item.get("role") or item.get("type") or "independent"),
                }
            )
        elif item:
            variables.append({"name": str(item), "type": "factor", "role": "independent"})

    sub_questions = []
    for index, item in enumerate(card.get("sub_questions") or [], start=1):
        content = _string_items(item)
        if content:
            sub_questions.append(
                {
                    "sub_question_id": (
                        str(item.get("sub_question_id"))
                        if isinstance(item, dict) and item.get("sub_question_id")
                        else f"sq_{index:03d}"
                    ),
                    "content": content[0],
                }
            )

    raw_keywords = card.get("search_keywords")
    if isinstance(raw_keywords, dict):
        keywords = {
            "zh": _string_items(raw_keywords.get("zh")),
            "en": _string_items(raw_keywords.get("en")),
        }
    else:
        values = _string_items(raw_keywords)
        keywords = {
            "zh": values if language.startswith("zh") else [],
            "en": values if not language.startswith("zh") else [],
        }

    scope = card.get("research_scope") if isinstance(card.get("research_scope"), dict) else {}
    return {
        **card,
        "research_object": normalized_object,
        "key_concepts": concepts,
        "key_variables": variables,
        "sub_questions": sub_questions,
        "research_scope": {
            "included": _string_items(scope.get("included")),
            "excluded": _string_items(scope.get("excluded")),
        },
        "search_keywords": keywords,
    }


def knowledge_question_card(card: dict[str, Any]) -> dict[str, Any]:
    canonical = canonical_question_card(card)
    return {
        **canonical,
        "research_object": canonical["research_object"]["name"],
        "key_concepts": [item["name"] for item in canonical["key_concepts"]],
        "sub_questions": [item["content"] for item in canonical["sub_questions"]],
        "search_keywords": [
            *canonical["search_keywords"]["zh"],
            *canonical["search_keywords"]["en"],
        ],
    }


def hypothesis_request(task_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task_context.get("task_id"),
        "iteration": task_context.get("iteration", 1),
        "question_card": task_context.get("question_card"),
        "evidence_cards": task_context.get("evidence_cards") or [],
        "knowledge_gaps": task_context.get("knowledge_gaps") or [],
        "user_constraints": (task_context.get("user_input") or {}).get("user_constraints", {}),
    }


def evidence_mapping_request(task_context: dict[str, Any]) -> dict[str, Any]:
    evidence_cards = []
    for source in task_context.get("evidence_cards") or []:
        if not isinstance(source, dict):
            continue
        evidence_cards.append(
            {
                **source,
                "literature_id": source.get("literature_id") or source.get("source_literature_id"),
                "source_type": source.get("source_type") or source.get("evidence_type"),
                "support_direction_hint": source.get("support_direction_hint")
                or source.get("support_direction"),
                "confidence": source.get("confidence")
                if source.get("confidence") is not None
                else source.get("strength_score", 0.5),
            }
        )
    return {
        "task_id": str(task_context.get("task_id") or ""),
        "stage": "evidence_mapping",
        "iteration": int(task_context.get("iteration") or 1),
        "threshold": float(
            task_context.get("evidence_mapping_threshold")
            or os.getenv("EVIDENCE_MAPPING_THRESHOLD", "7.0")
        ),
        "hypothesis_cards": task_context.get("hypothesis_cards") or [],
        "evidence_cards": evidence_cards,
        "literature_cards": task_context.get("literature_cards") or [],
    }


def planning_request(task_context: dict[str, Any]) -> dict[str, Any]:
    user_constraints = task_context.get("user_constraints")
    if not isinstance(user_constraints, dict):
        user_constraints = (task_context.get("user_input") or {}).get("user_constraints", {})
    planning_constraints = task_context.get("planning_constraints")
    if not isinstance(planning_constraints, dict):
        planning_constraints = {
            "plan_depth": user_constraints.get("output_detail_level", "standard"),
            "resource_level": "demo",
            "allowed_validation_types": [
                "public_dataset_analysis",
                "statistical_test",
                "literature_based_feasibility",
                "human_feedback",
            ],
            "forbidden_actions": [
                "invent_references",
                "invent_dataset_url",
                "claim_real_experiment_completed_without_execution",
            ],
            "preferred_output_language": (
                "zh-CN" if str(user_constraints.get("language", "zh")).startswith("zh") else "en"
            ),
        }
    return {
        "task_id": str(task_context.get("task_id") or ""),
        "iteration": int(task_context.get("iteration") or 1),
        "question_card": task_context.get("question_card") or {},
        "hypothesis_cards": task_context.get("hypothesis_cards") or [],
        "evidence_map": task_context.get("evidence_map") or [],
        "literature_cards": task_context.get("literature_cards") or [],
        "evidence_cards": task_context.get("evidence_cards") or [],
        "knowledge_gaps": task_context.get("knowledge_gaps") or [],
        "user_constraints": user_constraints,
        "planning_constraints": planning_constraints,
    }


def _literature_clients(root: Path, task_context: dict[str, Any]) -> list[Any]:
    retrieval = _load_package(root, "knowledge_integration_agent.retrieval")
    clients: list[Any] = [
        retrieval.OpenAlexClient(),
        retrieval.CrossrefClient(),
        retrieval.SemanticScholarClient(),
    ]
    question = task_context.get("question_card") or {}
    domain_text = " ".join(
        [
            *_string_items(question.get("domain")),
            *_string_items(question.get("research_object")),
            *_string_items(question.get("key_concepts")),
        ]
    ).lower()
    if any(
        term in domain_text
        for term in ("bio", "medic", "health", "disease", "neuro", "生物", "医学", "疾病", "神经")
    ):
        clients.extend([retrieval.PubMedClient(), retrieval.EuropePmcClient()])
    if any(term in domain_text for term in ("astro", "space", "天文", "宇宙", "星系")):
        clients.extend([retrieval.ArxivClient(), retrieval.NasaAdsClient()])
    return clients


def _status(value: Any) -> str:
    normalized = str(value or "failed").lower()
    if normalized in {"success", "ok", "passed"}:
        return "success"
    if normalized in {"partial", "partial_success", "warning"}:
        return "partial_success"
    return "failed"


def failure_response(
    task_context: dict[str, Any], stage: str, agent_id: str, error: Exception
) -> dict[str, Any]:
    payload_key = {
        "question_understanding": "question_card",
        "knowledge_integration": "literature_cards",
        "hypothesis_generation": "hypothesis_cards",
        "evidence_mapping": "evidence_map",
        "research_planning": "research_plan",
    }.get(stage)
    payload: dict[str, Any] = {}
    if payload_key in {"question_card", "research_plan"}:
        payload[payload_key] = None
    elif payload_key:
        payload[payload_key] = []
    if stage == "knowledge_integration":
        payload.update(evidence_cards=[], knowledge_gaps=[])
    issue = f"{type(error).__name__}: {error}"
    timed_out = "timeout" in issue.lower() or "timed out" in issue.lower()
    return {
        "metadata": {
            "task_id": str(task_context.get("task_id") or ""),
            "agent_id": agent_id,
            "stage": stage,
            "iteration": int(task_context.get("iteration") or 1),
            "status": "failed",
        },
        "payload": payload,
        "self_review": {
            "passed": False,
            "overall_score": 0.0,
            "threshold": 0.75,
            "dimension_scores": {},
            "issues": [issue],
            "suggestions": [
                "降低推理强度、减少知识检索或规划候选数，或调整 LLM_TIMEOUT_SECONDS 后重试。"
                if timed_out
                else "检查 Agent 路径、Python 依赖、模型密钥和上游字段后重试。"
            ],
        },
    }


class AgentRegistry:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()

    def run(
        self,
        stage: str,
        task_context: dict[str, Any],
        feedback: str | None = None,
        *,
        progress_handler: ProgressHandler | None = None,
        cancellation_checker: CancellationChecker | None = None,
    ) -> dict[str, Any]:
        if stage not in REAL_AGENT_STAGES:
            raise AgentIntegrationError(f"No real Agent is registered for stage: {stage}")
        runner = {
            "question_understanding": self._run_question_understanding,
            "knowledge_integration": self._run_knowledge_integration,
            "hypothesis_generation": self._run_hypothesis_generation,
            "evidence_mapping": self._run_evidence_mapping,
            "research_planning": self._run_research_planning,
        }[stage]
        started = time.perf_counter()
        try:
            if cancellation_checker:
                cancellation_checker()
            if stage == "knowledge_integration":
                raw = runner(
                    task_context,
                    feedback,
                    progress_handler,
                    cancellation_checker,
                )
            elif stage == "research_planning":
                raw = runner(
                    task_context,
                    feedback,
                    progress_handler,
                    cancellation_checker,
                )
            else:
                raw = runner(task_context, feedback)
            if cancellation_checker:
                cancellation_checker()
        except CancellationRequested:
            raise
        except Exception as exc:
            raw = failure_response(task_context, stage, get_agent_spec(stage).agent_id, exc)
        metadata = raw.setdefault("metadata", {})
        metadata.update(
            task_id=str(task_context.get("task_id") or metadata.get("task_id") or ""),
            agent_id=str(metadata.get("agent_id") or get_agent_spec(stage).agent_id),
            stage=stage,
            iteration=int(task_context.get("iteration") or 1),
            status=_status(metadata.get("status")),
        )
        metadata.setdefault("trace_id", f"trace_{uuid4().hex[:12]}")
        metadata["duration_ms"] = round((time.perf_counter() - started) * 1000)
        raw.setdefault("payload", {})
        raw.setdefault(
            "self_review",
            {
                "passed": False,
                "overall_score": 0.0,
                "threshold": 0.75,
                "dimension_scores": {},
                "issues": ["Agent did not provide a self-review."],
                "suggestions": ["Implement the unified self_review contract."],
            },
        )
        return raw

    def describe(self) -> list[dict[str, Any]]:
        sources = self.settings.source_status()
        return [
            {
                **AGENT_SPECS[stage].as_dict(),
                "contract_version": "1.0",
                "available": bool(sources.get(stage, {}).get("available")),
                "source": sources.get(stage, {}),
            }
            for stage in sorted(REAL_AGENT_STAGES)
        ]

    def _run_question_understanding(
        self, task_context: dict[str, Any], feedback: str | None
    ) -> dict[str, Any]:
        module = _load_package(self.settings.problem_agent_root, "problem_understanding.agent")
        agent = module.ProblemUnderstandingAgent(llm=ProjectLLMClient(task_context))
        raw = agent.run(
            task_context.get("user_input") or {},
            version=int(task_context.get("iteration") or 1),
            feedback={"comment": feedback} if feedback else None,
            task_id=str(task_context.get("task_id") or ""),
        )
        meta = raw.get("meta") or {}
        data = raw.get("data") or {}
        source_card = data.get("question_card")
        success = _status(raw.get("status")) == "success" and isinstance(source_card, dict)
        confidence = float((source_card or {}).get("confidence") or 0.0)
        issue = raw.get("error") or {}
        return {
            "metadata": {
                "task_id": str(task_context.get("task_id") or meta.get("task_id") or ""),
                "agent_id": "question_understanding_agent",
                "stage": "question_understanding",
                "iteration": int(task_context.get("iteration") or 1),
                "status": "success" if success else "failed",
            },
            "payload": {
                "question_card": canonical_question_card(
                    source_card or {},
                    str(
                        (task_context.get("user_input") or {})
                        .get("user_constraints", {})
                        .get("language")
                        or "zh"
                    ),
                )
                if success
                else None
            },
            "self_review": {
                "passed": success and confidence >= 0.35,
                "overall_score": confidence if success else 0.0,
                "threshold": 0.35,
                "dimension_scores": {"confidence": confidence if success else 0.0},
                "issues": []
                if success
                else [str(issue.get("message") or "问题理解 Agent 执行失败")],
                "suggestions": [] if success else ["检查 user_input 和模型配置后重试。"],
            },
        }

    def _run_knowledge_integration(
        self,
        task_context: dict[str, Any],
        feedback: str | None,
        progress_handler: ProgressHandler | None = None,
        cancellation_checker: CancellationChecker | None = None,
    ) -> dict[str, Any]:
        if not (os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY")):
            raise AgentIntegrationError(
                "知识整合 Agent 需要 DASHSCOPE_API_KEY 或 QWEN_API_KEY 环境变量"
            )
        package = _load_package(self.settings.knowledge_agent_root, "knowledge_integration_agent")
        adapted_context = {
            **task_context,
            "question_card": knowledge_question_card(task_context.get("question_card") or {}),
            "_feedback": feedback or "",
        }
        agent = package.KnowledgeIntegrationAgent(
            llm_client=ProjectLLMClient(task_context),
            literature_clients=_literature_clients(
                self.settings.knowledge_agent_root, task_context
            ),
        )
        agent.llm_client.max_attempts = max(
            1, int(os.getenv("KNOWLEDGE_LLM_MAX_ATTEMPTS", "1"))
        )
        search_policy = {
            "max_queries": int(os.getenv("KNOWLEDGE_MAX_QUERIES", "3")),
            "max_papers": int(os.getenv("KNOWLEDGE_MAX_PAPERS", "12")),
            "per_client_limit": int(os.getenv("KNOWLEDGE_PER_CLIENT_LIMIT", "2")),
            "min_recent_papers": 3,
            "must_verify_sources": True,
            "forbidden_actions": ["invent_references", "invent_dataset_url"],
        }

        def forward_progress(event: dict[str, Any]) -> None:
            if cancellation_checker:
                cancellation_checker()
            if not progress_handler:
                return

            event_name = str(event.get("event") or "knowledge_integration_progress")
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            database = str(payload.get("database") or "source")
            if event_name.startswith("retrieval_database_"):
                node_id = f"source_search:{database}"
            else:
                node_id = {
                    "feedback_routing_completed": "query_planning",
                    "retrieval_completed": "source_search",
                    "literature_extraction_completed": "literature_extract",
                    "evidence_extraction_completed": "evidence_extract",
                    "gap_synthesis_completed": "gap_synthesis",
                }.get(event_name, "knowledge_integration")
            progress_handler(
                {
                    "node_id": node_id,
                    "kind": "partial_output"
                    if event_name
                    in {
                        "retrieval_completed",
                        "literature_extraction_completed",
                        "evidence_extraction_completed",
                        "gap_synthesis_completed",
                    }
                    else "progress",
                    "message": {
                        "feedback_routing_completed": "已根据反馈调整知识检索策略。",
                        "retrieval_database_started": f"正在检索 {database}。",
                        "retrieval_database_completed": f"{database} 检索完成。",
                        "retrieval_database_failed": f"{database} 检索失败，继续处理其他来源。",
                        "retrieval_completed": "文献检索完成。",
                        "literature_extraction_completed": "文献卡片整理完成。",
                        "evidence_extraction_completed": "证据卡片整理完成。",
                        "gap_synthesis_completed": "知识空白梳理完成。",
                    }.get(event_name, event_name.replace("_", " ")),
                    "payload": dict(payload),
                    "operation": "replace",
                }
            )

        raw = package.KnowledgeIntegrationAdapter(
            agent=agent,
            default_search_policy=search_policy,
        ).call(
            adapted_context,
            progress_callback=forward_progress,
        )
        raw["metadata"]["status"] = _status(raw.get("metadata", {}).get("status"))
        return raw

    def _run_hypothesis_generation(
        self, task_context: dict[str, Any], feedback: str | None
    ) -> dict[str, Any]:
        if not os.getenv("DASHSCOPE_API_KEY"):
            raise AgentIntegrationError("候选假设生成 Agent 需要 DASHSCOPE_API_KEY 环境变量")
        module = _load_file(
            self.settings.hypothesis_agent_file,
            "team_hypothesis_generation_agent",
        )
        config = module.HypothesisAgentConfig(
            min_evidence_keyword_overlap=float(os.getenv("HYPOTHESIS_MIN_EVIDENCE_OVERLAP", "0")),
            max_retries=int(os.getenv("HYPOTHESIS_MAX_RETRIES", "1")),
        )
        agent = module.HypothesisGenerationAgent(config=config)
        agent.call_llm = ProjectLLMClient(task_context).generate_text
        request = hypothesis_request(task_context)
        if feedback:
            request["user_constraints"] = {
                **request["user_constraints"],
                "revision_feedback": feedback,
            }
        raw = agent.run(request)
        raw["metadata"]["status"] = _status(raw.get("metadata", {}).get("status"))
        return raw

    def _run_evidence_mapping(
        self, task_context: dict[str, Any], _feedback: str | None
    ) -> dict[str, Any]:
        package = _load_package(self.settings.evidence_agent_root / "src", "evidence_mapping")
        raw = package.EvidenceMappingAgent().run_dict(evidence_mapping_request(task_context))
        raw["metadata"]["status"] = _status(raw.get("metadata", {}).get("status"))
        for item in raw.get("payload", {}).get("evidence_map", []):
            review = item.get("detailed_review") or {}
            verdict = review.get("verdict") or {}
            suggestion = verdict.get("rollback_suggestion") or verdict.get("reason") or ""
            verdict.setdefault("recommendation", suggestion)
            review.setdefault(
                "feedback_for_iteration",
                {
                    "back_to": verdict.get("rollback_target", "none"),
                    "specific_suggestions": [suggestion] if suggestion else [],
                },
            )
        return raw

    def _run_research_planning(
        self,
        task_context: dict[str, Any],
        _feedback: str | None,
        progress_handler: ProgressHandler | None = None,
        cancellation_checker: CancellationChecker | None = None,
    ) -> dict[str, Any]:
        service = _load_package(self.settings.planning_agent_root, "planning_agent.service")
        current_node = {"id": "package_select"}
        completed_plans = {"count": 0}

        def emit(update: dict[str, Any]) -> None:
            if cancellation_checker:
                cancellation_checker()
            if progress_handler:
                progress_handler(update)

        def service_progress(message: str) -> None:
            hypothesis_match = re.search(r"hypothesis \d+/\d+: ([^ ]+)", message)
            finished_match = re.search(r"hypothesis ([^:]+)", message)
            if hypothesis_match:
                hypothesis_id = hypothesis_match.group(1)
                current_node["id"] = f"dify_call:{hypothesis_id}"
                emit({
                    "node_id": current_node["id"],
                    "kind": "started",
                    "message": message,
                    "payload": {"hypothesis_id": hypothesis_id},
                })
                return
            if message.startswith("Dify finished"):
                completed_plans["count"] += 1
                emit({
                    "node_id": current_node["id"],
                    "kind": "partial_output",
                    "message": message,
                    "payload": {"completed_plans": completed_plans["count"]},
                    "operation": "append",
                })
                return
            emit({
                "node_id": current_node["id"],
                "kind": "progress",
                "message": message,
                "payload": {"hypothesis_id": finished_match.group(1) if finished_match else None},
            })

        def dify_event(event: dict[str, Any]) -> None:
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            emit({
                "node_id": current_node["id"],
                "kind": "progress",
                "message": str(event.get("event") or "Dify progress"),
                "payload": {
                    "event": event.get("event"),
                    "status": data.get("status"),
                    "node_id": data.get("node_id"),
                    "title": data.get("title"),
                },
            })

        emit({
            "node_id": "package_select",
            "kind": "started",
            "message": "Selecting hypothesis evidence packages.",
            "progress": 0.05,
        })
        client = service.DifyWorkflowClient(
            event_handler=dify_event,
            cancellation_checker=cancellation_checker,
        )
        raw = service.run_planning_agent(
            planning_request(task_context),
            dify_client=client,
            max_packages=int(os.getenv("PLANNING_MAX_HYPOTHESES", "2")),
            max_parallel_calls=int(os.getenv("PLANNING_MAX_PARALLEL_CALLS", "1")),
            progress_handler=service_progress,
        )
        emit({
            "node_id": "aggregate",
            "kind": "progress",
            "message": "Normalizing and aggregating research plans.",
            "progress": 0.95,
            "payload": {"completed_plans": completed_plans["count"]},
            "operation": "replace",
        })
        raw["metadata"]["status"] = _status(raw.get("metadata", {}).get("status"))
        research_plan = raw.get("payload") or {}
        research_plan.setdefault("run_id", research_plan.get("task_id", ""))
        research_plan.setdefault("round_id", research_plan.get("iteration", 1))
        raw["payload"] = {"research_plan": research_plan}
        return raw
