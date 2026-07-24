from __future__ import annotations

import inspect
import os
import threading
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from .agent_protocol import (
    CancellationChecker,
    CancellationRequested,
    ProgressHandler,
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
from .controller_assistant import ControllerAssistant
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
        final_review_evaluator: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.registry = registry
        self.artifacts = artifacts
        self.review_gate = review_gate
        self.max_iterations = max_iterations
        self.final_review_evaluator = final_review_evaluator
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

    @staticmethod
    def _valid_references(items: Any, key: str) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return []
        return [
            item
            for item in items
            if isinstance(item, dict) and str(item.get(key) or "").strip()
        ]

    def _stage_preflight_issues(
        self,
        stage: str,
        context: dict[str, Any],
    ) -> list[str]:
        if stage != "hypothesis_generation":
            return []
        issues: list[str] = []
        if not self._valid_references(context.get("knowledge_gaps"), "gap_id"):
            issues.append(
                "knowledge_gaps is empty or invalid. Rerun knowledge_integration before hypothesis_generation."
            )
        if not self._valid_references(context.get("evidence_cards"), "evidence_id"):
            issues.append(
                "evidence_cards is empty or invalid. Rerun knowledge_integration before hypothesis_generation."
            )
        return issues

    def _preflight_response(
        self,
        task_id: str,
        spec: Any,
        iteration: int,
        issues: list[str],
    ) -> dict[str, Any]:
        return {
            "metadata": {
                "task_id": task_id,
                "agent_id": spec.agent_id,
                "stage": spec.stage,
                "iteration": iteration,
                "status": "partial_success",
            },
            "payload": {key: [] for key in spec.writes},
            "self_review": {
                "passed": False,
                "overall_score": 0,
                "threshold": self.review_gate.threshold,
                "dimension_scores": {
                    "prerequisite_readiness": 0,
                    "downstream_readiness": 0,
                },
                "issues": issues,
                "suggestions": [
                    "Rerun knowledge_integration and confirm it writes literature_cards, evidence_cards, and knowledge_gaps."
                ],
            },
        }

    def create_task(self, request: TaskCreateRequest) -> dict[str, Any]:
        task_id = request.task_id or f"task_{uuid4().hex[:12]}"
        constraints = {
            "language": "zh-CN",
            "domain_preference": "auto",
            "max_hypotheses": 5,
            "output_detail_level": "standard",
            "reasoning_level": "high",
            "memory_level": "medium",
            **request.user_constraints,
        }
        configured_max_tokens = int(os.getenv("LLM_MAX_TOKENS", "8192"))
        reasoning = str(constraints.get("reasoning_level") or "high")
        token_limits = {"low": 2048, "medium": 4096, "high": 6144, "ultra": configured_max_tokens}
        policy = request.model_policy.model_copy(deep=True) if request.model_policy else None
        model_policy = (
            policy.model_dump()
            if policy
            else {
                "provider": "dashscope",
                "model": os.getenv("QWEN_MODEL") or os.getenv("LLM_MODEL") or "qwen3.7-max",
                "reasoning": reasoning,
                "temperature": 0.2,
                "max_tokens": min(configured_max_tokens, token_limits.get(reasoning, configured_max_tokens)),
                "timeout_seconds": float(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
                "max_retries": int(os.getenv("LLM_MAX_RETRIES", "0")),
                "response_format": "json_object",
                "thinking_enabled": os.getenv("QWEN_ENABLE_THINKING", "false").lower() == "true" and reasoning in {"high", "ultra"},
            }
        )
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
            "extensions": {},
            "model_policy": model_policy,
            "plan_evaluations": [],
            "iteration_plans": [],
            "controller_routes": [],
        }
        self.artifacts.create_task(context)
        return context

    def get_task(self, task_id: str) -> dict[str, Any]:
        return {
            "manifest": self.artifacts.read_json(task_id, "manifest.json"),
            "task_context": self.artifacts.load_context(task_id),
        }

    def _built_in_final_review(self, context: dict[str, Any]) -> dict[str, Any]:
        evaluator = self.final_review_evaluator
        try:
            review = evaluator(context) if evaluator else ControllerAssistant().evaluate_workflow(context)
        except Exception:
            review = ControllerAssistant().evaluate_workflow(context)
        review = {**review, "checked_at": utc_now()}
        return {
            "metadata": {
                "task_id": context["task_id"],
                "agent_id": "orchestrator_review_gate",
                "stage": "final_review",
                "iteration": context["iteration"],
                "status": "success",
                "trace_id": f"trace_{uuid4().hex[:12]}",
            },
            "payload": {"final_review": review},
            "self_review": {
                "passed": True,
                "overall_score": 1.0,
                "threshold": self.review_gate.threshold,
                "dimension_scores": review.get("dimension_scores") or {},
                "issues": [],
                "suggestions": review.get("suggestions") or [],
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

    def _handled_approval(
        self, context: dict[str, Any], approval_id: str | None
    ) -> dict[str, Any] | None:
        if not approval_id:
            return None
        processed = (context.get("extensions") or {}).get("processed_approvals") or {}
        stored = processed.get(approval_id)
        return dict(stored) if isinstance(stored, dict) else None

    def _remember_approval(
        self,
        context: dict[str, Any],
        approval_id: str | None,
        *,
        stage: str,
        decision: str,
        status: str,
        review: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not approval_id:
            return context
        extensions = dict(context.get("extensions") or {})
        processed = dict(extensions.get("processed_approvals") or {})
        processed[approval_id] = {
            "approval_id": approval_id,
            "stage": stage,
            "decision": decision,
            "status": status,
            "review": review,
            "processed_at": utc_now(),
        }
        extensions["processed_approvals"] = processed
        context["extensions"] = extensions
        self.artifacts.save_context(str(context["task_id"]), context)
        return context

    @staticmethod
    def _feedback_with_memory(
        context: dict[str, Any], stage: str, feedback: str | None
    ) -> str | None:
        constraints = (context.get("user_input") or {}).get("user_constraints") or {}
        memory_level = str(constraints.get("memory_level") or "medium")
        if memory_level == "low":
            return feedback

        feedback_events = [
            item
            for item in context.get("feedback_events") or []
            if isinstance(item, dict)
        ]
        reviews = [
            item
            for item in context.get("reviews") or []
            if isinstance(item, dict) and item.get("stage") == stage
        ]
        event_limit = 2 if memory_level == "medium" else 8
        review_limit = 1 if memory_level == "medium" else 4
        history: list[str] = []
        for item in feedback_events[-event_limit:]:
            summary = str(item.get("input_summary") or "").strip()
            target = (item.get("target") or {}).get("stage")
            if summary:
                history.append(f"Feedback for {target or 'workflow'}: {summary}")
        for item in reviews[-review_limit:]:
            issues = [str(issue) for issue in item.get("issues") or [] if str(issue).strip()]
            if issues:
                history.append(f"Review issues for {stage}: {'; '.join(issues)}")

        parts = [part for part in (feedback, *history) if part]
        return "\n\n".join(parts)[:12000] or None

    @staticmethod
    def _text_values(value: Any, *, max_items: int = 40) -> list[str]:
        result: list[str] = []

        def visit(child: Any) -> None:
            if len(result) >= max_items:
                return
            if isinstance(child, str):
                clean = child.strip()
                if clean:
                    result.append(clean)
                return
            if isinstance(child, dict):
                for key in (
                    "core_question",
                    "research_object",
                    "description",
                    "claim",
                    "hypothesis",
                    "statement",
                    "gap",
                    "title",
                    "summary",
                    "rationale",
                    "expected_result",
                ):
                    if key in child:
                        visit(child.get(key))
                return
            if isinstance(child, list):
                for item in child:
                    visit(item)

        visit(value)
        return result

    def _attachment_retrieval_query(
        self,
        context: dict[str, Any],
        stage: str,
        feedback: str | None,
    ) -> str:
        user_input = context.get("user_input") or {}
        parts: list[str] = [
            str(user_input.get("original_question") or ""),
            str(user_input.get("base_question_description") or ""),
            str(feedback or ""),
            stage,
        ]
        if stage in {"question_understanding", "knowledge_integration"}:
            parts.extend(self._text_values(context.get("question_card")))
        elif stage == "hypothesis_generation":
            parts.extend(self._text_values(context.get("question_card")))
            parts.extend(self._text_values(context.get("evidence_cards")))
            parts.extend(self._text_values(context.get("knowledge_gaps")))
        elif stage == "evidence_mapping":
            parts.extend(self._text_values(context.get("hypothesis_cards")))
            parts.extend(self._text_values(context.get("evidence_cards")))
        elif stage == "research_planning":
            parts.extend(self._text_values(context.get("question_card")))
            parts.extend(self._text_values(context.get("hypothesis_cards")))
            parts.extend(self._text_values(context.get("evidence_map")))
            parts.extend(self._text_values(context.get("knowledge_gaps")))
        else:
            parts.extend(self._text_values(context))
        return "\n".join(part for part in parts if part).strip()[:12000]

    @staticmethod
    def _format_attachment_chunks(chunks: list[dict[str, Any]]) -> str:
        lines = []
        for chunk in chunks:
            lines.append(
                "\n".join(
                    [
                        f"[{chunk.get('citation_id')}] {chunk.get('name')} / {chunk.get('chunk_id')}",
                        str(chunk.get("text") or ""),
                    ]
                )
            )
        return "\n\n".join(lines)

    def _inject_attachment_context(
        self,
        context: dict[str, Any],
        stage: str,
        chunks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        execution_context = dict(context)
        user_input = dict((execution_context.get("user_input") or {}))
        base_description = str(user_input.get("question_description") or "").strip()
        retrieved_text = self._format_attachment_chunks(chunks)
        if retrieved_text:
            user_input["retrieved_attachment_chunks"] = chunks
            user_input["question_description"] = "\n\n".join(
                part
                for part in (
                    base_description,
                    "[Retrieved attachment chunks for this Agent]\n" + retrieved_text,
                )
                if part
            )[:20000]
        execution_context["user_input"] = user_input
        extensions = dict(execution_context.get("extensions") or {})
        retrieval_payload = {
            "schema_version": "attachment_retrieval_v1",
            "stage": stage,
            "chunks": chunks,
        }
        extensions["retrieved_attachment_chunks"] = retrieval_payload
        spec = get_agent_spec(stage)
        agent_extensions = dict(extensions.get(spec.agent_id) or {})
        agent_extensions["attachment_context"] = retrieval_payload
        extensions[spec.agent_id] = agent_extensions
        execution_context["extensions"] = extensions
        return execution_context

    @staticmethod
    def _invalidate_from_stage(
        context: dict[str, Any], target_stage: str
    ) -> tuple[tuple[str, ...], list[str]]:
        empty_values: dict[str, Any] = {
            "question_card": None,
            "literature_cards": [],
            "evidence_cards": [],
            "knowledge_gaps": [],
            "hypothesis_cards": [],
            "evidence_map": [],
            "research_plan": None,
            "final_review": None,
        }
        start = STAGE_ORDER.index(target_stage)
        invalidated_stages = STAGE_ORDER[start:]
        invalidated_fields: list[str] = []
        for stage in invalidated_stages:
            for field in get_agent_spec(stage).writes:
                context[field] = empty_values[field]
                invalidated_fields.append(field)

        invalidated_set = set(invalidated_stages)
        context["reviews"] = [
            review
            for review in context.get("reviews") or []
            if not isinstance(review, dict) or review.get("stage") not in invalidated_set
        ]
        return invalidated_stages, invalidated_fields

    def run_stage(
        self,
        task_id: str,
        stage: str,
        feedback: str | None = None,
        *,
        progress_handler: ProgressHandler | None = None,
        cancellation_checker: CancellationChecker | None = None,
        input_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        spec = get_agent_spec(stage)
        with self._task_lock(task_id):
            context = self.artifacts.load_context(task_id)
            iteration = int(context.get("iteration") or 1)
            effective_feedback = self._feedback_with_memory(context, stage, feedback)
            attachment_query = self._attachment_retrieval_query(
                context, stage, effective_feedback
            )
            attachment_chunks = self.artifacts.search_attachment_chunks(
                task_id, attachment_query, stage=stage, limit=6
            )
            execution_context = (
                self._inject_attachment_context(context, stage, attachment_chunks)
                if attachment_chunks
                else dict(context)
            )
            stage_input = slice_context(execution_context, spec)
            if attachment_chunks:
                stage_input["attachment_context"] = {
                    "schema_version": "attachment_retrieval_v1",
                    "retrieval_mode": "stage_scoped_chunks",
                    "query": attachment_query[:2000],
                    "chunks": attachment_chunks,
                }
            if input_override:
                unknown = sorted(set(input_override) - set(spec.reads) - {"extensions"})
                if unknown:
                    raise OrchestrationError(
                        f"Operator override contains undeclared inputs: {unknown}"
                    )
                stage_input.update(input_override)
                execution_context.update(input_override)
                override_id = f"override_{uuid4().hex[:12]}"
                self.artifacts.write_json(
                    task_id,
                    f"operator_overrides/{override_id}.json",
                    {
                        "override_id": override_id,
                        "stage": stage,
                        "iteration": iteration,
                        "input_override": input_override,
                        "created_at": utc_now(),
                    },
                )
            if effective_feedback:
                stage_input["feedback"] = effective_feedback
            preflight_issues = self._stage_preflight_issues(stage, context)
            self.artifacts.set_stage_status(task_id, stage, "running")
            self.artifacts.write_stage_input(task_id, stage, iteration, stage_input)
            node_run_id = self.artifacts.begin_node_run(
                task_id, stage, iteration, stage_input
            )
            if attachment_chunks:
                self._event(
                    task_id,
                    "attachment_chunks_retrieved",
                    f"{stage} retrieved {len(attachment_chunks)} attachment chunks.",
                    stage,
                    citations=[chunk.get("citation_id") for chunk in attachment_chunks],
                )
            self._event(task_id, "stage_started", f"{stage} started.", stage)

            try:
                if preflight_issues:
                    raw = self._preflight_response(task_id, spec, iteration, preflight_issues)
                    self._event(
                        task_id,
                        "stage_preflight_blocked",
                        f"{stage} prerequisites are not ready.",
                        stage,
                        issues=preflight_issues,
                    )
                elif stage == "final_review":
                    raw = self._built_in_final_review(context)
                else:
                    parameters = inspect.signature(self.registry.run).parameters.values()
                    supports_callbacks = any(
                        parameter.kind == inspect.Parameter.VAR_KEYWORD
                        for parameter in parameters
                    ) or {
                        "progress_handler",
                        "cancellation_checker",
                    }.issubset(inspect.signature(self.registry.run).parameters)
                    if supports_callbacks:
                        raw = self.registry.run(
                            stage,
                            execution_context,
                            effective_feedback,
                            progress_handler=progress_handler,
                            cancellation_checker=cancellation_checker,
                        )
                    else:
                        if cancellation_checker:
                            cancellation_checker()
                        raw = self.registry.run(stage, execution_context, effective_feedback)
                        if cancellation_checker:
                            cancellation_checker()
            except CancellationRequested:
                self.artifacts.finish_node_run(
                    task_id, stage, node_run_id, status="cancelled"
                )
                raise
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
                if stage == "final_review":
                    context = merge_payload(context, spec, response.payload)
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

            self.artifacts.finish_node_run(
                task_id,
                stage,
                node_run_id,
                status=status,
                output=raw,
                review=review.model_dump(),
            )

            return {
                "task_id": task_id,
                "stage": stage,
                "status": status,
                "response": raw,
                "review": review.model_dump(),
                "node_run_id": node_run_id,
                "task_context": context,
            }

    def run_to(
        self,
        task_id: str,
        target_stage: str,
        feedback: str | None = None,
        input_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target_index = STAGE_ORDER.index(target_stage)
        executions = []
        for index, stage in enumerate(STAGE_ORDER[: target_index + 1]):
            execution = self.run_stage(
                task_id,
                stage,
                feedback if stage == target_stage else None,
                input_override=input_override if stage == target_stage else None,
            )
            executions.append(execution)
            if execution["status"] not in {"passed", "completed"}:
                break
        return {
            "task_id": task_id,
            "target_stage": target_stage,
            "status": executions[-1]["status"] if executions else "created",
            "executions": executions,
            "task_context": self.artifacts.load_context(task_id),
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
            approval_id = str(request.approval_id or "").strip() or None
            handled = self._handled_approval(context, approval_id)
            if handled is not None:
                return {
                    "status": handled.get("status", "processed"),
                    "review": handled.get("review"),
                    "task_context": context,
                    "idempotent": True,
                    "approval_id": approval_id,
                }
            current_stage = context.get("current_stage")
            source_review: ReviewRecord | None = None
            quality_override = current_stage == request.stage and request.decision == "accept"
            if quality_override:
                source_review = ReviewRecord.model_validate(
                    self.artifacts.read_json(
                        task_id, f"reviews/{request.stage}.latest.review.json"
                    )
                )
                if source_review.decision != "retry":
                    raise OrchestrationError(
                        "Only a Review Gate retry can be accepted as a quality override"
                    )
            elif current_stage != "human_review":
                raise OrchestrationError(
                    "Task is not waiting for human review or a quality decision"
                )

            if request.decision == "retry":
                result = self.run_stage(task_id, request.stage, request.comment)
                latest = self.artifacts.load_context(task_id)
                self._remember_approval(
                    latest,
                    approval_id,
                    stage=request.stage,
                    decision=request.decision,
                    status=str(result.get("status") or "retry"),
                    review=result.get("review") if isinstance(result.get("review"), dict) else None,
                )
                return {**result, "approval_id": approval_id}

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
                context = self._remember_approval(
                    context,
                    approval_id,
                    stage=request.stage,
                    decision=request.decision,
                    status="rollback",
                )
                return {
                    "status": "rollback",
                    "task_context": context,
                    "approval_id": approval_id,
                }

            raw = self.artifacts.latest_stage_output(task_id, request.stage)
            response = AgentResponse.model_validate(raw)
            if response.metadata.status == "failed":
                raise OrchestrationError("A failed Agent response cannot be accepted")
            human_review = ReviewRecord(
                review_id=f"review_{uuid4().hex[:12]}",
                task_id=task_id,
                stage=request.stage,
                decision="accept",
                comment=request.comment
                or (
                    "Accepted by operator despite the recommended quality threshold."
                    if quality_override
                    else "Accepted by human reviewer."
                ),
                score=source_review.score
                if source_review
                else ReviewScore(
                    schema_validity=1,
                    required_fields=1,
                    downstream_readiness=1,
                    evidence_traceability=1,
                    iteration_value=response.self_review.overall_score,
                ),
                overall_score=source_review.overall_score if source_review else 1,
                operator="human",
            )
            context = self._accept(
                context,
                request.stage,
                response,
                human_review,
                trigger="quality_gate_override" if quality_override else "human_review_accept",
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
                "quality_gate_overridden" if quality_override else "human_review_accepted",
                human_review.comment,
                request.stage,
            )
            context = self._remember_approval(
                context,
                approval_id,
                stage=request.stage,
                decision=request.decision,
                status="passed",
                review=human_review.model_dump(),
            )
            return {
                "status": "passed",
                "review": human_review.model_dump(),
                "task_context": context,
                "approval_id": approval_id,
            }

    def record_feedback(
        self,
        task_id: str,
        target_stage: str,
        comment: str,
        *,
        mode: str | None = None,
        reasoning_level: str | None = None,
        memory_level: str | None = None,
    ) -> dict[str, Any]:
        get_agent_spec(target_stage)
        with self._task_lock(task_id):
            context = self.artifacts.load_context(task_id)
            if mode is not None:
                context["mode"] = mode
            user_input = dict(context.get("user_input") or {})
            constraints = dict(user_input.get("user_constraints") or {})
            if reasoning_level is not None:
                constraints["reasoning_level"] = reasoning_level
                model_policy = dict(context.get("model_policy") or {})
                configured_max_tokens = int(os.getenv("LLM_MAX_TOKENS", "8192"))
                token_limits = {"low": 2048, "medium": 4096, "high": 6144, "ultra": configured_max_tokens}
                model_policy["reasoning"] = reasoning_level
                model_policy["max_tokens"] = min(
                    configured_max_tokens,
                    token_limits.get(reasoning_level, configured_max_tokens),
                )
                context["model_policy"] = model_policy
            if memory_level is not None:
                constraints["memory_level"] = memory_level
            user_input["user_constraints"] = constraints
            context["user_input"] = user_input
            invalidated_stages, invalidated_fields = self._invalidate_from_stage(
                context, target_stage
            )
            iteration = int(context.get("iteration") or 1)
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
            context["current_stage"] = target_stage
            context["feedback_events"] = [*list(context.get("feedback_events") or []), event]
            self.artifacts.snapshot(
                task_id,
                context,
                stage=target_stage,
                trigger="human_feedback",
                changed_fields=[
                    "mode",
                    "user_input.user_constraints",
                    "feedback_events",
                    "reviews",
                    *invalidated_fields,
                ],
            )
            manifest = self.artifacts.read_json(task_id, "manifest.json")
            stage_status = dict(manifest.get("stage_status") or {})
            for stage in invalidated_stages:
                stage_status[stage] = "retrying" if stage == target_stage else "queued"
            self.artifacts.update_manifest(
                task_id,
                current_stage=target_stage,
                status="retrying",
                stage_status=stage_status,
            )
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
        mode: str | None = None,
        reasoning_level: str | None = None,
        memory_level: str | None = None,
    ) -> dict[str, Any]:
        context = self.record_feedback(
            task_id,
            target_stage,
            comment,
            mode=mode,
            reasoning_level=reasoning_level,
            memory_level=memory_level,
        )
        if not execute:
            return {"status": "feedback_recorded", "task_context": context}

        if rerun_downstream:
            return self.run_from(task_id, target_stage, comment)
        return self.run_stage(task_id, target_stage, comment)

    def apply_iteration_plan(
        self,
        task_id: str,
        evaluation: dict[str, Any],
        iteration_plan: dict[str, Any],
    ) -> dict[str, Any]:
        with self._task_lock(task_id):
            context = self.artifacts.load_context(task_id)
            iteration = int(context.get("iteration") or 1) + 1
            if iteration > self.max_iterations:
                raise OrchestrationError("Maximum workflow iterations exceeded")
            agents = [stage for stage in STAGE_ORDER if stage in iteration_plan["agents_to_rerun"]]
            if not agents:
                raise OrchestrationError("Iteration plan selected no runnable agents")
            for field in iteration_plan["artifacts_to_invalidate"]:
                if field in context:
                    context[field] = None if field in {"question_card", "research_plan", "final_review"} else []
            context["iteration"] = iteration
            context["current_stage"] = agents[0]
            extensions = dict(context.get("extensions") or {})
            extensions["iteration_control"] = {
                "status": "active",
                "iteration": iteration,
                "updated_at": utc_now(),
            }
            context["extensions"] = extensions
            evaluation = {**evaluation, "created_at": evaluation.get("created_at") or utc_now()}
            iteration_plan = {
                **iteration_plan,
                "iteration": iteration,
                "created_at": iteration_plan.get("created_at") or utc_now(),
            }
            context["plan_evaluations"] = [*list(context.get("plan_evaluations") or []), evaluation]
            context["iteration_plans"] = [*list(context.get("iteration_plans") or []), iteration_plan]
            self.artifacts.snapshot(
                task_id,
                context,
                stage=agents[0],
                trigger="plan_evaluation",
                changed_fields=["iteration", "plan_evaluations", "iteration_plans", *iteration_plan["artifacts_to_invalidate"]],
            )
            self.artifacts.update_manifest(
                task_id, iteration=iteration, current_stage=agents[0], status="retrying"
            )
            self._event(task_id, "iteration_plan_created", iteration_plan["reason"], agents[0], iteration_plan=iteration_plan)
            return context

    def finish_iteration(self, task_id: str) -> dict[str, Any]:
        with self._task_lock(task_id):
            context = self.artifacts.load_context(task_id)
            if not context.get("research_plan"):
                raise OrchestrationError("Research plan is not available")
            extensions = dict(context.get("extensions") or {})
            extensions["iteration_control"] = {
                "status": "ended",
                "iteration": int(context.get("iteration") or 1),
                "ended_at": utc_now(),
            }
            context["extensions"] = extensions
            context["current_stage"] = "completed"
            self.artifacts.save_context(task_id, context)
            manifest = self.artifacts.read_json(task_id, "manifest.json")
            stage_status = dict(manifest.get("stage_status") or {})
            if context.get("final_review") is not None:
                stage_status["final_review"] = "passed"
            self.artifacts.update_manifest(
                task_id,
                status="completed",
                current_stage="completed",
                active_run_id=None,
                stage_status=stage_status,
            )
            self._event(
                task_id,
                "iteration_ended",
                "The operator ended iterative refinement and entered controller Q&A mode.",
                "final_review",
                iteration=int(context.get("iteration") or 1),
            )
            return context

    def record_controller_route(self, task_id: str, route: dict[str, Any]) -> dict[str, Any]:
        with self._task_lock(task_id):
            context = self.artifacts.load_context(task_id)
            route = {**route, "created_at": route.get("created_at") or utc_now()}
            context["controller_routes"] = [*list(context.get("controller_routes") or []), route]
            self.artifacts.save_context(task_id, context)
            self._event(task_id, "controller_route_created", route["reason"], route.get("target_stage"), route=route)
            return context


__all__ = ["ArtifactError", "OrchestrationError", "Orchestrator"]
