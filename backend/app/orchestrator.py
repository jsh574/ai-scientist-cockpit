from __future__ import annotations

import threading
from typing import Any
from uuid import uuid4

from .agent_protocol import (
    STAGE_ORDER,
    AgentRunner,
    get_agent_spec,
    merge_payload,
    next_stage,
    slice_context,
)
from .artifact_service import ArtifactError, ArtifactService
from .contracts import (
    AgentResponse,
    HumanReviewRequest,
    ReviewRecord,
    ReviewScore,
    TaskCreateRequest,
    TaskEvent,
    utc_now,
)
from .review_gate import ReviewGate


class OrchestrationError(RuntimeError):
    pass


class Orchestrator:
    def __init__(
        self,
        registry: AgentRunner,
        artifacts: ArtifactService,
        review_gate: ReviewGate,
        *,
        max_iterations: int = 10,
    ) -> None:
        self.registry = registry
        self.artifacts = artifacts
        self.review_gate = review_gate
        self.max_iterations = max_iterations
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def _task_lock(self, task_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(task_id, threading.RLock())

    def _event(
        self,
        task_id: str,
        event_type: str,
        message: str,
        stage: str | None = None,
        **data: Any,
    ) -> None:
        self.artifacts.append_event(
            TaskEvent(
                event_id=f"evt_{uuid4().hex[:12]}",
                task_id=task_id,
                type=event_type,
                stage=stage,
                message=message,
                data=data,
            )
        )

    def create_task(self, request: TaskCreateRequest) -> dict[str, Any]:
        task_id = request.task_id or f"task_{uuid4().hex[:12]}"
        constraints = {
            "language": "zh-CN",
            "domain_preference": "auto",
            "max_hypotheses": 5,
            "output_detail_level": "standard",
            **request.user_constraints,
        }
        context: dict[str, Any] = {
            "task_id": task_id,
            "mode": request.mode,
            "current_stage": "created",
            "iteration": 1,
            "user_input": {
                "original_question": request.original_question,
                "user_constraints": constraints,
            },
            "question_card": None,
            "literature_cards": [],
            "evidence_cards": [],
            "knowledge_gaps": [],
            "hypothesis_cards": [],
            "evidence_map": [],
            "research_plan": None,
            "final_review": None,
            "reviews": [],
            "versions": [],
            "feedback_events": [],
        }
        self.artifacts.create_task(context)
        return context

    def get_task(self, task_id: str) -> dict[str, Any]:
        return {
            "manifest": self.artifacts.read_json(task_id, "manifest.json"),
            "task_context": self.artifacts.load_context(task_id),
        }

    def _built_in_final_review(self, context: dict[str, Any]) -> dict[str, Any]:
        required = (
            "question_card",
            "literature_cards",
            "evidence_cards",
            "hypothesis_cards",
            "evidence_map",
            "research_plan",
        )
        missing = [key for key in required if context.get(key) in (None, [], {})]
        traceable_literature = all(
            item.get("doi") or item.get("url")
            for item in context.get("literature_cards") or []
            if isinstance(item, dict)
        )
        score = max(0.0, 1.0 - len(missing) / len(required))
        if not traceable_literature:
            score = max(0.0, score - 0.15)
        feedback_count = len(context.get("feedback_events") or [])
        review = {
            "passed": not missing and traceable_literature,
            "overall_score": round(score, 3),
            "strengths": [
                "The full five-agent chain produced structured artifacts.",
                "Evidence and research-plan objects remain traceable by ID.",
            ]
            if not missing
            else [],
            "weaknesses": [
                *[f"Missing or empty artifact: {key}" for key in missing],
                *(
                    []
                    if traceable_literature
                    else ["One or more literature sources are not traceable."]
                ),
                *(
                    []
                    if feedback_count
                    else ["No feedback-driven second iteration has been recorded yet."]
                ),
            ],
            "revision_required": bool(missing) or not traceable_literature,
            "feedback_iterations": feedback_count,
            "checked_at": utc_now(),
        }
        return {
            "metadata": {
                "task_id": context["task_id"],
                "agent_id": "orchestrator_review_gate",
                "stage": "final_review",
                "iteration": context["iteration"],
                "status": "success" if review["passed"] else "partial_success",
                "trace_id": f"trace_{uuid4().hex[:12]}",
            },
            "payload": {"final_review": review},
            "self_review": {
                "passed": review["passed"],
                "overall_score": review["overall_score"],
                "threshold": self.review_gate.threshold,
                "dimension_scores": {
                    "completeness": score,
                    "traceability": 1.0 if traceable_literature else 0.0,
                },
                "issues": review["weaknesses"],
                "suggestions": ["Run a feedback iteration and compare version snapshots."]
                if not feedback_count
                else [],
            },
        }

    def _accept(
        self,
        context: dict[str, Any],
        stage: str,
        response: AgentResponse,
        review: ReviewRecord,
        *,
        trigger: str,
    ) -> dict[str, Any]:
        spec = get_agent_spec(stage)
        merged = merge_payload(context, spec, response.payload)
        merged["reviews"] = [*list(context.get("reviews") or []), review.model_dump()]
        upcoming = next_stage(stage)
        merged["current_stage"] = upcoming or "completed"
        self.artifacts.snapshot(
            str(context["task_id"]),
            merged,
            stage=stage,
            trigger=trigger,
            changed_fields=list(response.payload),
        )
        return merged

    def run_stage(self, task_id: str, stage: str, feedback: str | None = None) -> dict[str, Any]:
        spec = get_agent_spec(stage)
        with self._task_lock(task_id):
            context = self.artifacts.load_context(task_id)
            iteration = int(context.get("iteration") or 1)
            stage_input = slice_context(context, spec)
            if feedback:
                stage_input["feedback"] = feedback
            self.artifacts.set_stage_status(task_id, stage, "running")
            self.artifacts.write_stage_input(task_id, stage, iteration, stage_input)
            self._event(task_id, "stage_started", f"{stage} started.", stage)

            raw = (
                self._built_in_final_review(context)
                if stage == "final_review"
                else self.registry.run(stage, context, feedback)
            )
            self.artifacts.write_stage_output(task_id, stage, iteration, raw)
            response, review = self.review_gate.evaluate(raw, context, spec)
            self.artifacts.write_review(task_id, stage, iteration, review.model_dump())

            if response is not None and review.decision == "accept":
                context = self._accept(
                    context, stage, response, review, trigger="review_gate_accept"
                )
                status = "completed" if stage == "final_review" else "passed"
                self.artifacts.set_stage_status(task_id, stage, status)
                if stage == "final_review":
                    self.artifacts.update_manifest(
                        task_id, status="completed", current_stage="completed"
                    )
                self._event(
                    task_id,
                    "review_gate_passed",
                    f"{stage} passed Review Gate.",
                    stage,
                    score=review.overall_score,
                )
            elif response is not None and review.decision == "human_review":
                context["current_stage"] = "human_review"
                context["reviews"] = [*list(context.get("reviews") or []), review.model_dump()]
                self.artifacts.save_context(task_id, context)
                self.artifacts.set_stage_status(task_id, stage, "human_review")
                status = "human_review"
                self._event(
                    task_id,
                    "human_review_requested",
                    f"{stage} is waiting for a human decision.",
                    stage,
                )
            else:
                context["current_stage"] = stage
                context["reviews"] = [*list(context.get("reviews") or []), review.model_dump()]
                self.artifacts.save_context(task_id, context)
                status = "failed" if review.decision == "fail" else "retry"
                self.artifacts.set_stage_status(task_id, stage, status)
                self._event(
                    task_id,
                    "review_gate_rejected",
                    f"{stage} did not pass Review Gate.",
                    stage,
                    decision=review.decision,
                    issues=review.issues,
                )

            return {
                "task_id": task_id,
                "stage": stage,
                "status": status,
                "response": raw,
                "review": review.model_dump(),
                "task_context": context,
            }

    def run_from(
        self,
        task_id: str,
        start_stage: str = "question_understanding",
        feedback: str | None = None,
    ) -> dict[str, Any]:
        start = STAGE_ORDER.index(start_stage)
        executions = []
        for index, stage in enumerate(STAGE_ORDER[start:], start=start):
            execution = self.run_stage(
                task_id,
                stage,
                feedback if index == start else None,
            )
            executions.append(execution)
            if execution["status"] not in {"passed", "completed"}:
                break
        return {
            "task_id": task_id,
            "status": executions[-1]["status"] if executions else "created",
            "executions": executions,
            "task_context": self.artifacts.load_context(task_id),
        }

    def submit_review(self, task_id: str, request: HumanReviewRequest) -> dict[str, Any]:
        get_agent_spec(request.stage)
        with self._task_lock(task_id):
            context = self.artifacts.load_context(task_id)
            if context.get("current_stage") != "human_review":
                raise OrchestrationError("Task is not waiting for human review")

            if request.decision == "retry":
                return self.run_stage(task_id, request.stage, request.comment)

            if request.decision == "rollback":
                context["current_stage"] = request.stage
                self.artifacts.save_context(task_id, context)
                self.artifacts.set_stage_status(task_id, request.stage, "rollback")
                self._event(
                    task_id,
                    "human_review_rollback",
                    request.comment or f"{request.stage} rolled back.",
                    request.stage,
                )
                return {"status": "rollback", "task_context": context}

            raw = self.artifacts.latest_stage_output(task_id, request.stage)
            response = AgentResponse.model_validate(raw)
            human_review = ReviewRecord(
                review_id=f"review_{uuid4().hex[:12]}",
                task_id=task_id,
                stage=request.stage,
                decision="accept",
                comment=request.comment or "Accepted by human reviewer.",
                score=ReviewScore(
                    schema_validity=1,
                    required_fields=1,
                    downstream_readiness=1,
                    evidence_traceability=1,
                    iteration_value=response.self_review.overall_score,
                ),
                overall_score=1,
                operator="human",
            )
            context = self._accept(
                context,
                request.stage,
                response,
                human_review,
                trigger="human_review_accept",
            )
            self.artifacts.write_review(
                task_id,
                request.stage,
                int(context.get("iteration") or 1),
                human_review.model_dump(),
            )
            self.artifacts.set_stage_status(task_id, request.stage, "passed")
            self._event(
                task_id,
                "human_review_accepted",
                human_review.comment,
                request.stage,
            )
            return {
                "status": "passed",
                "review": human_review.model_dump(),
                "task_context": context,
            }

    def record_feedback(
        self,
        task_id: str,
        target_stage: str,
        comment: str,
    ) -> dict[str, Any]:
        get_agent_spec(target_stage)
        with self._task_lock(task_id):
            context = self.artifacts.load_context(task_id)
            iteration = int(context.get("iteration") or 1) + 1
            if iteration > self.max_iterations:
                raise OrchestrationError(
                    "Maximum workflow iterations exceeded "
                    f"({self.max_iterations}). Export the current task or create a new task "
                    "before submitting more feedback."
                )
            event = {
                "feedback_id": f"feedback_{uuid4().hex[:12]}",
                "round_id": iteration,
                "feedback_type": "human_comment",
                "target": {"stage": target_stage},
                "input_summary": comment,
                "result_summary": "Pending rerun",
                "score_delta": {},
                "controller_action": "feedback_recorded",
                "revision_suggestion": comment,
                "created_at": utc_now(),
            }
            context["iteration"] = iteration
            context["current_stage"] = target_stage
            context["feedback_events"] = [*list(context.get("feedback_events") or []), event]
            self.artifacts.snapshot(
                task_id,
                context,
                stage=target_stage,
                trigger="human_feedback",
                changed_fields=["iteration", "feedback_events"],
            )
            self.artifacts.update_manifest(task_id, iteration=iteration)
            self._event(
                task_id,
                "feedback_received",
                comment,
                target_stage,
                feedback_id=event["feedback_id"],
            )
            return context

    def apply_feedback(
        self,
        task_id: str,
        target_stage: str,
        comment: str,
        *,
        rerun_downstream: bool = True,
        execute: bool = True,
    ) -> dict[str, Any]:
        context = self.record_feedback(task_id, target_stage, comment)
        if not execute:
            return {"status": "feedback_recorded", "task_context": context}

        if rerun_downstream:
            return self.run_from(task_id, target_stage, comment)
        return self.run_stage(task_id, target_stage, comment)


__all__ = ["ArtifactError", "OrchestrationError", "Orchestrator"]
