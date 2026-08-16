from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from planning_agent.adapter import (
    build_hypothesis_evidence_packages,
    select_top_packages,
    validate_planner_input,
)
from planning_agent.workflow_chain import PlanningProtocolRunner

# Compatibility-only exported name for existing test/injection callers.
PlanningWorkflowChainRunner = PlanningProtocolRunner

AGENT_ID = "research_planning_agent"
STAGE = "research_planning"
ProgressHandler = Callable[[str], None]
ExecutionEventHandler = Callable[[str, dict[str, Any]], None]
# Compatibility-only public type name for callers migrating to execution events.
WorkflowEventHandler = ExecutionEventHandler
CancellationChecker = Callable[[], None]


def run_planning_agent(
    data: dict[str, Any],
    workflow_runner: PlanningProtocolRunner | None = None,
    max_packages: int | None = None,
    progress_handler: ProgressHandler | None = None,
    max_parallel_calls: int | None = None,
    workflow_event_handler: WorkflowEventHandler | None = None,
    cancellation_checker: CancellationChecker | None = None,
    model_policy: dict[str, Any] | None = None,
    execution_event_handler: ExecutionEventHandler | None = None,
) -> dict[str, Any]:
    if workflow_event_handler is not None and execution_event_handler is not None:
        raise ValueError(
            "Use execution_event_handler; workflow_event_handler is only a compatibility alias."
        )
    errors = validate_planner_input(data)
    if errors:
        return _failed_response(data, errors, score=0.0)

    packages = build_hypothesis_evidence_packages(data)
    selected = select_top_packages(
        packages,
        max_packages=max_packages or _max_hypotheses(data),
    )
    runner = workflow_runner or PlanningProtocolRunner.from_env(
        progress_handler=progress_handler,
        event_handler=execution_event_handler or workflow_event_handler,
        cancellation_checker=cancellation_checker,
        model_policy=model_policy,
    )
    if not _runner_is_configured(runner):
        missing = ", ".join(_missing_stages(runner)) or "protocol compiler stages"
        return _failed_response(
            data,
            [
                "Planning protocol compiler is not configured. Set DASHSCOPE_API_KEY "
                f"and a supported Qwen model (missing: {missing})."
            ],
            score=0.0,
        )
    return _run_planning_chain(
        data,
        selected,
        runner,
        _max_parallel_calls(max_parallel_calls),
    )


def _runner_is_configured(runner: PlanningProtocolRunner) -> bool:
    configured = {
        str(item.get("name")) for item in runner.configuration_summary() if item.get("configured")
    }
    return {
        "draft",
        "review_methodology",
        "review_statistics",
        "review_feasibility",
        "synthesis",
    } <= configured


def _missing_stages(runner: PlanningProtocolRunner) -> list[str]:
    configured = {
        str(item.get("name")) for item in runner.configuration_summary() if item.get("configured")
    }
    required = (
        "draft",
        "review_methodology",
        "review_statistics",
        "review_feasibility",
        "synthesis",
    )
    return [stage for stage in required if stage not in configured]


