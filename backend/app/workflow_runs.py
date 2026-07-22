from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

from .agent_protocol import CancellationRequested, STAGE_ORDER
from .artifact_service import ArtifactError, ArtifactService
from .contracts import NodeEvent, TaskEvent, utc_now
from .orchestrator import Orchestrator


ACTIVE_RUN_STATUSES = {"queued", "running", "pausing", "paused", "cancelling"}
RESUMABLE_RUN_STATUSES = {"paused", "human_review", "retry", "interrupted", "cancelled"}
FINAL_RUN_STATUSES = {"completed", "cancelled", "failed"}
KNOWLEDGE_PHASES_BY_NODE = {
    "query_planning": ("literature_search", 1),
    "source_search": ("literature_search", 1),
    "source_verify": ("literature_search", 1),
    "relevance_filter": ("literature_search", 1),
    "literature_extract": ("literature_search", 1),
    "evidence_extract": ("evidence_integration", 2),
    "gap_synthesis": ("knowledge_gap_synthesis", 3),
    "quality_review": ("knowledge_gap_synthesis", 3),
}
KNOWLEDGE_PHASE_LABELS = {
    "literature_search": "Literature search",
    "evidence_integration": "Evidence integration",
    "knowledge_gap_synthesis": "Knowledge gap synthesis",
}


class WorkflowRunError(RuntimeError):
    pass


