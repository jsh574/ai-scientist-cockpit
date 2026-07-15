from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator

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

    def __init__(self) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AgentIntegrationError("缺少 openai 依赖，请安装 backend/requirements.txt") from exc

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
        self.max_tokens = int(os.getenv("LLM_MAX_TOKENS", "8192"))
        self.enable_thinking = os.getenv("QWEN_ENABLE_THINKING", "false").lower() == "true"
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
            max_retries=1,
        )

    def _complete(
        self, system_prompt: str, user_prompt: str, temperature: float
    ) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=temperature,
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


class ProjectPlanningWorkflowClient:
    """Expose the Planning Agent workflow contract through the shared Qwen client."""

    configured = True

    def __init__(self, llm: Any | None = None, workflow_error: type[Exception] = AgentIntegrationError) -> None:
        self.llm = llm or ProjectLLMClient()
        self.workflow_error = workflow_error

    def run_workflow(self, inputs: dict[str, Any]) -> dict[str, Any]:
        try:
            question_card = _json_object(inputs.get("question_card"))
            package = _json_object(inputs.get("hypothesis_evidence_package"))
            planning_constraints = _json_object(inputs.get("planning_constraints"))
            user_constraints = _json_object(inputs.get("user_constraints"))
            result = self.llm.generate_json(
                system_prompt=(
                    "你是科研实验规划 Agent。请为一个候选假设生成可执行、可证伪、可追溯的研究方案。"
                    "只能引用输入中存在的 evidence_id 和 literature_id，不得虚构文献、数据集 URL 或已完成的实验结果。"
                    "输出一个 JSON 对象，顶层可以是 plan_result，也可以直接包含 plan。plan 必须包含："
                    "problem_statement；rationale{text,logic_chain[]}；"
                    "technical_details{required_methods,candidate_models_or_algorithms,statistical_tests,software_stack}；"
                    "datasets{source,target}；paper_title；paper_abstract；methods{overall_design,steps[]}；"
                    "experiments{main_experiment,baselines,metrics,procedure,ablation_or_sensitivity_analysis}；"
                    "results{result_type,expected_findings,feasibility_check,falsification_criteria}；"
                    "references；feedback_tasks；limitations。不要输出 Markdown。"
                ),
                user_payload={
                    "task_id": inputs.get("task_id"),
                    "iteration": inputs.get("iteration", 1),
                    "hypothesis_id": inputs.get("hypothesis_id"),
                    "question_card": question_card,
                    "hypothesis_evidence_package": package,
                    "planning_constraints": planning_constraints,
                    "user_constraints": user_constraints,
                },
                expected_schema="experiment_planner_plan_result_v1",
            )
            return _normalize_project_plan_result(result, inputs, package)
        except Exception as exc:
            if isinstance(exc, self.workflow_error):
                raise
            raise self.workflow_error(f"Qwen planning workflow failed: {exc}") from exc


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
        keywords = {"zh": values if language.startswith("zh") else [], "en": values if not language.startswith("zh") else []}

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
        "user_constraints": (task_context.get("user_input") or {}).get(
            "user_constraints", {}
        ),
    }


