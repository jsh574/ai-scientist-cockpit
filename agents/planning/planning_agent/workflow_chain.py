from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any, Protocol
from uuid import uuid4

from planning_agent.adapter import build_hypothesis_evidence_packages
from planning_agent.local_nodes import (
    compact_for_synthesis,
    compile_planning_brief,
    merge_protocol_reviews,
    planning_brief_issues,
)
from planning_agent.runtime import (
    CancellationChecker,
    ExecutionEventHandler,
    PlanningExecutionError,
    PlanningFormatError,
    PlanningLLMClient,
    PlanningLLMConfig,
    StageRunResult,
)
from planning_agent.schemas import REVIEW_ROLES
from planning_agent.stage_clients import LocalPlanningProtocolClient

DEFAULT_REVIEW_ROLES = REVIEW_ROLES
ProgressHandler = Callable[[str], None]


class ProtocolClient(Protocol):
    @property
    def configured(self) -> bool: ...

    def run(
        self,
        stage: str,
        inputs: dict[str, Any],
        event_context: dict[str, Any] | None = None,
    ) -> StageRunResult: ...

    def public_summary(self) -> dict[str, Any]: ...


class PlanningProtocolRunner:
    """Evidence-grounded protocol compiler controlled by deterministic Python."""

    def __init__(
        self,
        client: ProtocolClient,
        *,
        progress_handler: ProgressHandler | None = None,
        event_handler: ExecutionEventHandler | None = None,
        cancellation_checker: CancellationChecker | None = None,
        max_parallel_calls: int = 1,
        max_repair_attempts: int = 1,
        synthesis_context_max_chars: int = 16000,
    ) -> None:
        self.client = client
        self.progress_handler = progress_handler
        self.event_handler = event_handler
        self.cancellation_checker = cancellation_checker
        self.max_parallel_calls = max(1, min(8, int(max_parallel_calls)))
        self.max_repair_attempts = max(0, min(1, int(max_repair_attempts)))
        self.synthesis_context_max_chars = max(4000, int(synthesis_context_max_chars))
        self._model_slots = threading.BoundedSemaphore(self.max_parallel_calls)

    @classmethod
    def from_env(
        cls,
        *,
        progress_handler: ProgressHandler | None = None,
        event_handler: ExecutionEventHandler | None = None,
        cancellation_checker: CancellationChecker | None = None,
        model_policy: Mapping[str, Any] | None = None,
    ) -> PlanningProtocolRunner:
        config = PlanningLLMConfig.from_env(model_policy)
        llm = PlanningLLMClient(
            config,
            event_handler=event_handler,
            cancellation_checker=cancellation_checker,
        )
        return cls(
            LocalPlanningProtocolClient(llm, config),
            progress_handler=progress_handler,
            event_handler=event_handler,
            cancellation_checker=cancellation_checker,
            max_parallel_calls=_env_int("PLANNING_MAX_PARALLEL_CALLS", 1),
            max_repair_attempts=_repair_attempts(),
            synthesis_context_max_chars=_context_limit(),
        )

    def configuration_summary(self) -> list[dict[str, Any]]:
        base = self.client.public_summary()
        stages = (
            "draft",
            "review_methodology",
            "review_statistics",
            "review_feasibility",
            "synthesis",
            "repair",
        )
        return [
            {
                **base,
                "name": stage,
                "configured": bool(self.client.configured),
                "thinking_enabled": bool(base.get("thinking_enabled"))
                and stage not in {"synthesis", "repair"},
                "optional": stage == "repair",
            }
            for stage in stages
        ]

    def run(
        self,
        data: dict[str, Any],
        hypothesis_id: str | None = None,
        **_compatibility_options: Any,
    ) -> dict[str, Any]:
        packages = build_hypothesis_evidence_packages(data)
        package = _select_package(packages, hypothesis_id)
        return self._run_package(data, package)

    def run_batch(
        self,
        data: dict[str, Any],
        *,
        max_parallel_hypotheses: int = 1,
        max_parallel_calls: int | None = None,
        **_compatibility_options: Any,
    ) -> dict[str, Any]:
        if max_parallel_calls is not None and int(max_parallel_calls) != self.max_parallel_calls:
            self.max_parallel_calls = max(1, min(8, int(max_parallel_calls)))
            self._model_slots = threading.BoundedSemaphore(self.max_parallel_calls)
        packages = build_hypothesis_evidence_packages(data)
        report = {
            "schema_version": "planning_protocol_batch_v1",
            "task_id": str(data.get("task_id") or ""),
            "iteration": int(data.get("iteration") or 1),
            "status": "running",
            "workflow_mode": "local_protocol_compiler",
            "hypothesis_runs": [],
            "errors": [],
            "started_at": _utc_timestamp(),
        }
        if not packages:
            report["status"] = "failed"
            report["errors"] = ["No hypothesis evidence packages were available."]
            report["finished_at"] = _utc_timestamp()
            return report

        workers = max(1, min(int(max_parallel_hypotheses), len(packages)))
        ordered: list[dict[str, Any] | None] = [None] * len(packages)
        if workers == 1:
            for index, package in enumerate(packages):
                self._check_cancelled()
                ordered[index] = self._safe_run_package(data, package)
        else:
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="planning-hypothesis"
            ) as executor:
                futures: dict[Future[dict[str, Any]], int] = {
                    executor.submit(self._safe_run_package, data, package): index
                    for index, package in enumerate(packages)
                }
                for future in as_completed(futures):
                    self._check_cancelled()
                    ordered[futures[future]] = future.result()

        runs = [item for item in ordered if isinstance(item, dict)]
        report["hypothesis_runs"] = runs
        usable = [item for item in runs if item.get("final_result")]
        successful = [item for item in runs if item.get("status") == "success"]
        if len(successful) == len(runs):
            report["status"] = "success"
        elif usable:
            report["status"] = "partial_success"
        else:
            report["status"] = "failed"
        report["errors"] = [
            f"Hypothesis {item.get('hypothesis_id')}: {error}"
            for item in runs
            for error in item.get("errors", [])
            if item.get("status") == "failed"
        ]
        report["finished_at"] = _utc_timestamp()
        return report

    def _safe_run_package(
        self, data: dict[str, Any], package: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            return self._run_package(data, package)
        except PlanningExecutionError as exc:
            return _failed_hypothesis_report(package, str(exc))
        except ValueError as exc:
            return _failed_hypothesis_report(package, str(exc))

    def _run_package(
        self, data: dict[str, Any], package: dict[str, Any]
    ) -> dict[str, Any]:
        self._check_cancelled()
        started = time.monotonic()
        task_id = str(data.get("task_id") or "")
        iteration = int(data.get("iteration") or 1)
        hypothesis_id = str(package.get("hypothesis_id") or "")
        run = {
            "schema_version": "planning_protocol_run_v1",
            "hypothesis_id": hypothesis_id,
            "status": "running",
            "workflow_mode": "local_protocol_compiler",
            "next_action": "compile_protocol",
            "stages": [],
            "intermediate_results": {},
            "final_result": None,
            "errors": [],
            "model_call_count": 0,
            "total_tokens": 0,
        }

        brief_context = _event_context(hypothesis_id, "brief")
        self._emit_local("brief", "stage_started", brief_context)
        brief = compile_planning_brief(
            data.get("question_card", {}),
            hypothesis_id,
            package,
            data.get("planning_constraints", {}),
            data.get("user_constraints", {}),
            str(data.get("_feedback") or ""),
        )
        brief_errors = planning_brief_issues(brief)
        run["intermediate_results"]["planning_brief"] = brief
        brief_stage = {
            "name": "brief",
            "status": "failed" if brief_errors else "success",
            "issues": brief_errors,
        }
        run["stages"].append(brief_stage)
        if brief_errors:
            self._emit_local(
                "brief", "stage_failed", brief_context, error="; ".join(brief_errors)
            )
            run["status"] = "failed"
            run["next_action"] = "request_upstream_evidence"
            run["errors"] = brief_errors
            return _finish_run(run, started)
        self._emit_local("brief", "stage_finished", brief_context)

        self._emit(f"Compiling protocol draft for {hypothesis_id}.")
        draft_result = self._call(
            "draft",
            {
                "task_id": task_id,
                "iteration": iteration,
                "hypothesis_id": hypothesis_id,
                "planning_brief": brief,
            },
            _event_context(hypothesis_id, "draft"),
        )
        _record_call(run, draft_result)
        draft = _required_output(draft_result, "protocol_draft")
        draft_contract = _required_output(draft_result, "contract_report")
        run["stages"].append(_stage_record(draft_result, draft_contract))
        run["intermediate_results"]["protocol_draft"] = draft
        if not draft_contract.get("passed"):
            raise PlanningExecutionError(
                "Protocol draft failed deterministic validation: "
                + "; ".join(str(item) for item in draft_contract.get("issues", []))
            )

        self._emit(f"Running scoped protocol reviews for {hypothesis_id}.")
        reviews, review_failures, review_stage_records, review_calls = self._run_reviews(
            task_id, iteration, hypothesis_id, brief, draft
        )
        for result in review_calls:
            _record_call(run, result)
        # Failed review calls have no StageRunResult but still consumed a request.
        run["model_call_count"] += len(review_failures)
        run["stages"].extend(review_stage_records)
        run["intermediate_results"]["specialist_reviews"] = reviews
        merged = merge_protocol_reviews(reviews, review_failures)
        run["intermediate_results"]["merged_reviews"] = merged

        synthesis_brief = compact_for_synthesis(brief, self.synthesis_context_max_chars)
        self._emit(f"Synthesizing reviewed protocol for {hypothesis_id}.")
        try:
            synthesis_result = self._call(
                "synthesis",
                {
                    "task_id": task_id,
                    "iteration": iteration,
                    "hypothesis_id": hypothesis_id,
                    "planning_brief": synthesis_brief,
                    "protocol_draft": draft,
                    "merged_reviews": merged,
                },
                _event_context(hypothesis_id, "synthesis"),
            )
        except PlanningFormatError as exc:
            if not self.max_repair_attempts:
                raise
            run["model_call_count"] += 1
            run["stages"].append(
                {"name": "synthesis", "status": "failed", "issues": [str(exc)]}
            )
            plan_result = {}
            contract = {
                "passed": False,
                "repairable": True,
                "issues": ["synthesis returned invalid JSON"],
            }
        else:
            _record_call(run, synthesis_result)
            plan_result = _required_output(synthesis_result, "plan_result")
            contract = _required_output(synthesis_result, "contract_report")
            run["stages"].append(_stage_record(synthesis_result, contract))

        if not contract.get("passed") and contract.get("repairable") and self.max_repair_attempts:
            self._emit(f"Repairing final protocol contract for {hypothesis_id}.")
            repair_result = self._call(
                "repair",
                {
                    "task_id": task_id,
                    "iteration": iteration,
                    "hypothesis_id": hypothesis_id,
                    "planning_brief": synthesis_brief,
                    "invalid_result": plan_result,
                    "contract_issues": contract.get("issues", []),
                },
                _event_context(hypothesis_id, "repair"),
            )
            _record_call(run, repair_result)
            plan_result = _required_output(repair_result, "plan_result")
            contract = _required_output(repair_result, "contract_report")
            run["stages"].append(_stage_record(repair_result, contract))

        if not contract.get("passed"):
            run["status"] = "failed"
            run["next_action"] = "inspect_contract_failure"
            run["errors"] = [str(item) for item in contract.get("issues", [])]
            run["final_result"] = plan_result
            return _finish_run(run, started)

        if review_failures:
            plan_result["status"] = "partial_success"
            plan = plan_result.get("plan")
            if isinstance(plan, dict):
                limitations = plan.setdefault("limitations", [])
                if isinstance(limitations, list):
                    limitations.append(
                        "Specialist review unavailable: " + ", ".join(sorted(review_failures))
                    )
        run["final_result"] = plan_result
        run["status"] = str(plan_result.get("status") or "success")
        run["next_action"] = "continue_to_product"
        return _finish_run(run, started)

    def _run_reviews(
        self,
        task_id: str,
        iteration: int,
        hypothesis_id: str,
        brief: dict[str, Any],
        draft: dict[str, Any],
    ) -> tuple[
        list[dict[str, Any]],
        dict[str, str],
        list[dict[str, Any]],
        list[StageRunResult],
    ]:
        reviews_by_role: dict[str, dict[str, Any]] = {}
        failures: dict[str, str] = {}
        stage_by_role: dict[str, dict[str, Any]] = {}
        calls_by_role: dict[str, StageRunResult] = {}

        def review(role: str) -> tuple[str, StageRunResult]:
            result = self._call(
                f"review_{role}",
                {
                    "task_id": task_id,
                    "iteration": iteration,
                    "hypothesis_id": hypothesis_id,
                    "planning_brief": brief,
                    "protocol_draft": draft,
                },
                _event_context(hypothesis_id, "review", review_role=role),
            )
            return role, result

        with ThreadPoolExecutor(max_workers=len(REVIEW_ROLES), thread_name_prefix="planning-review") as executor:
            futures = {executor.submit(review, role): role for role in REVIEW_ROLES}
            for future in as_completed(futures):
                role = futures[future]
                try:
                    _, result = future.result()
                    calls_by_role[role] = result
                    contract = _required_output(result, "contract_report")
                    stage_by_role[role] = _stage_record(result, contract, review_role=role)
                    if not contract.get("passed"):
                        failures[role] = "; ".join(
                            str(item) for item in contract.get("issues", [])
                        ) or "review contract failed"
                        continue
                    reviews_by_role[role] = _required_output(result, "protocol_review")
                except PlanningExecutionError as exc:
                    failures[role] = str(exc)
                    stage_by_role[role] = {
                        "name": f"review_{role}",
                        "review_role": role,
                        "status": "failed",
                        "issues": [str(exc)],
                    }

        reviews = [reviews_by_role[role] for role in REVIEW_ROLES if role in reviews_by_role]
        stages = [stage_by_role[role] for role in REVIEW_ROLES]
        calls = [calls_by_role[role] for role in REVIEW_ROLES if role in calls_by_role]
        return reviews, failures, stages, calls

    def _call(
        self, stage: str, inputs: dict[str, Any], event_context: dict[str, Any]
    ) -> StageRunResult:
        while not self._model_slots.acquire(timeout=0.2):
            self._check_cancelled()
        try:
            self._check_cancelled()
            return self.client.run(stage, inputs, event_context)
        finally:
            self._model_slots.release()

    def _check_cancelled(self) -> None:
        if self.cancellation_checker:
            self.cancellation_checker()

    def _emit(self, message: str) -> None:
        if self.progress_handler:
            self.progress_handler(message)

    def _emit_local(
        self,
        stage: str,
        event_name: str,
        context: dict[str, Any],
        **payload: Any,
    ) -> None:
        if not self.event_handler:
            return
        self.event_handler(
            stage,
            {"event": event_name, "stage": stage, **context, **payload},
        )


# One-version compatibility alias for callers that injected the former runner type.
PlanningWorkflowChainRunner = PlanningProtocolRunner


def _event_context(
    hypothesis_id: str, planning_stage: str, *, review_role: str = ""
) -> dict[str, Any]:
    context = {
        "planning_stage": planning_stage,
        "hypothesis_id": hypothesis_id,
        "attempt": 1,
    }
    if review_role:
        context["review_role"] = review_role
    return context


def _select_package(
    packages: list[dict[str, Any]], hypothesis_id: str | None
) -> dict[str, Any]:
    if not packages:
        raise ValueError("No hypothesis evidence package is available.")
    if hypothesis_id is None:
        return packages[0]
    for package in packages:
        if str(package.get("hypothesis_id") or "") == hypothesis_id:
            return package
    raise ValueError(f"Unknown hypothesis_id: {hypothesis_id}")


def _required_output(result: StageRunResult, key: str) -> dict[str, Any]:
    value = result.outputs.get(key)
    if not isinstance(value, dict):
        raise PlanningExecutionError(f"Planning stage {result.stage} omitted {key}.")
    return value


def _record_call(run: dict[str, Any], result: StageRunResult) -> None:
    run["model_call_count"] += 1
    if isinstance(result.total_tokens, int):
        run["total_tokens"] += result.total_tokens


def _stage_record(
    result: StageRunResult,
    contract: dict[str, Any],
    *,
    review_role: str = "",
) -> dict[str, Any]:
    record = {
        "name": result.stage,
        "status": "success" if contract.get("passed") else "failed",
        "run_id": result.run_id,
        "elapsed_time": result.elapsed_time,
        "total_tokens": result.total_tokens,
        "issues": contract.get("issues", []),
    }
    if review_role:
        record["review_role"] = review_role
    return record


def _failed_hypothesis_report(
    package: Mapping[str, Any], error: str
) -> dict[str, Any]:
    return {
        "schema_version": "planning_protocol_run_v1",
        "hypothesis_id": str(package.get("hypothesis_id") or ""),
        "status": "failed",
        "workflow_mode": "local_protocol_compiler",
        "next_action": "inspect_failure",
        "stages": [],
        "intermediate_results": {},
        "final_result": None,
        "errors": [error],
        "model_call_count": 0,
        "total_tokens": 0,
    }


def _finish_run(run: dict[str, Any], started: float) -> dict[str, Any]:
    run["elapsed_time"] = round(time.monotonic() - started, 3)
    return run


def _repair_attempts() -> int:
    if os.getenv("PLANNING_MAX_REPAIR_ATTEMPTS") is not None:
        return _env_int("PLANNING_MAX_REPAIR_ATTEMPTS", 1)
    # Deprecated compatibility alias for one release.
    return _env_int("PLANNING_SELECTOR_MAX_FORMAT_RETRIES", 1)


def _context_limit() -> int:
    if os.getenv("PLANNING_SYNTHESIS_CONTEXT_MAX_CHARS") is not None:
        return _env_int("PLANNING_SYNTHESIS_CONTEXT_MAX_CHARS", 16000)
    return _env_int("PLANNING_FINAL_CONTEXT_MAX_CHARS", 16000)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
