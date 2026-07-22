from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from planning_agent.adapter import (
    build_dify_workflow_inputs,
    build_hypothesis_evidence_packages,
    select_top_packages,
    validate_planner_input,
)
from planning_agent.dify_client import DifyWorkflowClient, DifyWorkflowError
from planning_agent.workflow_chain import PlanningWorkflowChainRunner

AGENT_ID = "research_planning_agent"
STAGE = "research_planning"
ProgressHandler = Callable[[str], None]
WorkflowEventHandler = Callable[[str, dict[str, Any]], None]
CancellationChecker = Callable[[], None]


def run_planning_agent(
    data: dict[str, Any],
    dify_client: DifyWorkflowClient | None = None,
    workflow_runner: PlanningWorkflowChainRunner | None = None,
    max_packages: int | None = None,
    progress_handler: ProgressHandler | None = None,
    max_parallel_calls: int | None = None,
    workflow_event_handler: WorkflowEventHandler | None = None,
    cancellation_checker: CancellationChecker | None = None,
) -> dict[str, Any]:
    errors = validate_planner_input(data)
    if errors:
        return _failed_response(data, errors, score=0.0)

    packages = build_hypothesis_evidence_packages(data)
    selected = select_top_packages(
        packages,
        max_packages=max_packages or _max_hypotheses(data),
    )
    runner = workflow_runner
    if runner is None and dify_client is None:
        auto_runner = PlanningWorkflowChainRunner.from_env(
            progress_handler=progress_handler,
            event_handler=workflow_event_handler,
            cancellation_checker=cancellation_checker,
        )
        if _runner_is_configured(auto_runner):
            runner = auto_runner
    parallel_calls = _max_parallel_calls(max_parallel_calls)
    if runner is not None:
        return _run_planning_chain(
            data, selected, runner, parallel_calls
        )

    legacy_event_handler = None

    if workflow_event_handler:
        def forward_legacy_event(event: dict[str, Any]) -> None:
            workflow_event_handler("workflow_c", event)

        legacy_event_handler = forward_legacy_event
    client = dify_client or DifyWorkflowClient(
        event_handler=legacy_event_handler,
        cancellation_checker=cancellation_checker,
    )
    if not client.configured:
        return _failed_response(
            data,
            ["Dify workflow is not configured. Set DIFY_API_URL and DIFY_API_KEY."],
            score=0.0,
        )

    plan_results, dify_errors = _run_selected_packages(
        data=data,
        selected=selected,
        client=client,
        progress_handler=progress_handler,
        max_parallel_calls=parallel_calls,
    )

    return _response_from_plan_results(data, plan_results, dify_errors)


def _runner_is_configured(runner: PlanningWorkflowChainRunner) -> bool:
    return all(
        bool(item.get("configured")) for item in runner.configuration_summary()
    )