def evidence_mapping_request(task_context: dict[str, Any]) -> dict[str, Any]:
    evidence_cards = []
    for source in task_context.get("evidence_cards") or []:
        if not isinstance(source, dict):
            continue
        evidence_cards.append(
            {
                **source,
                "literature_id": source.get("literature_id")
                or source.get("source_literature_id"),
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
        user_constraints = (task_context.get("user_input") or {}).get(
            "user_constraints", {}
        )
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


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    return {}


def _dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _normalize_project_plan_result(
    result: dict[str, Any], inputs: dict[str, Any], package: dict[str, Any]
) -> dict[str, Any]:
    candidate = result.get("plan_result") if isinstance(result.get("plan_result"), dict) else result
    raw_plan = candidate.get("plan") if isinstance(candidate.get("plan"), dict) else candidate
    hypothesis = str(package.get("hypothesis") or "")
    hypothesis_id = str(inputs.get("hypothesis_id") or package.get("hypothesis_id") or "")

    evidence_groups = package.get("evidence_subset") or {}
    evidence_cards = [
        *_dict_items(evidence_groups.get("supporting_evidence")),
        *_dict_items(evidence_groups.get("opposing_evidence")),
        *_dict_items(evidence_groups.get("uncertain_evidence")),
    ]
    valid_evidence_ids = {
        str(item.get("evidence_id")) for item in evidence_cards if item.get("evidence_id")
    }
    literature = _dict_items(package.get("source_literature"))
    literature_by_id = {
        str(item.get("literature_id")): item
        for item in literature
        if item.get("literature_id")
    }
    valid_source_ids = set(literature_by_id)

    rationale = raw_plan.get("rationale") if isinstance(raw_plan.get("rationale"), dict) else {}
    logic_chain = []
    for index, item in enumerate(_dict_items(rationale.get("logic_chain")), start=1):
        logic_chain.append(
            {
                "step": int(item.get("step") or index),
                "claim": str(item.get("claim") or hypothesis),
                "evidence_ids": [
                    value for value in _string_items(item.get("evidence_ids"))
                    if value in valid_evidence_ids
                ],
                "source_ids": [
                    value for value in _string_items(item.get("source_ids"))
                    if value in valid_source_ids
                ],
            }
        )
    if not logic_chain:
        logic_chain = [{
            "step": 1,
            "claim": hypothesis,
            "evidence_ids": sorted(valid_evidence_ids),
            "source_ids": sorted(valid_source_ids),
        }]

    technical = raw_plan.get("technical_details") if isinstance(raw_plan.get("technical_details"), dict) else {}
    datasets = raw_plan.get("datasets") if isinstance(raw_plan.get("datasets"), dict) else {}
    methods = raw_plan.get("methods") if isinstance(raw_plan.get("methods"), dict) else {}
    experiments = raw_plan.get("experiments") if isinstance(raw_plan.get("experiments"), dict) else {}
    main_experiment = experiments.get("main_experiment") if isinstance(experiments.get("main_experiment"), dict) else {}
    results = raw_plan.get("results") if isinstance(raw_plan.get("results"), dict) else {}

    method_steps = _dict_items(methods.get("steps"))
    if not method_steps:
        method_steps = [
            {
                "step_id": "step_001",
                "name": "数据准备与质量控制",
                "description": "整理可用公开数据字段并记录缺失与偏倚。",
                "input": ["evidence_cards", "dataset metadata"],
                "output": ["analysis-ready dataset"],
            },
            {
                "step_id": "step_002",
                "name": "假设检验",
                "description": str(package.get("validation_idea") or "执行预注册的统计检验。"),
                "input": _string_items(package.get("target_variables")),
                "output": ["effect estimates", "uncertainty report"],
            },
        ]

    references = []
    for item in _dict_items(raw_plan.get("references")):
        source_id = str(item.get("source_id") or item.get("literature_id") or "")
        source = literature_by_id.get(source_id)
        if not source:
            continue
        references.append(
            {
                "source_id": source_id,
                "title": str(source.get("title") or ""),
                "authors": _string_items(source.get("authors")),
                "year": int(source.get("year") or 0),
                "doi": str(source.get("doi") or ""),
                "url": str(source.get("url") or ""),
                "used_for": _string_items(item.get("used_for")) or ["rationale"],
            }
        )
    if not references:
        references = [
            {
                "source_id": source_id,
                "title": str(source.get("title") or ""),
                "authors": _string_items(source.get("authors")),
                "year": int(source.get("year") or 0),
                "doi": str(source.get("doi") or ""),
                "url": str(source.get("url") or ""),
                "used_for": ["rationale"],
            }
            for source_id, source in literature_by_id.items()
        ]

    target_variables = _string_items(package.get("target_variables"))
    falsification = _string_items(results.get("falsification_criteria")) or [
        f"在预设统计功效和误差范围内，未观察到与假设一致的效应：{hypothesis}"
    ]
    feedback_tasks = _dict_items(raw_plan.get("feedback_tasks"))
    if package.get("needs_more_evidence") and not feedback_tasks:
        feedback_tasks = [{
            "task_id": f"fb_{hypothesis_id}",
            "task_type": "literature_supplement",
            "priority": "high",
            "objective": "补充当前假设缺失或不确定的证据。",
            "input_requirements": [hypothesis_id],
            "expected_output": "新增可追溯 evidence_cards 并重新评估证据强度。",
        }]
    for item in feedback_tasks:
        if item.get("priority") not in {"high", "medium", "low"}:
            item["priority"] = "medium"
        item["input_requirements"] = _string_items(item.get("input_requirements"))

    plan = {
        "problem_statement": str(raw_plan.get("problem_statement") or hypothesis),
        "rationale": {
            "text": str(rationale.get("text") or package.get("rationale") or ""),
            "logic_chain": logic_chain,
        },
        "technical_details": {
            "required_methods": _string_items(technical.get("required_methods")) or ["公开数据分析"],
            "candidate_models_or_algorithms": _string_items(technical.get("candidate_models_or_algorithms")),
            "statistical_tests": _string_items(technical.get("statistical_tests")) or ["效应量与置信区间估计"],
            "software_stack": _string_items(technical.get("software_stack")) or ["Python"],
        },
        "datasets": {
            "source": _dict_items(datasets.get("source")),
            "target": _dict_items(datasets.get("target")) or [{
                "name": "待确认的研究数据集",
                "description": "根据公开数据可用性确定，不虚构数据来源。",
                "fields": target_variables,
            }],
        },
        "paper_title": str(raw_plan.get("paper_title") or hypothesis),
        "paper_abstract": str(raw_plan.get("paper_abstract") or f"本研究拟检验：{hypothesis}"),
        "methods": {
            "overall_design": str(methods.get("overall_design") or package.get("validation_idea") or "可证伪的观察性研究设计"),
            "steps": method_steps,
        },
        "experiments": {
            "main_experiment": {
                "objective": str(main_experiment.get("objective") or package.get("expected_observation") or hypothesis),
                "independent_variables": _string_items(main_experiment.get("independent_variables")) or target_variables[:1],
                "dependent_variables": _string_items(main_experiment.get("dependent_variables")) or target_variables[1:],
                "control_variables": _string_items(main_experiment.get("control_variables")),
            },
            "baselines": _dict_items(experiments.get("baselines")),
            "metrics": _dict_items(experiments.get("metrics")),
            "procedure": _string_items(experiments.get("procedure")) or [item.get("description", "") for item in method_steps],
            "ablation_or_sensitivity_analysis": _string_items(experiments.get("ablation_or_sensitivity_analysis")),
        },
        "results": {
            "result_type": str(results.get("result_type") or "expected_or_feasibility_result"),
            "expected_findings": _string_items(results.get("expected_findings")) or _string_items(package.get("expected_observation")),
            "feasibility_check": str(results.get("feasibility_check") or package.get("validation_idea") or "需先确认数据字段可用性。"),
            "falsification_criteria": falsification,
        },
        "references": references,
        "feedback_tasks": feedback_tasks,
        "limitations": list(dict.fromkeys([
            *_string_items(raw_plan.get("limitations")),
            *_string_items(package.get("limitations")),
        ])),
    }
    return {
        "schema_version": "experiment_planner_plan_result_v1",
        "agent_name": "ExperimentPlannerAgent",
        "task_id": str(inputs.get("task_id") or ""),
        "iteration": int(inputs.get("iteration") or 1),
        "hypothesis_id": hypothesis_id,
        "status": "success",
        "error_message": "",
        "plan": plan,
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
    if any(term in domain_text for term in ("bio", "medic", "health", "disease", "neuro", "生物", "医学", "疾病", "神经")):
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
            "issues": [f"{type(error).__name__}: {error}"],
            "suggestions": ["检查 Agent 路径、Python 依赖、模型密钥和上游字段后重试。"],
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
        try:
            return runner(task_context, feedback)
        except Exception as exc:
            return failure_response(task_context, stage, f"{stage}_agent", exc)

    def _run_question_understanding(
        self, task_context: dict[str, Any], feedback: str | None
    ) -> dict[str, Any]:
        module = _load_package(self.settings.problem_agent_root, "problem_understanding.agent")
        agent = module.ProblemUnderstandingAgent(llm=ProjectLLMClient())
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
                    str((task_context.get("user_input") or {}).get("user_constraints", {}).get("language") or "zh"),
                ) if success else None
            },
            "self_review": {
                "passed": success and confidence >= 0.35,
                "overall_score": confidence if success else 0.0,
                "threshold": 0.35,
                "dimension_scores": {"confidence": confidence if success else 0.0},
                "issues": [] if success else [str(issue.get("message") or "问题理解 Agent 执行失败")],
                "suggestions": [] if success else ["检查 user_input 和模型配置后重试。"],
            },
        }

    def _run_knowledge_integration(
        self, task_context: dict[str, Any], _feedback: str | None
    ) -> dict[str, Any]:
        if not (os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY")):
            raise AgentIntegrationError(
                "知识整合 Agent 需要 DASHSCOPE_API_KEY 或 QWEN_API_KEY 环境变量"
            )
        package = _load_package(self.settings.knowledge_agent_root, "knowledge_integration_agent")
        adapted_context = {
            **task_context,
            "question_card": knowledge_question_card(task_context.get("question_card") or {}),
        }
        agent = package.KnowledgeIntegrationAgent(
            llm_client=ProjectLLMClient(),
            literature_clients=_literature_clients(
                self.settings.knowledge_agent_root, task_context
            ),
        )
        search_policy = {
            "max_queries": int(os.getenv("KNOWLEDGE_MAX_QUERIES", "3")),
            "max_papers": int(os.getenv("KNOWLEDGE_MAX_PAPERS", "12")),
            "per_client_limit": int(os.getenv("KNOWLEDGE_PER_CLIENT_LIMIT", "2")),
            "min_recent_papers": 3,
            "must_verify_sources": True,
            "forbidden_actions": ["invent_references", "invent_dataset_url"],
        }
        raw = package.KnowledgeIntegrationAdapter(
            agent=agent,
            default_search_policy=search_policy,
        ).call(adapted_context)
        raw["metadata"]["status"] = _status(raw.get("metadata", {}).get("status"))
        return raw

    def _run_hypothesis_generation(
        self, task_context: dict[str, Any], _feedback: str | None
    ) -> dict[str, Any]:
        if not os.getenv("DASHSCOPE_API_KEY"):
            raise AgentIntegrationError("候选假设生成 Agent 需要 DASHSCOPE_API_KEY 环境变量")
        module = _load_file(
            self.settings.hypothesis_agent_file,
            "team_hypothesis_generation_agent",
        )
        config = module.HypothesisAgentConfig(
            min_evidence_keyword_overlap=float(
                os.getenv("HYPOTHESIS_MIN_EVIDENCE_OVERLAP", "0")
            ),
            max_retries=int(os.getenv("HYPOTHESIS_MAX_RETRIES", "1")),
        )
        agent = module.HypothesisGenerationAgent(config=config)
        agent.call_llm = ProjectLLMClient().generate_text
        raw = agent.run(hypothesis_request(task_context))
        raw["metadata"]["status"] = _status(raw.get("metadata", {}).get("status"))
        return raw

    def _run_evidence_mapping(
        self, task_context: dict[str, Any], _feedback: str | None
    ) -> dict[str, Any]:
        package = _load_package(
            self.settings.evidence_agent_root / "src", "evidence_mapping"
        )
        raw = package.EvidenceMappingAgent().run_dict(
            evidence_mapping_request(task_context)
        )
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
        self, task_context: dict[str, Any], _feedback: str | None
    ) -> dict[str, Any]:
        service = _load_package(
            self.settings.planning_agent_root, "planning_agent.service"
        )
        dify_client = _load_package(
            self.settings.planning_agent_root, "planning_agent.dify_client"
        )
        raw = service.run_planning_agent(
            planning_request(task_context),
            dify_client=ProjectPlanningWorkflowClient(
                workflow_error=dify_client.DifyWorkflowError
            ),
            max_packages=int(os.getenv("PLANNING_MAX_HYPOTHESES", "2")),
            max_parallel_calls=int(os.getenv("PLANNING_MAX_PARALLEL_CALLS", "1")),
        )
        raw["metadata"]["status"] = _status(raw.get("metadata", {}).get("status"))
        research_plan = raw.get("payload") or {}
        research_plan.setdefault("run_id", research_plan.get("task_id", ""))
        research_plan.setdefault("round_id", research_plan.get("iteration", 1))
        raw["payload"] = {"research_plan": research_plan}
        return raw