def _run_planning_chain(
    data: dict[str, Any],
    selected: list[dict[str, Any]],
    runner: PlanningProtocolRunner,
    max_parallel_calls: int,
) -> dict[str, Any]:
    selected_ids = [str(package.get("hypothesis_id") or "") for package in selected]
    cards_by_id = {
        str(card.get("hypothesis_id") or ""): card
        for card in data.get("hypothesis_cards", [])
        if isinstance(card, dict)
    }
    chain_data = {
        **data,
        "hypothesis_cards": [
            cards_by_id[hypothesis_id]
            for hypothesis_id in selected_ids
            if hypothesis_id in cards_by_id
        ],
    }
    report = runner.run_batch(
        chain_data,
        max_parallel_hypotheses=max_parallel_calls,
        max_parallel_calls=max_parallel_calls,
    )
    packages_by_id = {str(package.get("hypothesis_id") or ""): package for package in selected}
    plan_results: list[dict[str, Any]] = []
    issues = [str(item) for item in report.get("errors", []) if str(item).strip()]
    for hypothesis_run in report.get("hypothesis_runs", []):
        if not isinstance(hypothesis_run, dict):
            continue
        hypothesis_id = str(hypothesis_run.get("hypothesis_id") or "")
        package = packages_by_id.get(hypothesis_id, {"hypothesis_id": hypothesis_id})
        final_result = hypothesis_run.get("final_result")
        if isinstance(final_result, dict) and final_result:
            plan_results.append(_normalize_plan_result(chain_data, package, final_result))
            if hypothesis_run.get("status") != "success":
                issues.append(
                    f"Hypothesis {hypothesis_id} requires action: "
                    f"{hypothesis_run.get('next_action') or hypothesis_run.get('status')}"
                )
            continue
        child_errors = [str(item) for item in hypothesis_run.get("errors", []) if str(item).strip()]
        reason = "; ".join(child_errors) or (
            "Protocol compiler stopped before a usable final plan; "
            f"next_action={hypothesis_run.get('next_action') or 'inspect_failure'}"
        )
        plan_results.append(_failed_plan_result(chain_data, package, reason))
        issues.append(f"Hypothesis {hypothesis_id}: {reason}")
    return _response_from_plan_results(data, plan_results, issues)


def _response_from_plan_results(
    data: dict[str, Any],
    plan_results: list[dict[str, Any]],
    execution_issues: list[str],
) -> dict[str, Any]:
    payload = _aggregate_payload(data, plan_results)
    issues = execution_issues + _guardrail_issues(data, payload)
    payload["status"] = _payload_status(payload, issues)
    if not payload.get("plans"):
        payload["status"] = "failed"
        issues.append("Local planning workflow returned no plans.")
    return _response(
        data=data,
        status=payload["status"],
        payload=payload,
        passed=payload["status"] == "success",
        issues=issues,
        score=0.82 if payload["status"] == "success" else 0.62,
    )


def _failed_response(data: dict[str, Any], errors: list[str], score: float) -> dict[str, Any]:
    return _response(
        data=data,
        status="failed",
        payload=_failed_payload(data, errors),
        passed=False,
        issues=errors,
        score=score,
    )


def _response(
    data: dict[str, Any],
    status: str,
    payload: dict[str, Any],
    passed: bool,
    issues: list[str],
    score: float,
) -> dict[str, Any]:
    return {
        "metadata": {
            "task_id": data.get("task_id", ""),
            "agent_id": AGENT_ID,
            "stage": STAGE,
            "iteration": data.get("iteration", 1),
            "status": status,
        },
        "payload": payload,
        "self_review": {
            "passed": passed,
            "overall_score": score,
            "threshold": 0.75,
            "dimension_scores": {
                "format_validity": 1.0 if payload.get("plans") else 0.0,
                "traceability": 1.0 if not issues else 0.6,
                "testability": 0.8 if payload.get("plans") else 0.0,
            },
            "issues": issues,
            "suggestions": _suggestions(status, issues),
        },
    }