def _run_planning_chain(
    data: dict[str, Any],
    selected: list[dict[str, Any]],
    runner: PlanningWorkflowChainRunner,
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
        max_revisions=max(0, _env_int("PLANNING_MAX_REVISIONS", 1)),
        max_parallel_hypotheses=max_parallel_calls,
    )
    packages_by_id = {
        str(package.get("hypothesis_id") or ""): package for package in selected
    }
    plan_results: list[dict[str, Any]] = []
    issues = [str(item) for item in report.get("errors", []) if str(item).strip()]
    for hypothesis_run in report.get("hypothesis_runs", []):
        if not isinstance(hypothesis_run, dict):
            continue
        hypothesis_id = str(hypothesis_run.get("hypothesis_id") or "")
        package = packages_by_id.get(hypothesis_id, {"hypothesis_id": hypothesis_id})
        final_result = hypothesis_run.get("final_result")
        if isinstance(final_result, dict) and final_result:
            plan_results.append(
                _normalize_plan_result(chain_data, package, final_result)
            )
            if hypothesis_run.get("status") != "success":
                issues.append(
                    f"Hypothesis {hypothesis_id} requires action: "
                    f"{hypothesis_run.get('next_action') or hypothesis_run.get('status')}"
                )
            continue
        child_errors = [
            str(item)
            for item in hypothesis_run.get("errors", [])
            if str(item).strip()
        ]
        reason = "; ".join(child_errors) or (
            f"Workflow B stopped with decision="
            f"{hypothesis_run.get('decision') or 'unknown'}; "
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
        issues.append("Dify workflow returned no plans.")
    return _response(
        data=data,
        status=payload["status"],
        payload=payload,
        passed=payload["status"] == "success",
        issues=issues,
        score=0.82 if payload["status"] == "success" else 0.62,
    )


def _run_selected_packages(
    data: dict[str, Any],
    selected: list[dict[str, Any]],
    client: DifyWorkflowClient,
    progress_handler: ProgressHandler | None,
    max_parallel_calls: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    if max_parallel_calls <= 1 or len(selected) <= 1:
        return _run_selected_packages_serial(data, selected, client, progress_handler)
    return _run_selected_packages_parallel(
        data, selected, client, progress_handler, max_parallel_calls
    )


def _run_selected_packages_serial(
    data: dict[str, Any],
    selected: list[dict[str, Any]],
    client: DifyWorkflowClient,
    progress_handler: ProgressHandler | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    plan_results: list[dict[str, Any]] = []
    dify_errors: list[str] = []
    total = len(selected)
    for index, package in enumerate(selected, start=1):
        plan_result, error = _run_one_package(
            data=data,
            package=package,
            client=client,
            progress_handler=progress_handler,
            index=index,
            total=total,
            parallel=False,
        )
        plan_results.append(plan_result)
        if error:
            dify_errors.append(error)
    return plan_results, dify_errors


def _run_selected_packages_parallel(
    data: dict[str, Any],
    selected: list[dict[str, Any]],
    client: DifyWorkflowClient,
    progress_handler: ProgressHandler | None,
    max_parallel_calls: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    _emit_progress(
        progress_handler,
        f"Calling Dify in parallel: {len(selected)} hypotheses, max_parallel_calls={max_parallel_calls}",
    )
    plan_results: list[dict[str, Any] | None] = [None] * len(selected)
    dify_errors: list[str] = []
    total = len(selected)
    with ThreadPoolExecutor(max_workers=min(max_parallel_calls, total)) as executor:
        futures = {
            executor.submit(
                _run_one_package,
                data,
                package,
                client,
                progress_handler,
                index,
                total,
                True,
            ): index - 1
            for index, package in enumerate(selected, start=1)
        }
        for future in as_completed(futures):
            result_index = futures[future]
            plan_result, error = future.result()
            plan_results[result_index] = plan_result
            if error:
                dify_errors.append(error)
    return [item for item in plan_results if item is not None], dify_errors


def _run_one_package(
    data: dict[str, Any],
    package: dict[str, Any],
    client: DifyWorkflowClient,
    progress_handler: ProgressHandler | None,
    index: int,
    total: int,
    parallel: bool,
) -> tuple[dict[str, Any], str | None]:
    hypothesis_id = package.get("hypothesis_id", "unknown")
    mode = "parallel" if parallel else "serial"
    _emit_progress(
        progress_handler,
        f"Calling Dify for hypothesis {index}/{total}: {hypothesis_id} ({mode})",
    )
    try:
        result = client.run_workflow(build_dify_workflow_inputs(data, package))
    except DifyWorkflowError as exc:
        error = f"Hypothesis {hypothesis_id}: {exc}"
        _emit_progress(progress_handler, f"Dify failed for hypothesis {hypothesis_id}: {exc}")
        return _failed_plan_result(data, package, str(exc)), error
    _emit_progress(progress_handler, f"Dify finished for hypothesis {hypothesis_id}")
    return _normalize_plan_result(data, package, result), None


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


def _aggregate_payload(
    data: dict[str, Any], plan_results: list[dict[str, Any]]
) -> dict[str, Any]:
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
    plan_result.setdefault("schema_version", "experiment_planner_plan_result_v1")
    plan_result.setdefault("agent_name", "ExperimentPlannerAgent")
    plan_result.setdefault("task_id", data.get("task_id", ""))
    plan_result.setdefault("iteration", data.get("iteration", 1))
    plan_result.setdefault("hypothesis_id", package.get("hypothesis_id", ""))
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
        item.get("literature_id") for item in data.get("literature_cards", [])
    }
    valid_evidence_ids = {item.get("evidence_id") for item in data.get("evidence_cards", [])}

    for plan_item in payload.get("plans", []):
        if plan_item.get("status") == "failed":
            continue
        plan = plan_item.get("plan", {})
        for reference in plan.get("references", []):
            if reference.get("source_id") not in valid_literature_ids:
                issues.append(
                    f"Plan {plan_item.get('hypothesis_id')} references unknown source "
                    f"{reference.get('source_id')}"
                )
        for step in plan.get("rationale", {}).get("logic_chain", []):
            for evidence_id in step.get("evidence_ids", []):
                if evidence_id not in valid_evidence_ids:
                    issues.append(
                        f"Plan {plan_item.get('hypothesis_id')} uses unknown evidence "
                        f"{evidence_id}"
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
        value = _env_int("DIFY_MAX_PARALLEL_CALLS", 1)
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
        return ["请检查 Dify 配置、工作流输出 JSON、证据 ID 和文献 ID 是否符合模块 5 规范。"]
    return ["请检查 Dify 工作流是否已发布并返回单个 plan_result。"]


def _emit_progress(progress_handler: ProgressHandler | None, message: str) -> None:
    if progress_handler:
        progress_handler(message)