class WorkflowRunManager:
    def __init__(
        self,
        orchestrator: Orchestrator,
        artifacts: ArtifactService,
        *,
        max_workers: int = 4,
    ) -> None:
        self.orchestrator = orchestrator
        self.artifacts = artifacts
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="eurekaloop-run",
        )
        self._lock = threading.RLock()
        self._conditions: dict[str, threading.Condition] = {}
        self._records: dict[str, dict[str, Any]] = {}
        self._run_to_task: dict[str, str] = {}
        self._futures: dict[str, Future[None]] = {}
        self._load_and_recover_runs()

    def shutdown(self, *, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def start(
        self,
        task_id: str,
        *,
        start_stage: str = "question_understanding",
        feedback: str | None = None,
    ) -> dict[str, Any]:
        if not self.artifacts.task_exists(task_id):
            raise WorkflowRunError("Task does not exist")
        if start_stage not in STAGE_ORDER:
            raise WorkflowRunError(f"Unknown start stage: {start_stage}")
        with self._lock:
            context = self.artifacts.load_context(task_id)
            active = [
                record
                for record in self._records.values()
                if record["task_id"] == task_id
                and record["status"] in ACTIVE_RUN_STATUSES
            ]
            if active:
                raise WorkflowRunError(
                    f"Task already has an active workflow run: {active[0]['run_id']}"
                )
            run_id = f"run_{uuid4().hex[:12]}"
            now = utc_now()
            record = {
                "schema_version": "workflow_run_v1",
                "run_id": run_id,
                "task_id": task_id,
                "iteration_id": int(context.get("iteration") or 1),
                "status": "queued",
                "start_stage": start_stage,
                "current_node": start_stage,
                "current_stage": start_stage,
                "feedback": feedback,
                "cancel_requested": False,
                "pause_requested": False,
                "sequence": 0,
                "node_results": [],
                "pending_instructions": [],
                "applied_instructions": [],
                "checkpoints": [],
                "error": None,
                "created_at": now,
                "started_at": None,
                "updated_at": now,
                "finished_at": None,
            }
            self._register(record)
            self._persist(record)
            self._emit(record, "queued", "Workflow run queued.", start_stage)
            self._submit(record, start_stage=start_stage, feedback=feedback)
            return dict(record)

    def add_instruction(
        self,
        run_id: str,
        *,
        comment: str,
        target_stage: str | None = None,
        action: str = "append",
    ) -> dict[str, Any]:
        with self._lock:
            record = self._require_record(run_id)
            if record["status"] in FINAL_RUN_STATUSES:
                raise WorkflowRunError("A finished workflow run cannot accept instructions")
            if target_stage is not None and target_stage not in STAGE_ORDER:
                raise WorkflowRunError(f"Unknown target stage: {target_stage}")
            instruction = {
                "instruction_id": f"instruction_{uuid4().hex[:12]}",
                "comment": comment,
                "target_stage": target_stage,
                "action": action,
                "status": "queued",
                "created_at": utc_now(),
                "applied_at": None,
            }
            record["pending_instructions"] = [
                *list(record.get("pending_instructions") or []),
                instruction,
            ]
            if action == "pause_modify" and record["status"] in {"queued", "running"}:
                record["pause_requested"] = True
                record["status"] = "pausing"
            self._persist(record)
            self._emit(
                record,
                "progress",
                "Operator instruction queued for the next safe node boundary.",
                record.get("current_stage"),
                node_id="operator_instruction",
                payload={
                    "instruction_id": instruction["instruction_id"],
                    "target_stage": target_stage,
                    "action": action,
                },
            )
            return dict(record)

    def get(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._records.get(run_id)
            if record is None:
                raise WorkflowRunError("Workflow run does not exist")
            return dict(record)

    def list_for_task(self, task_id: str) -> list[dict[str, Any]]:
        if not self.artifacts.task_exists(task_id):
            raise WorkflowRunError("Task does not exist")
        with self._lock:
            records = [
                dict(record)
                for record in self._records.values()
                if record["task_id"] == task_id
            ]
        return sorted(records, key=lambda item: item["created_at"], reverse=True)

    def pause(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._require_record(run_id)
            if record["status"] not in {"queued", "running"}:
                raise WorkflowRunError(
                    f"Workflow run cannot pause from status {record['status']}"
                )
            record["pause_requested"] = True
            record["status"] = "pausing"
            self._persist(record)
            self._emit(
                record,
                "pause_requested",
                "Workflow run will pause at the next safe node boundary.",
                record.get("current_stage"),
            )
            return dict(record)

    def resume(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._require_record(run_id)
            if record["status"] not in RESUMABLE_RUN_STATUSES | {"pausing"}:
                raise WorkflowRunError(
                    f"Workflow run cannot resume from status {record['status']}"
                )
            record["pause_requested"] = False
            record["cancel_requested"] = False
            previous_status = record["status"]
            record["status"] = "running"
            record["finished_at"] = None
            record["updated_at"] = utc_now()
            self._persist(record)
            condition = self._conditions.setdefault(
                run_id, threading.Condition(self._lock)
            )
            condition.notify_all()
            future = self._futures.get(run_id)
            if previous_status in {"human_review", "retry", "interrupted", "cancelled"} and (
                future is None or future.done()
            ):
                context = self.artifacts.load_context(record["task_id"])
                extensions = dict(context.get("extensions") or {})
                extensions["workflow_resume"] = {
                    "run_id": run_id,
                    "stage": record.get("current_stage"),
                    "checkpoints": list(record.get("checkpoints") or []),
                }
                context["extensions"] = extensions
                self.artifacts.save_context(record["task_id"], context)
                next_stage = str(context.get("current_stage") or record["current_stage"])
                if next_stage not in STAGE_ORDER:
                    raise WorkflowRunError(
                        f"Task has no resumable current stage: {next_stage}"
                    )
                self._submit(record, start_stage=next_stage, feedback=None)
            self._emit(
                record,
                "resumed",
                "Workflow run resumed.",
                record.get("current_stage"),
            )
            return dict(record)

    def cancel(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._require_record(run_id)
            if record["status"] in FINAL_RUN_STATUSES:
                return dict(record)
            record["cancel_requested"] = True
            record["pause_requested"] = False
            record["status"] = "cancelled"
            record["finished_at"] = utc_now()
            self._persist(record)
            condition = self._conditions.setdefault(
                run_id, threading.Condition(self._lock)
            )
            condition.notify_all()
            self._emit(
                record,
                "cancelled",
                "Workflow run cancelled immediately. Late Agent output will be discarded.",
                record.get("current_stage"),
            )
            return dict(record)

    def _submit(
        self,
        record: dict[str, Any],
        *,
        start_stage: str,
        feedback: str | None,
    ) -> None:
        future = self._executor.submit(
            self._execute,
            record["run_id"],
            start_stage,
            feedback,
        )
        self._futures[record["run_id"]] = future

    def _execute(
        self,
        run_id: str,
        start_stage: str,
        feedback: str | None,
    ) -> None:
        try:
            with self._lock:
                record = self._require_record(run_id)
                if record.get("cancel_requested"):
                    return
                record["status"] = "running"
                record["started_at"] = record.get("started_at") or utc_now()
                self._persist(record)
                self._emit(record, "started", "Workflow run started.", start_stage)

            start_index = STAGE_ORDER.index(start_stage)
            for index, stage in enumerate(STAGE_ORDER[start_index:], start=start_index):
                if not self._wait_until_runnable(run_id, stage):
                    return
                instruction_feedback = self._take_instructions(run_id, stage)
                iteration_feedback = self._iteration_instruction(run_id, stage)
                with self._lock:
                    record = self._require_record(run_id)
                    if record.get("cancel_requested"):
                        return
                    record["current_node"] = stage
                    record["current_stage"] = stage
                    record["status"] = "running"
                    self._persist(record)
                    self._emit(record, "started", f"{stage} started.", stage)

                execution = self.orchestrator.run_stage(
                    record["task_id"],
                    stage,
                    "\n\n".join(
                        part
                        for part in (
                            feedback if index == start_index else None,
                            instruction_feedback,
                            iteration_feedback,
                        )
                        if part
                    )
                    or None,
                    progress_handler=lambda update, active_stage=stage: self._handle_progress(
                        run_id, active_stage, update
                    ),
                    cancellation_checker=lambda: self._check_cancelled(run_id),
                )
                result_status = str(execution.get("status") or "failed")
                context = self.artifacts.load_context(record["task_id"])
                extensions = dict(context.get("extensions") or {})
                if "workflow_resume" in extensions:
                    extensions.pop("workflow_resume", None)
                    context["extensions"] = extensions
                    self.artifacts.save_context(record["task_id"], context)

                with self._lock:
                    record = self._require_record(run_id)
                    record["node_results"] = [
                        *list(record.get("node_results") or []),
                        {
                            "node_id": stage,
                            "stage": stage,
                            "status": result_status,
                            "completed_at": utc_now(),
                        },
                    ]
                    self._persist(record)
                    self._emit(
                        record,
                        "final_output",
                        f"{stage} finished with status {result_status}.",
                        stage,
                        payload={"status": result_status},
                    )

                if self._cancel_if_requested(run_id):
                    return
                if result_status not in {"passed", "completed"}:
                    waiting_status = (
                        "human_review"
                        if result_status == "human_review"
                        else "retry"
                        if result_status == "retry"
                        else "failed"
                    )
                    self._finish(
                        run_id,
                        waiting_status,
                        error=None
                        if waiting_status != "failed"
                        else f"{stage} failed",
                    )
                    return

            self._finish(run_id, "completed")
        except CancellationRequested:
            with self._lock:
                record = self._records.get(run_id)
                if record is not None:
                    self.artifacts.set_stage_status(
                        record["task_id"], record["current_stage"], "cancelled"
                    )
            self._finish(run_id, "cancelled")
        except Exception as exc:
            self._finish(run_id, "failed", error=f"{type(exc).__name__}: {exc}")

    def _iteration_instruction(self, run_id: str, stage: str) -> str | None:
        with self._lock:
            record = self._require_record(run_id)
            context = self.artifacts.load_context(record["task_id"])
            plans = list(context.get("iteration_plans") or [])
            if not plans:
                return None
            plan = plans[-1] if isinstance(plans[-1], dict) else {}
            if int(plan.get("iteration") or 0) != int(record.get("iteration_id") or 0):
                return None
            instructions = plan.get("instructions_by_agent")
            if not isinstance(instructions, dict):
                return None
            instruction = instructions.get(stage)
            return str(instruction) if instruction else None

    def _cooperate(
        self, run_id: str, stage: str, node_id: str | None = None
    ) -> None:
        with self._lock:
            record = self._require_record(run_id)
            if node_id:
                record["current_node"] = node_id
            condition = self._conditions.setdefault(
                run_id, threading.Condition(self._lock)
            )
            if record.get("cancel_requested"):
                raise CancellationRequested()
            while record.get("pause_requested"):
                if record["status"] != "paused":
                    record["status"] = "paused"
                    self._persist(record)
                    self._emit(
                        record,
                        "paused",
                        "Workflow run paused at a safe node boundary.",
                        stage,
                        node_id=node_id,
                    )
                condition.wait()
                if record.get("cancel_requested"):
                    raise CancellationRequested()

    def _check_cancelled(self, run_id: str) -> None:
        with self._lock:
            record = self._require_record(run_id)
            if record.get("cancel_requested"):
                raise CancellationRequested()

    def _handle_progress(
        self, run_id: str, stage: str, update: dict[str, Any]
    ) -> None:
        node_id = str(update.get("node_id") or stage)
        payload = update.get("payload") if isinstance(update.get("payload"), dict) else {}
        payload = dict(payload)
        phase_id = None
        if stage == "knowledge_integration":
            phase_id, phase_index = KNOWLEDGE_PHASES_BY_NODE.get(
                node_id.split(":", 1)[0],
                ("literature_search", 1),
            )
            payload.setdefault("phase_id", phase_id)
            payload.setdefault("phase_index", phase_index)
            payload.setdefault("phase_label", KNOWLEDGE_PHASE_LABELS[phase_id])
        self._cooperate(run_id, stage, node_id)
        with self._lock:
            record = self._require_record(run_id)
            if str(update.get("kind") or "progress") == "partial_output":
                checkpoint = {
                    "node_id": node_id,
                    "stage": stage,
                    "phase_id": phase_id,
                    "payload": payload,
                    "created_at": utc_now(),
                }
                record["checkpoints"] = [
                    item
                    for item in list(record.get("checkpoints") or [])
                    if item.get("node_id") != node_id
                ] + [checkpoint]
                if phase_id:
                    phase_checkpoint = {
                        "node_id": phase_id,
                        "stage": stage,
                        "phase_id": phase_id,
                        "payload": payload,
                        "created_at": utc_now(),
                    }
                    record["checkpoints"] = [
                        item
                        for item in list(record.get("checkpoints") or [])
                        if item.get("node_id") != phase_id
                    ] + [phase_checkpoint]
                self._persist(record)
            self._emit(
                record,
                str(update.get("kind") or "progress"),
                str(update.get("message") or f"{node_id} progress."),
                stage,
                node_id=node_id,
                progress=update.get("progress"),
                payload=payload,
                operation=str(update.get("operation") or "append"),
            )

    def _wait_until_runnable(self, run_id: str, stage: str) -> bool:
        with self._lock:
            record = self._require_record(run_id)
            condition = self._conditions.setdefault(
                run_id, threading.Condition(self._lock)
            )
            if record.get("cancel_requested"):
                self._finish_locked(record, "cancelled")
                return False
            while record.get("pause_requested"):
                if record["status"] != "paused":
                    record["status"] = "paused"
                    record["current_stage"] = stage
                    self._persist(record)
                    self._emit(
                        record,
                        "paused",
                        "Workflow run paused at a safe node boundary.",
                        stage,
                    )
                condition.wait()
                if record.get("cancel_requested"):
                    self._finish_locked(record, "cancelled")
                    return False
            return True

    def _take_instructions(self, run_id: str, stage: str) -> str | None:
        with self._lock:
            record = self._require_record(run_id)
            pending = list(record.get("pending_instructions") or [])
            applicable = [
                item
                for item in pending
                if item.get("target_stage") in {None, stage}
            ]
            if not applicable:
                return None
            applied_at = utc_now()
            for item in applicable:
                item["status"] = "applied"
                item["applied_at"] = applied_at
                item["applied_stage"] = stage
            applicable_ids = {item["instruction_id"] for item in applicable}
            record["pending_instructions"] = [
                item
                for item in pending
                if item.get("instruction_id") not in applicable_ids
            ]
            record["applied_instructions"] = [
                *list(record.get("applied_instructions") or []),
                *applicable,
            ]
            self._persist(record)
            self._emit(
                record,
                "progress",
                f"Applied {len(applicable)} operator instruction(s) to {stage}.",
                stage,
                node_id="operator_instruction",
                payload={"count": len(applicable), "stage": stage},
            )
            return "\n\n".join(str(item["comment"]) for item in applicable)

    def _cancel_if_requested(self, run_id: str) -> bool:
        with self._lock:
            record = self._require_record(run_id)
            if not record.get("cancel_requested"):
                return False
            self._finish_locked(record, "cancelled")
            return True

    def _finish(
        self,
        run_id: str,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        with self._lock:
            record = self._records.get(run_id)
            if record is None:
                return
            if (
                record.get("status") == "cancelled"
                and record.get("cancel_requested")
                and status != "cancelled"
            ):
                return
            self._finish_locked(record, status, error=error)

    def _finish_locked(
        self,
        record: dict[str, Any],
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        record["status"] = status
        record["error"] = error
        record["cancel_requested"] = False
        record["pause_requested"] = False
        if status in FINAL_RUN_STATUSES:
            record["finished_at"] = utc_now()
        self._persist(record)
        event_kind = "failed" if status == "failed" else status
        self._emit(
            record,
            event_kind,
            error or f"Workflow run {status}.",
            record.get("current_stage"),
        )

    def _load_and_recover_runs(self) -> None:
        for manifest in self.artifacts.list_tasks(include_archived=True):
            task_id = str(manifest.get("task_id") or "")
            if not task_id:
                continue
            runs_dir = self.artifacts.task_root(task_id) / "runs"
            if not runs_dir.is_dir():
                continue
            for path in runs_dir.glob("run_*.json"):
                try:
                    record = self.artifacts.read_json(
                        task_id, f"runs/{path.name}"
                    )
                except (ArtifactError, ValueError):
                    continue
                if not isinstance(record, dict) or not record.get("run_id"):
                    continue
                record.setdefault(
                    "iteration_id", int(manifest.get("iteration") or 1)
                )
                record.setdefault("pending_instructions", [])
                record.setdefault("applied_instructions", [])
                record.setdefault("checkpoints", [])
                if record.get("status") in ACTIVE_RUN_STATUSES:
                    record["status"] = "interrupted"
                    record["error"] = "Backend restarted while the workflow run was active."
                    record["updated_at"] = utc_now()
                    self.artifacts.write_json(task_id, f"runs/{path.name}", record)
                    self.artifacts.write_json(task_id, "runs/latest.json", record)
                    if manifest.get("status") in ACTIVE_RUN_STATUSES:
                        self.artifacts.update_manifest(task_id, status="interrupted")
                self._register(record)

    def _register(self, record: dict[str, Any]) -> None:
        run_id = str(record["run_id"])
        task_id = str(record["task_id"])
        self._records[run_id] = record
        self._run_to_task[run_id] = task_id
        self._conditions.setdefault(run_id, threading.Condition(self._lock))

    def _require_record(self, run_id: str) -> dict[str, Any]:
        record = self._records.get(run_id)
        if record is None:
            raise WorkflowRunError("Workflow run does not exist")
        return record

    def _persist(self, record: dict[str, Any]) -> None:
        record["updated_at"] = utc_now()
        task_id = str(record["task_id"])
        run_id = str(record["run_id"])
        self.artifacts.write_json(task_id, f"runs/{run_id}.json", record)
        self.artifacts.write_json(task_id, "runs/latest.json", record)
        current_stage = (
            "completed"
            if record["status"] == "completed"
            else str(record.get("current_stage") or record.get("start_stage"))
        )
        self.artifacts.update_manifest(
            task_id,
            status=record["status"],
            current_stage=current_stage,
            active_run_id=None
            if record["status"] in FINAL_RUN_STATUSES
            else run_id,
        )

    def _emit(
        self,
        record: dict[str, Any],
        kind: str,
        message: str,
        stage: str | None,
        *,
        progress: float | None = None,
        payload: dict[str, Any] | None = None,
        operation: str = "append",
        node_id: str | None = None,
    ) -> None:
        record["sequence"] = int(record.get("sequence") or 0) + 1
        self._persist(record)
        node = NodeEvent(
            event_id=f"evt_{uuid4().hex[:12]}",
            task_id=str(record["task_id"]),
            run_id=str(record["run_id"]),
            node_id=node_id or stage or "workflow",
            stage=stage,
            sequence=record["sequence"],
            kind=kind,
            message=message,
            progress=progress,
            payload=payload or {},
            operation=operation,
        )
        self.artifacts.append_event(
            TaskEvent(
                event_id=node.event_id,
                task_id=node.task_id,
                type=f"node_{kind}",
                stage=node.stage,
                message=node.message,
                data={
                    "schema_version": node.schema_version,
                    "run_id": node.run_id,
                    "node_id": node.node_id,
                    "sequence": node.sequence,
                    "kind": node.kind,
                    "progress": node.progress,
                    "payload": node.payload,
                    "operation": node.operation,
                },
                created_at=node.created_at,
            )
        )
