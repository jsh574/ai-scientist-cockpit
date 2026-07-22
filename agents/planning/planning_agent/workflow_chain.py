from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Protocol

from planning_agent.adapter import (
    build_dify_workflow_inputs,
    build_hypothesis_evidence_packages,
    select_top_packages,
    validate_planner_input,
)
from planning_agent.workflow_api import (
    DifyWorkflowAPIError,
    GenericDifyWorkflowClient,
    WorkflowEndpointConfig,
    WorkflowRunResult,
)

CHAIN_SCHEMA_VERSION = "planning_workflow_chain_test_v1"
BATCH_CHAIN_SCHEMA_VERSION = "planning_workflow_chain_batch_test_v1"
DEFAULT_VARIANTS = (
    "minimum_viable",
    "high_information",
    "resource_efficient",
)
ProgressHandler = Callable[[str], None]


class WorkflowClient(Protocol):
    @property
    def configured(self) -> bool: ...

    def run(
        self, inputs: dict[str, Any], event_context: dict[str, Any] | None = None
    ) -> WorkflowRunResult: ...


class PlanningWorkflowChainRunner:
    """Run the guarded Workflow A -> B -> C planning chain."""

    def __init__(
        self,
        candidate_client: WorkflowClient,
        selector_client: WorkflowClient,
        planner_client: WorkflowClient,
        progress_handler: ProgressHandler | None = None,
        max_c_context_chars: int | None = None,
        max_selection_retries: int | None = None,
    ) -> None:
        self.candidate_client = candidate_client
        self.selector_client = selector_client
        self.planner_client = planner_client
        self.progress_handler = progress_handler
        self.max_c_context_chars = max_c_context_chars or _env_int(
            "DIFY_WORKFLOW_C_PLANNING_CONSTRAINTS_MAX_CHARS", 12000
        )
        retry_limit = (
            max_selection_retries
            if max_selection_retries is not None
            else _env_int("DIFY_WORKFLOW_B_MAX_FORMAT_RETRIES", 1)
        )
        self.max_selection_retries = max(0, min(2, retry_limit))

    @classmethod
    def from_env(
        cls,
        progress_handler: ProgressHandler | None = None,
        event_handler: Callable[[str, dict[str, Any]], None] | None = None,
        cancellation_checker: Callable[[], None] | None = None,
    ) -> PlanningWorkflowChainRunner:
        return cls(
            candidate_client=GenericDifyWorkflowClient(
                WorkflowEndpointConfig.from_env("A"), event_handler, cancellation_checker
            ),
            selector_client=GenericDifyWorkflowClient(
                WorkflowEndpointConfig.from_env("B"), event_handler, cancellation_checker
            ),
            planner_client=GenericDifyWorkflowClient(
                WorkflowEndpointConfig.from_env("C"), event_handler, cancellation_checker
            ),
            progress_handler=progress_handler,
        )

    def configuration_summary(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for name, client in (
            ("workflow_a", self.candidate_client),
            ("workflow_b", self.selector_client),
            ("workflow_c", self.planner_client),
        ):
            config = getattr(client, "config", None)
            if config is not None and hasattr(config, "public_summary"):
                summaries.append(config.public_summary())
            else:
                summaries.append(
                    {"name": name, "configured": bool(getattr(client, "configured", True))}
                )
        return summaries

    def run(
        self,
        data: dict[str, Any],
        hypothesis_id: str | None = None,
        variants: tuple[str, ...] = DEFAULT_VARIANTS,
        max_revisions: int = 1,
    ) -> dict[str, Any]:
        report = _new_report(data, hypothesis_id, variants, max_revisions)
        started = time.monotonic()
        try:
            self._validate_configuration()
            errors = validate_planner_input(data)
            if errors:
                raise ValueError("; ".join(errors))
            package = _select_package(data, hypothesis_id)
            report["hypothesis_id"] = package.get("hypothesis_id", "")
            report["hypothesis"] = package.get("hypothesis", "")
            self._run_rounds(
                report=report,
                data=data,
                package=package,
                variants=variants,
                max_revisions=max(0, max_revisions),
            )
        except (DifyWorkflowAPIError, ValueError) as exc:
            report["status"] = "failed"
            report["next_action"] = "inspect_failure"
            report["errors"].append(str(exc))
        except Exception as exc:  # Preserve a report for unexpected live API behavior.
            if type(exc).__name__ == "CancellationRequested":
                raise
            report["status"] = "failed"
            report["next_action"] = "inspect_failure"
            report["errors"].append(f"Unexpected {type(exc).__name__}: {exc}")
        report["completed_at"] = _utc_now()
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        return report

    def run_batch(
        self,
        data: dict[str, Any],
        variants: tuple[str, ...] = DEFAULT_VARIANTS,
        max_revisions: int = 1,
        max_parallel_hypotheses: int = 1,
    ) -> dict[str, Any]:
        parallelism = max(1, min(8, int(max_parallel_hypotheses)))
        report = _new_batch_report(data, variants, max_revisions, parallelism)
        started = time.monotonic()
        try:
            self._validate_configuration()
            errors = validate_planner_input(data)
            if errors:
                raise ValueError("; ".join(errors))
            packages = select_top_packages(
                build_hypothesis_evidence_packages(data),
                max_packages=len(data.get("hypothesis_cards", [])),
            )
            _validate_batch_packages(packages)
            report["hypothesis_ids"] = [
                str(package.get("hypothesis_id") or "") for package in packages
            ]
            self._emit(
                f"Batch A/B/C: running {len(packages)} hypotheses with "
                f"max_parallel_hypotheses={parallelism}."
            )
            report["hypothesis_runs"] = self._run_batch_packages(
                data=data,
                packages=packages,
                variants=variants,
                max_revisions=max(0, max_revisions),
                max_parallel_hypotheses=parallelism,
            )
            _finalize_batch_status(report)
        except (DifyWorkflowAPIError, ValueError) as exc:
            report["status"] = "failed"
            report["next_action"] = "inspect_failure"
            report["errors"].append(str(exc))
        except Exception as exc:
            if type(exc).__name__ == "CancellationRequested":
                raise
            report["status"] = "failed"
            report["next_action"] = "inspect_failure"
            report["errors"].append(f"Unexpected {type(exc).__name__}: {exc}")
        report["completed_at"] = _utc_now()
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        return report

    def _run_batch_packages(
        self,
        data: dict[str, Any],
        packages: list[dict[str, Any]],
        variants: tuple[str, ...],
        max_revisions: int,
        max_parallel_hypotheses: int,
    ) -> list[dict[str, Any]]:
        if max_parallel_hypotheses <= 1 or len(packages) <= 1:
            return [
                self._run_batch_package(data, package, variants, max_revisions)
                for package in packages
            ]

        reports: list[dict[str, Any] | None] = [None] * len(packages)
        with ThreadPoolExecutor(
            max_workers=min(max_parallel_hypotheses, len(packages))
        ) as executor:
            futures = {
                executor.submit(
                    self._run_batch_package,
                    data,
                    package,
                    variants,
                    max_revisions,
                ): index
                for index, package in enumerate(packages)
            }
            for future in as_completed(futures):
                reports[futures[future]] = future.result()
        return [item for item in reports if item is not None]

    def _run_batch_package(
        self,
        data: dict[str, Any],
        package: dict[str, Any],
        variants: tuple[str, ...],
        max_revisions: int,
    ) -> dict[str, Any]:
        hypothesis_id = str(package.get("hypothesis_id") or "")
        child_progress_handler = None
        if self.progress_handler:
            parent_progress_handler = self.progress_handler

            def emit_child_progress(message: str) -> None:
                parent_progress_handler(f"[{hypothesis_id}] {message}")

            child_progress_handler = emit_child_progress
        child = PlanningWorkflowChainRunner(
            candidate_client=self.candidate_client,
            selector_client=self.selector_client,
            planner_client=self.planner_client,
            progress_handler=child_progress_handler,
            max_c_context_chars=self.max_c_context_chars,
            max_selection_retries=self.max_selection_retries,
        )
        return child.run(
            data,
            hypothesis_id=hypothesis_id,
            variants=variants,
            max_revisions=max_revisions,
        )

    def _validate_configuration(self) -> None:
        missing = [
            name
            for name, client in (
                ("Workflow A", self.candidate_client),
                ("Workflow B", self.selector_client),
                ("Workflow C", self.planner_client),
            )
            if not bool(getattr(client, "configured", True))
        ]
        if missing:
            raise ValueError(f"Missing Dify configuration for: {', '.join(missing)}")

    def _run_rounds(
        self,
        report: dict[str, Any],
        data: dict[str, Any],
        package: dict[str, Any],
        variants: tuple[str, ...],
        max_revisions: int,
    ) -> None:
        constraints = _mapping(data.get("planning_constraints"))
        revision_count = 0
        round_number = 1
        while True:
            candidate_stage, candidates, guardrails = self._run_candidates(
                data, package, variants, constraints, round_number
            )
            report["stages"].append(candidate_stage)
            report["intermediate_results"]["candidate_rounds"].append(
                {
                    "round": round_number,
                    "candidates": candidates,
                    "guardrail_reports": guardrails,
                }
            )
            if not candidates:
                raise DifyWorkflowAPIError(
                    f"Workflow A round {round_number} returned no usable design candidates."
                )

            selection_attempt = 1
            selection_constraints = constraints
            while True:
                selection_stage, selection, selected_design, selection_guardrail = (
                    self._run_selection(
                        data,
                        package,
                        candidates,
                        selection_constraints,
                        round_number,
                        selection_attempt,
                    )
                )
                report["stages"].append(selection_stage)
                report["intermediate_results"]["selection_rounds"].append(
                    {
                        "round": round_number,
                        "attempt": selection_attempt,
                        "design_selection": selection,
                        "selected_design": selected_design,
                        "selection_guardrail_report": selection_guardrail,
                    }
                )
                decision = str(selection.get("decision") or "failed")
                if (
                    decision == "failed"
                    and selection_guardrail.get("passed") is False
                    and selection_attempt <= self.max_selection_retries
                ):
                    issues = selection_guardrail.get("issues", [])
                    selection_attempt += 1
                    selection_constraints = {
                        **constraints,
                        "selection_format_retry": {
                            "attempt": selection_attempt,
                            "previous_issues": issues if isinstance(issues, list) else [],
                            "instruction": "Return the complete design_selection_v1 root object.",
                        },
                    }
                    self._emit(
                        "Workflow B returned malformed structured output; "
                        f"retrying selection attempt {selection_attempt}."
                    )
                    continue
                break
            report["decision"] = decision

            if decision == "accept":
                if not selected_design:
                    raise DifyWorkflowAPIError(
                        "Workflow B accepted a candidate but returned an empty selected_design."
                    )
                self._run_final_plan(report, data, package, constraints, selection, selected_design)
                return

            if decision == "revise_once" and revision_count < max_revisions:
                instruction = str(selection.get("revision_instruction") or "").strip()
                if not instruction:
                    self._stop_for_action(report, decision, "revise_candidates")
                    report["errors"].append(
                        "Workflow B requested revise_once without revision_instruction."
                    )
                    return
                revision_count += 1
                round_number += 1
                constraints = {
                    **constraints,
                    "candidate_revision": {
                        "revision_round": revision_count,
                        "revision_instruction": instruction,
                        "source_decision": "revise_once",
                    },
                }
                self._emit(
                    f"Workflow B requested one bounded revision; starting A/B round {round_number}."
                )
                continue

            next_action = {
                "revise_once": "revise_candidates",
                "feedback_required": "request_upstream_feedback",
                "human_review": "human_review",
                "failed": "inspect_failure",
            }.get(decision, "inspect_failure")
            if decision == "failed":
                guard_issues = selection_guardrail.get("issues", [])
                if isinstance(guard_issues, list) and guard_issues:
                    report["errors"].extend(
                        f"Workflow B guardrail: {issue}" for issue in guard_issues
                    )
                else:
                    report["errors"].append("Workflow B returned decision=failed.")
            self._stop_for_action(report, decision, next_action)
            return

    def _run_candidates(
        self,
        data: dict[str, Any],
        package: dict[str, Any],
        variants: tuple[str, ...],
        constraints: dict[str, Any],
        round_number: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        if not variants:
            raise ValueError("At least one Workflow A variant is required.")
        self._emit(
            f"Workflow A round {round_number}: generating {len(variants)} candidates in parallel."
        )
        started = time.monotonic()
        runs_by_variant: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=len(variants)) as executor:
            futures = {
                executor.submit(
                    self._run_candidate,
                    data,
                    package,
                    variant,
                    constraints,
                    round_number,
                ): variant
                for variant in variants
            }
            for future in as_completed(futures):
                variant = futures[future]
                try:
                    runs_by_variant[variant] = future.result()
                except DifyWorkflowAPIError as exc:
                    runs_by_variant[variant] = {
                        "variant_mode": variant,
                        "status": "failed",
                        "error": str(exc),
                    }
        runs = [runs_by_variant[variant] for variant in variants]
        candidates = [
            item["outputs"]["design_candidate"]
            for item in runs
            if item.get("status") == "success"
            and isinstance(item.get("outputs", {}).get("design_candidate"), dict)
        ]
        guardrails = [
            item["outputs"]["guardrail_report"]
            for item in runs
            if isinstance(item.get("outputs", {}).get("guardrail_report"), dict)
        ]
        stage_status = (
            "success"
            if len(candidates) == len(variants)
            else "partial_success"
            if candidates
            else "failed"
        )
        stage = {
            "stage_id": "candidate_generation",
            "round": round_number,
            "status": stage_status,
            "duration_seconds": round(time.monotonic() - started, 3),
            "accepted_candidate_count": len(candidates),
            "rejected_candidate_count": len(variants) - len(candidates),
            "runs": runs,
        }
        return stage, candidates, guardrails

    def _run_candidate(
        self,
        data: dict[str, Any],
        package: dict[str, Any],
        variant: str,
        constraints: dict[str, Any],
        round_number: int,
    ) -> dict[str, Any]:
        inputs = build_dify_workflow_inputs(data, package)
        inputs["variant_mode"] = variant
        inputs["_feedback"] = str(data.get("_feedback") or "")
        inputs["planning_constraints"] = _json_text(constraints)
        result = self.candidate_client.run(
            inputs,
            event_context={
                "workflow_stage": "A",
                "hypothesis_id": str(package.get("hypothesis_id") or ""),
                "variant_mode": variant,
                "round": round_number,
                "attempt": 1,
            },
        )
        candidate = _required_object(result.outputs, "design_candidate", "Workflow A")
        guardrail = _required_object(result.outputs, "guardrail_report", "Workflow A")
        if candidate.get("hypothesis_id") != package.get("hypothesis_id"):
            raise DifyWorkflowAPIError(f"Workflow A {variant} returned a mismatched hypothesis_id.")
        accepted = guardrail.get("passed") is True and candidate.get("status") != "failed"
        return {
            "variant_mode": variant,
            "status": "success" if accepted else "rejected",
            "error": "" if accepted else "Workflow A guardrail rejected the candidate.",
            "workflow_run_id": result.workflow_run_id,
            "task_id": result.task_id,
            "elapsed_time": result.elapsed_time,
            "total_tokens": result.total_tokens,
            "outputs": result.outputs,
        }

    def _run_selection(
        self,
        data: dict[str, Any],
        package: dict[str, Any],
        candidates: list[dict[str, Any]],
        constraints: dict[str, Any],
        round_number: int,
        attempt: int,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        self._emit(
            f"Workflow B round {round_number} attempt {attempt}: "
            f"judging {len(candidates)} candidates."
        )
        started = time.monotonic()
        base = build_dify_workflow_inputs(data, package)
        inputs = {
            "task_id": base["task_id"],
            "iteration": base["iteration"],
            "hypothesis_id": base["hypothesis_id"],
            "design_candidates": _json_text(candidates),
            "hypothesis_evidence_package": base["hypothesis_evidence_package"],
            "planning_constraints": _json_text(constraints),
            "user_constraints": base["user_constraints"],
        }
        result = self.selector_client.run(
            inputs,
            event_context={
                "workflow_stage": "B",
                "hypothesis_id": str(base["hypothesis_id"]),
                "round": round_number,
                "attempt": attempt,
            },
        )
        selection = _required_object(result.outputs, "design_selection", "Workflow B")
        selected_design = _required_object(result.outputs, "selected_design", "Workflow B")
        guardrail = _required_object(result.outputs, "selection_guardrail_report", "Workflow B")
        stage = {
            "stage_id": "design_selection",
            "round": round_number,
            "attempt": attempt,
            "status": "success",
            "decision": selection.get("decision", "failed"),
            "duration_seconds": round(time.monotonic() - started, 3),
            "workflow_run_id": result.workflow_run_id,
            "task_id": result.task_id,
            "elapsed_time": result.elapsed_time,
            "total_tokens": result.total_tokens,
            "outputs": result.outputs,
        }
        return stage, selection, selected_design, guardrail

    def _run_final_plan(
        self,
        report: dict[str, Any],
        data: dict[str, Any],
        package: dict[str, Any],
        constraints: dict[str, Any],
        selection: dict[str, Any],
        selected_design: dict[str, Any],
    ) -> None:
        self._emit("Workflow C: generating the final research plan from the accepted design.")
        started = time.monotonic()
        base = build_dify_workflow_inputs(data, package)
        c_constraints, context_info = _build_c_constraints(
            constraints,
            selection,
            selected_design,
            self.max_c_context_chars,
        )
        base["planning_constraints"] = _json_text(c_constraints)
        result = self.planner_client.run(
            base,
            event_context={
                "workflow_stage": "C",
                "hypothesis_id": str(base["hypothesis_id"]),
                "selected_candidate_id": str(selected_design.get("candidate_id") or ""),
            },
        )
        plan_result = dict(_required_object(result.outputs, "plan_result", "Workflow C"))
        normalized_identity_fields = []
        expected_identity = {
            "schema_version": "experiment_planner_plan_result_v1",
            "agent_name": "ExperimentPlannerAgent",
            "task_id": base["task_id"],
            "iteration": base["iteration"],
            "hypothesis_id": base["hypothesis_id"],
        }
        for field, expected in expected_identity.items():
            if plan_result.get(field) != expected:
                normalized_identity_fields.append(field)
            plan_result[field] = expected
        plan = plan_result.get("plan")
        if not isinstance(plan, dict):
            raise DifyWorkflowAPIError("Workflow C plan_result.plan must be an object.")
        business_status = str(plan_result.get("status") or ("success" if plan else "failed"))
        if business_status not in {"success", "partial_success", "failed"}:
            business_status = "failed"
            plan_result["status"] = "failed"
            plan_result["error_message"] = "Workflow C returned an invalid business status."
        else:
            plan_result["status"] = business_status
        if not plan and business_status in {"success", "partial_success"}:
            business_status = "failed"
            plan_result["status"] = "failed"
            plan_result["error_message"] = str(
                plan_result.get("error_message") or "Workflow C returned an empty plan."
            )
        stage = {
            "stage_id": "final_plan_generation",
            "round": 1,
            "status": business_status,
            "duration_seconds": round(time.monotonic() - started, 3),
            "workflow_run_id": result.workflow_run_id,
            "task_id": result.task_id,
            "elapsed_time": result.elapsed_time,
            "total_tokens": result.total_tokens,
            "context_control": context_info,
            "normalized_identity_fields": normalized_identity_fields,
            "contract_report": result.outputs.get("contract_report", {}),
            "outputs": result.outputs,
        }
        report["stages"].append(stage)
        report["final_result"] = plan_result
        if business_status == "success" and plan:
            report["status"] = "success"
            report["next_action"] = "continue_to_product"
        elif business_status == "partial_success" and plan:
            report["status"] = "requires_action"
            report["next_action"] = "review_partial_plan"
        else:
            report["status"] = "failed"
            report["next_action"] = "inspect_failure"
            report["errors"].append(
                str(plan_result.get("error_message") or "Workflow C returned a failed plan_result.")
            )

    def _stop_for_action(self, report: dict[str, Any], decision: str, next_action: str) -> None:
        report["status"] = "failed" if decision == "failed" else "requires_action"
        report["next_action"] = next_action
        self._emit(f"Stopping before Workflow C: decision={decision}, next_action={next_action}.")

    def _emit(self, message: str) -> None:
        if self.progress_handler:
            self.progress_handler(message)


def _new_report(
    data: dict[str, Any],
    hypothesis_id: str | None,
    variants: tuple[str, ...],
    max_revisions: int,
) -> dict[str, Any]:
    return {
        "schema_version": CHAIN_SCHEMA_VERSION,
        "task_id": str(data.get("task_id") or ""),
        "iteration": int(data.get("iteration") or 1),
        "hypothesis_id": hypothesis_id or "",
        "hypothesis": "",
        "status": "running",
        "decision": "",
        "next_action": "",
        "started_at": _utc_now(),
        "completed_at": "",
        "duration_seconds": 0.0,
        "configuration": {
            "variants": list(variants),
            "max_revisions": max(0, max_revisions),
        },
        "stages": [],
        "intermediate_results": {
            "candidate_rounds": [],
            "selection_rounds": [],
        },
        "final_result": None,
        "errors": [],
    }


def _new_batch_report(
    data: dict[str, Any],
    variants: tuple[str, ...],
    max_revisions: int,
    max_parallel_hypotheses: int,
) -> dict[str, Any]:
    return {
        "schema_version": BATCH_CHAIN_SCHEMA_VERSION,
        "task_id": str(data.get("task_id") or ""),
        "iteration": int(data.get("iteration") or 1),
        "status": "running",
        "next_action": "",
        "started_at": _utc_now(),
        "completed_at": "",
        "duration_seconds": 0.0,
        "configuration": {
            "variants": list(variants),
            "max_revisions": max(0, max_revisions),
            "max_parallel_hypotheses": max_parallel_hypotheses,
            "selection_order": "selection_score_desc",
        },
        "hypothesis_ids": [],
        "summary": {
            "total": 0,
            "success": 0,
            "requires_action": 0,
            "failed": 0,
        },
        "hypothesis_runs": [],
        "errors": [],
    }


def _validate_batch_packages(packages: list[dict[str, Any]]) -> None:
    if not packages:
        raise ValueError("No hypothesis evidence packages could be built from the input.")
    hypothesis_ids = [str(package.get("hypothesis_id") or "") for package in packages]
    if any(not hypothesis_id for hypothesis_id in hypothesis_ids):
        raise ValueError("Every hypothesis must have a non-empty hypothesis_id.")
    if len(set(hypothesis_ids)) != len(hypothesis_ids):
        raise ValueError("Duplicate hypothesis_id values are not allowed in batch mode.")


def _finalize_batch_status(report: dict[str, Any]) -> None:
    runs = report.get("hypothesis_runs", [])
    statuses = [str(item.get("status") or "failed") for item in runs]
    summary = {
        "total": len(statuses),
        "success": statuses.count("success"),
        "requires_action": statuses.count("requires_action"),
        "failed": statuses.count("failed"),
    }
    report["summary"] = summary
    if summary["total"] and summary["success"] == summary["total"]:
        report["status"] = "success"
        report["next_action"] = "continue_to_product"
    elif summary["failed"] == summary["total"]:
        report["status"] = "failed"
        report["next_action"] = "inspect_failure"
    elif summary["failed"]:
        report["status"] = "partial_success"
        report["next_action"] = "inspect_partial_failure"
    elif summary["requires_action"]:
        report["status"] = "requires_action"
        report["next_action"] = "resolve_hypothesis_actions"
    else:
        report["status"] = "failed"
        report["next_action"] = "inspect_failure"

    report["errors"] = [
        f"{run.get('hypothesis_id')}: {error}"
        for run in runs
        if run.get("status") == "failed"
        for error in run.get("errors", [])
    ]


def _select_package(data: dict[str, Any], hypothesis_id: str | None) -> dict[str, Any]:
    packages = build_hypothesis_evidence_packages(data)
    if hypothesis_id:
        for package in packages:
            if package.get("hypothesis_id") == hypothesis_id:
                return package
        raise ValueError(f"Unknown hypothesis_id: {hypothesis_id}")
    selected = select_top_packages(packages, max_packages=1)
    if not selected:
        raise ValueError("No hypothesis evidence package could be built from the input.")
    return selected[0]


def _required_object(outputs: dict[str, Any], key: str, workflow: str) -> dict[str, Any]:
    value = outputs.get(key)
    if not isinstance(value, dict):
        raise DifyWorkflowAPIError(f"{workflow} output `{key}` is not a JSON object.")
    return value


def _build_c_constraints(
    constraints: dict[str, Any],
    selection: dict[str, Any],
    selected_design: dict[str, Any],
    max_chars: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    full = {
        **constraints,
        "selected_design": selected_design,
        "design_selection": selection,
    }
    full_text = _json_text(full)
    if len(full_text) <= max_chars:
        return full, {
            "strategy": "full_selection_context",
            "serialized_chars": len(full_text),
            "max_chars": max_chars,
        }

    compact_selection = {
        key: selection.get(key)
        for key in (
            "schema_version",
            "task_id",
            "iteration",
            "hypothesis_id",
            "decision",
            "selected_candidate_id",
            "revision_instruction",
            "meta_review",
            "limitations",
        )
        if key in selection
    }
    compact = {
        **constraints,
        "selected_design": selected_design,
        "design_selection": compact_selection,
        "context_compaction": {
            "applied": True,
            "removed_fields": [
                "design_selection.candidate_reviews",
                "design_selection.feedback_tasks",
            ],
            "reason": "Workflow C planning_constraints character budget",
        },
    }
    compact_text = _json_text(compact)
    if len(compact_text) > max_chars:
        raise ValueError(
            "Accepted Workflow B context exceeds Workflow C planning_constraints limit: "
            f"{len(compact_text)} > {max_chars} characters after safe compaction."
        )
    return compact, {
        "strategy": "compact_selection_context",
        "serialized_chars": len(compact_text),
        "original_serialized_chars": len(full_text),
        "max_chars": max_chars,
    }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default