def _failed_payload(data: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    return {
        "schema_version": "experiment_planner_output_v1",
        "agent_name": "ExperimentPlannerAgent",
        "task_id": data.get("task_id", ""),
        "iteration": data.get("iteration", 1),
        "status": "failed",
        "plans": [],
        "error_message": "; ".join(errors),
    }


def _aggregate_payload(data: dict[str, Any], plan_results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "experiment_planner_output_v1",
        "agent_name": "ExperimentPlannerAgent",
        "task_id": data.get("task_id", ""),
        "iteration": data.get("iteration", 1),
        "status": "success",
        "plans": plan_results,
    }


def _normalize_plan_result(
    data: dict[str, Any], package: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    if "plans" in result and result.get("plans"):
        result = result["plans"][0]
    plan_result = dict(result)
    plan_result["schema_version"] = "experiment_planner_plan_result_v1"
    plan_result["agent_name"] = "ExperimentPlannerAgent"
    plan_result["task_id"] = data.get("task_id", "")
    plan_result["iteration"] = data.get("iteration", 1)
    plan_result["hypothesis_id"] = package.get("hypothesis_id", "")
    plan_result.setdefault("status", "success" if plan_result.get("plan") else "failed")
    plan_result.setdefault("error_message", "")
    plan_result.setdefault("plan", {})
    return {
        "hypothesis_id": plan_result["hypothesis_id"],
        "status": plan_result["status"],
        "error_message": plan_result["error_message"],
        "plan": plan_result["plan"],
    }


def _failed_plan_result(
    data: dict[str, Any], package: dict[str, Any], error_message: str
) -> dict[str, Any]:
    return {
        "hypothesis_id": package.get("hypothesis_id", ""),
        "status": "failed",
        "error_message": error_message,
        "plan": {},
    }


def _payload_status(payload: dict[str, Any], issues: list[str]) -> str:
    plans = payload.get("plans", [])
    if not plans:
        return "failed"
    successful = [plan for plan in plans if plan.get("status") == "success"]
    usable = [plan for plan in plans if plan.get("status") in {"success", "partial_success"}]
    if len(successful) == len(plans) and not issues:
        return "success"
    if usable:
        return "partial_success"
    return "failed"


def _guardrail_issues(data: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    valid_literature_ids = {
        str(item.get("literature_id"))
        for item in data.get("literature_cards", [])
        if isinstance(item, dict) and item.get("literature_id")
    }
    valid_evidence_ids = {
        str(item.get("evidence_id"))
        for item in data.get("evidence_cards", [])
        if isinstance(item, dict) and item.get("evidence_id")
    }

    for plan_item in payload.get("plans", []):
        if not isinstance(plan_item, dict):
            issues.append("Planning result item must be an object.")
            continue
        if plan_item.get("status") == "failed":
            continue
        plan = plan_item.get("plan", {})
        hypothesis_id = plan_item.get("hypothesis_id")
        if not isinstance(plan, dict):
            issues.append(f"Plan {hypothesis_id} payload must be an object.")
            continue
        references = plan.get("references", [])
        if not isinstance(references, list):
            issues.append(f"Plan {hypothesis_id} references must be an array.")
            references = []
        for index, reference in enumerate(references):
            if not isinstance(reference, dict):
                issues.append(
                    f"Plan {hypothesis_id} references[{index}] must be an object."
                )
                continue
            source_id = str(reference.get("source_id") or "")
            if source_id not in valid_literature_ids:
                issues.append(
                    f"Plan {hypothesis_id} references unknown source {source_id or '<missing>'}"
                )
        rationale = plan.get("rationale", {})
        if not isinstance(rationale, dict):
            issues.append(f"Plan {hypothesis_id} rationale must be an object.")
            continue
        logic_chain = rationale.get("logic_chain", [])
        if not isinstance(logic_chain, list):
            issues.append(f"Plan {hypothesis_id} rationale.logic_chain must be an array.")
            continue
        for index, step in enumerate(logic_chain):
            if not isinstance(step, dict):
                issues.append(
                    f"Plan {hypothesis_id} rationale.logic_chain[{index}] must be an object."
                )
                continue
            evidence_ids = step.get("evidence_ids", [])
            if not isinstance(evidence_ids, list):
                issues.append(
                    f"Plan {hypothesis_id} rationale.logic_chain[{index}].evidence_ids "
                    "must be an array."
                )
                continue
            for evidence_id in evidence_ids:
                if evidence_id not in valid_evidence_ids:
                    issues.append(
                        f"Plan {hypothesis_id} uses unknown evidence {evidence_id}"
                    )
    return issues


def _max_hypotheses(data: dict[str, Any]) -> int:
    constraints = data.get("user_constraints", {})
    value = constraints.get("max_hypotheses", 3)
    try:
        return max(1, min(3, int(value)))
    except (TypeError, ValueError):
        return 3


def _max_parallel_calls(value: int | None) -> int:
    if value is None:
        value = _env_int("PLANNING_MAX_PARALLEL_CALLS", 1)
    return max(1, min(8, value))


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _suggestions(status: str, issues: list[str]) -> list[str]:
    if status == "success":
        return []
    if issues:
        return ["请检查协议草案、专家审查、综合定稿的 JSON 契约及证据/文献 allowlist。"]
    return ["请检查百炼模型配置与本地协议编译阶段是否返回有效研究计划。"]
