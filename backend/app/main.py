from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from .adapters import REAL_AGENT_STAGES, AgentRegistry, ProjectLLMClient
from .controller_assistant import ControllerAssistant
from .agent_protocol import AGENT_SPECS, STAGE_ORDER, slice_context
from .artifact_service import ArtifactError, ArtifactService
from .contracts import (
    FeedbackRequest,
    ControllerRouteRequest,
    HumanReviewRequest,
    LegacyStageRunRequest,
    RunInstructionRequest,
    PlanEvaluationRequest,
    NodeExecuteRequest,
    StageRunRequest,
    TaskArchiveRequest,
    TaskCreateRequest,
    WorkflowStartRequest,
)
from .orchestrator import OrchestrationError, Orchestrator
from .review_gate import ReviewGate
from .settings import Settings
from .workflow_runs import WorkflowRunError, WorkflowRunManager

settings = Settings.from_env()
registry = AgentRegistry(settings)
artifacts = ArtifactService(settings.artifacts_root)
controller_assistant = ControllerAssistant()


def evaluate_final_review(context: dict[str, Any]) -> dict[str, Any]:
    try:
        llm_client = ProjectLLMClient(context)
    except Exception:
        llm_client = None
    return controller_assistant.evaluate_workflow(context, llm_client)


orchestrator = Orchestrator(
    registry,
    artifacts,
    ReviewGate(settings.review_threshold),
    max_iterations=settings.max_iterations,
    final_review_evaluator=evaluate_final_review,
)
workflow_runs = WorkflowRunManager(orchestrator, artifacts)

app = FastAPI(
    title="EurekaLoop AI Scientist",
    version="1.0.0",
    description="Schema-first multi-agent scientific workflow and artifact service.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(
        exc,
        (ArtifactError, OrchestrationError, WorkflowRunError, ValueError),
    ):
        message = str(exc)
        status = 404 if "does not exist" in message else 409
        return HTTPException(status_code=status, detail=message)
    return HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


@app.get("/api/health")
def health() -> dict[str, Any]:
    sources = settings.source_status()
    agent_sources = {key: value for key, value in sources.items() if key in REAL_AGENT_STAGES}
    ready_agent_count = sum(1 for item in agent_sources.values() if item.get("ready"))
    return {
        "status": "ok" if ready_agent_count == len(agent_sources) else "degraded",
        "version": app.version,
        "protocol_version": "1.0",
        "model": os.getenv("QWEN_MODEL") or os.getenv("LLM_MODEL") or "qwen3.7-max",
        "max_iterations": settings.max_iterations,
        "ready_agent_count": ready_agent_count,
        "real_agent_stages": sorted(REAL_AGENT_STAGES),
        "workflow": list(STAGE_ORDER),
        "sources": sources,
        "capabilities": {
            "tasks": True,
            "task_archive": True,
            "task_start": True,
            "workflow_runs": True,
            "workflow_pause": True,
            "workflow_cancel": True,
            "workflow_instructions": True,
            "node_history": True,
            "node_validation": True,
            "controller_router": True,
            "plan_evaluation": True,
            "model_policy": True,
            "stage_run": True,
            "reviews": True,
            "feedback": True,
            "versions": True,
            "artifacts": True,
            "attachments": True,
            "events": True,
            "export": True,
        },
        "attachments": {
            "max_bytes": settings.attachment_max_bytes,
            "allowed_extensions": [".txt", ".md", ".csv", ".json"],
        },
        "llm": {
            "timeout_seconds": float(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
            "max_retries": int(os.getenv("LLM_MAX_RETRIES", "0")),
            "thinking_enabled": os.getenv("QWEN_ENABLE_THINKING", "false").lower()
            == "true",
            "knowledge_max_attempts": int(
                os.getenv("KNOWLEDGE_LLM_MAX_ATTEMPTS", "1")
            ),
        },
        "model_policy": {
            "supported_fields": [
                "provider", "model", "reasoning", "temperature", "max_tokens",
                "timeout_seconds", "max_retries", "response_format", "thinking_enabled",
            ],
            "dify_supported_fields": ["timeout_seconds"],
            "dify_unsupported_fields": [
                "model", "reasoning", "temperature", "max_tokens", "max_retries",
                "response_format", "thinking_enabled",
            ],
        },
        "mcp": {"server": "backend.mcp_server", "transport": "stdio"},
    }


@app.get("/api/agents")
def list_agents() -> dict[str, Any]:
    return {"protocol_version": "1.0", "agents": registry.describe()}


@app.post("/api/stages/{stage}/run")
async def run_stage_legacy(stage: str, request: LegacyStageRunRequest) -> dict[str, Any]:
    """Stateless compatibility endpoint retained for existing Agent clients."""
    if stage not in REAL_AGENT_STAGES:
        raise HTTPException(status_code=404, detail=f"Unknown Agent stage: {stage}")
    return await run_in_threadpool(
        registry.run,
        stage,
        request.task_context,
        request.feedback,
    )


@app.post("/api/tasks", status_code=201)
def create_task(request: TaskCreateRequest) -> dict[str, Any]:
    try:
        context = orchestrator.create_task(request)
        return {"task_id": context["task_id"], "task_context": context}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/tasks")
def list_tasks(include_archived: bool = False) -> dict[str, Any]:
    return {"tasks": artifacts.list_tasks(include_archived=include_archived)}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    try:
        return orchestrator.get_task(task_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/tasks/{task_id}/archive")
def archive_task(task_id: str, request: TaskArchiveRequest) -> dict[str, Any]:
    try:
        manifest = artifacts.set_archived(task_id, request.archived)
        return {"task_id": task_id, "manifest": manifest}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/tasks/{task_id}/attachments")
def list_task_attachments(task_id: str) -> dict[str, Any]:
    try:
        return {"task_id": task_id, "attachments": artifacts.list_attachments(task_id)}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/tasks/{task_id}/attachments")
async def upload_task_attachments(
    task_id: str,
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    try:
        if not artifacts.task_exists(task_id):
            raise ArtifactError("Task does not exist")
        uploaded = []
        context = artifacts.load_context(task_id)
        for upload in files:
            filename = upload.filename or ""
            content = await upload.read(settings.attachment_max_bytes + 1)
            if len(content) > settings.attachment_max_bytes:
                raise ArtifactError(
                    f"Attachment exceeds {settings.attachment_max_bytes} bytes: {filename}"
                )
            item, context = artifacts.add_attachment(
                task_id,
                filename,
                content,
                upload.content_type,
                context_char_limit=settings.attachment_context_chars,
            )
            uploaded.append(item)
        return {"task_id": task_id, "attachments": uploaded, "task_context": context}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/tasks/{task_id}/start", status_code=status.HTTP_202_ACCEPTED)
def start_task(
    task_id: str, request: WorkflowStartRequest | None = None
) -> dict[str, Any]:
    try:
        start_request = request or WorkflowStartRequest()
        run = workflow_runs.start(
            task_id,
            start_stage=start_request.start_stage,
            feedback=start_request.feedback,
        )
        return {"task_id": task_id, "run": run}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/tasks/{task_id}/runs")
def list_task_runs(task_id: str) -> dict[str, Any]:
    try:
        return {"task_id": task_id, "runs": workflow_runs.list_for_task(task_id)}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/runs/{run_id}")
def get_workflow_run(run_id: str) -> dict[str, Any]:
    try:
        return workflow_runs.get(run_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/runs/{run_id}/pause")
def pause_workflow_run(run_id: str) -> dict[str, Any]:
    try:
        return workflow_runs.pause(run_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/runs/{run_id}/resume")
def resume_workflow_run(run_id: str) -> dict[str, Any]:
    try:
        return workflow_runs.resume(run_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/runs/{run_id}/cancel")
def cancel_workflow_run(run_id: str) -> dict[str, Any]:
    try:
        return workflow_runs.cancel(run_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/runs/{run_id}/instructions", status_code=status.HTTP_202_ACCEPTED)
def add_run_instruction(run_id: str, request: RunInstructionRequest) -> dict[str, Any]:
    try:
        return workflow_runs.add_instruction(
            run_id,
            comment=request.comment,
            target_stage=request.target_stage,
            action=request.action,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/tasks/{task_id}/controller/route")
def route_controller_message(task_id: str, request: ControllerRouteRequest) -> dict[str, Any]:
    try:
        context = artifacts.load_context(task_id)
        try:
            llm_client = ProjectLLMClient(context)
        except Exception:
            llm_client = None
        route = controller_assistant.route(context, request.message, llm_client)
        updated = orchestrator.record_controller_route(task_id, route)
        run = None
        if request.execute and route["intent"] == "cancel":
            runs = workflow_runs.list_for_task(task_id)
            active = next(
                (
                    item
                    for item in runs
                    if item["status"]
                    in {"queued", "running", "pausing", "paused", "cancelling"}
                ),
                None,
            )
            if active:
                run = workflow_runs.cancel(active["run_id"])
        elif request.execute and route["intent"] in {
            "modify",
            "rerun_agent",
            "retrieve_more",
        }:
            target = route.get("target_stage") or "research_planning"
            updated = orchestrator.record_feedback(
                task_id, target, route["optimized_instruction"]
            )
            run = workflow_runs.start(
                task_id,
                start_stage=target,
                feedback=route["optimized_instruction"],
            )
        return {
            "task_id": task_id,
            "route": route,
            "task_context": updated,
            "run": run,
        }
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/tasks/{task_id}/plan-evaluations")
def evaluate_research_plan(task_id: str, request: PlanEvaluationRequest) -> dict[str, Any]:
    try:
        context = artifacts.load_context(task_id)
        try:
            llm_client = ProjectLLMClient(context)
        except Exception:
            llm_client = None
        evaluation, iteration_plan = controller_assistant.evaluate_plan(
            context,
            request.user_score,
            request.comment,
            request.problem_type,
            llm_client,
        )
        updated = orchestrator.apply_iteration_plan(task_id, evaluation, iteration_plan)
        run = workflow_runs.start(
            task_id,
            start_stage=iteration_plan["agents_to_rerun"][0],
            feedback=request.comment,
        ) if request.execute else None
        return {
            "task_id": task_id,
            "evaluation": evaluation,
            "iteration_plan": iteration_plan,
            "task_context": updated,
            "run": run,
        }
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/tasks/{task_id}/iterations/finish")
def finish_task_iteration(task_id: str) -> dict[str, Any]:
    try:
        return {
            "task_id": task_id,
            "task_context": orchestrator.finish_iteration(task_id),
        }
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/tasks/{task_id}/stages/{stage}/run")
async def run_task_stage(task_id: str, stage: str, request: StageRunRequest) -> dict[str, Any]:
    try:
        return await run_in_threadpool(orchestrator.run_stage, task_id, stage, request.feedback)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/tasks/{task_id}/stages")
def task_stages(task_id: str) -> dict[str, Any]:
    try:
        manifest = artifacts.read_json(task_id, "manifest.json")
        return {
            "task_id": task_id,
            "current_stage": manifest.get("current_stage"),
            "stages": [
                {
                    **AGENT_SPECS[stage].as_dict(),
                    "status": (manifest.get("stage_status") or {}).get(stage, "queued"),
                }
                for stage in STAGE_ORDER
            ],
        }
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/tasks/{task_id}/stages/{stage}")
def task_stage_detail(task_id: str, stage: str) -> dict[str, Any]:
    try:
        spec = AGENT_SPECS.get(stage)
        if spec is None:
            raise ValueError(f"Unknown stage: {stage}")
        manifest = artifacts.read_json(task_id, "manifest.json")
        result: dict[str, Any] = {
            "task_id": task_id,
            "stage": spec.as_dict(),
            "status": (manifest.get("stage_status") or {}).get(stage, "queued"),
            "input": None,
            "output": None,
            "review": None,
        }
        iteration = int(manifest.get("iteration") or 1)
        status = str(result["status"])
        output_is_valid = status not in {"queued", "retrying", "rollback", "running"}
        output: dict[str, Any] | None = None
        if output_is_valid:
            with suppress(ArtifactError):
                output = artifacts.read_json(task_id, f"stages/{stage}/latest.output.json")
                result["output"] = output
            with suppress(ArtifactError):
                result["review"] = artifacts.read_json(
                    task_id, f"reviews/{stage}.latest.review.json"
                )

        output_iteration = (
            int(((output or {}).get("metadata") or {}).get("iteration") or iteration)
            if output_is_valid
            else iteration
        )
        with suppress(ArtifactError):
            result["input"] = artifacts.read_json(
                task_id, f"stages/{stage}/i{output_iteration:03d}.input.json"
            )
        return result
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/tasks/{task_id}/stage-history")
def task_stage_history(task_id: str) -> dict[str, Any]:
    try:
        history = artifacts.list_stage_history(task_id, list(STAGE_ORDER))
        return {
            "task_id": task_id,
            "history": [
                {
                    "task_id": task_id,
                    "stage": AGENT_SPECS[item["metadata"]["stage"]].as_dict(),
                    "status": item["metadata"]["status"],
                    "iteration": item["metadata"]["iteration"],
                    "node_run_id": item["metadata"]["node_run_id"],
                    "started_at": item["metadata"]["started_at"],
                    "finished_at": item["metadata"].get("finished_at"),
                    "input": item["input"],
                    "output": item["output"],
                    "review": item["review"],
                }
                for item in history
            ],
        }
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/tasks/{task_id}/nodes/{node_id}/runs")
def list_node_runs(task_id: str, node_id: str) -> dict[str, Any]:
    try:
        if node_id not in AGENT_SPECS:
            raise ValueError(f"Unknown node: {node_id}")
        return {
            "task_id": task_id,
            "node_id": node_id,
            "runs": artifacts.list_node_runs(task_id, node_id),
        }
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/tasks/{task_id}/nodes/{node_id}/runs/{node_run_id}")
def node_run_detail(
    task_id: str, node_id: str, node_run_id: str
) -> dict[str, Any]:
    try:
        if node_id not in AGENT_SPECS:
            raise ValueError(f"Unknown node: {node_id}")
        return artifacts.get_node_run(task_id, node_id, node_run_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/tasks/{task_id}/nodes/{node_id}/runs-diff")
def node_run_diff(
    task_id: str,
    node_id: str,
    left: str = Query(...),
    right: str = Query(...),
) -> dict[str, Any]:
    try:
        return artifacts.node_run_diff(task_id, node_id, left, right)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/tasks/{task_id}/nodes/{node_id}/execute")
async def execute_node(
    task_id: str, node_id: str, request: NodeExecuteRequest
) -> dict[str, Any]:
    try:
        spec = AGENT_SPECS.get(node_id)
        if spec is None:
            raise ValueError(f"Unknown node: {node_id}")
        context = artifacts.load_context(task_id)
        node_input = slice_context(context, spec)
        node_input.update(request.input_override)
        missing = [key for key, value in node_input.items() if value is None]
        if request.validate_only:
            return {
                "task_id": task_id,
                "node_id": node_id,
                "valid": not missing,
                "missing_fields": missing,
                "input": node_input,
            }
        if request.mode == "from":
            run = workflow_runs.start(
                task_id, start_stage=node_id, feedback=request.feedback
            )
            return {"task_id": task_id, "mode": "from", "run": run}
        if request.mode == "to":
            result = await run_in_threadpool(
                orchestrator.run_to,
                task_id,
                node_id,
                request.feedback,
                request.input_override,
            )
            return {"task_id": task_id, "mode": "to", "result": result}
        result = await run_in_threadpool(
            orchestrator.run_stage,
            task_id,
            node_id,
            request.feedback,
            input_override=request.input_override,
        )
        return {"task_id": task_id, "mode": "only", "result": result}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/tasks/{task_id}/nodes/{node_id}/validate")
def validate_node_input(task_id: str, node_id: str) -> dict[str, Any]:
    try:
        spec = AGENT_SPECS.get(node_id)
        if spec is None:
            raise ValueError(f"Unknown node: {node_id}")
        context = artifacts.load_context(task_id)
        node_input = slice_context(context, spec)
        missing = [
            key
            for key, value in node_input.items()
            if value is None or (key not in {"reviews", "versions", "feedback_events"} and value == [])
        ]
        downstream = STAGE_ORDER[STAGE_ORDER.index(node_id) :]
        invalidated_fields = sorted(
            {
                field
                for stage in downstream
                for field in AGENT_SPECS[stage].writes
            }
        )
        return {
            "task_id": task_id,
            "node_id": node_id,
            "valid": not missing,
            "missing_fields": missing,
            "input": node_input,
            "would_invalidate": invalidated_fields,
        }
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/tasks/{task_id}/reviews")
async def submit_review(task_id: str, request: HumanReviewRequest) -> dict[str, Any]:
    try:
        return await run_in_threadpool(orchestrator.submit_review, task_id, request)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/tasks/{task_id}/feedback")
async def apply_feedback(task_id: str, request: FeedbackRequest) -> dict[str, Any]:
    try:
        return await run_in_threadpool(
            orchestrator.apply_feedback,
            task_id,
            request.target_stage,
            request.comment,
            rerun_downstream=request.rerun_downstream,
            execute=request.execute,
            mode=request.mode,
            reasoning_level=request.reasoning_level,
            memory_level=request.memory_level,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/tasks/{task_id}/context")
def get_context(task_id: str) -> dict[str, Any]:
    try:
        return artifacts.load_context(task_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/tasks/{task_id}/versions")
def list_versions(task_id: str) -> dict[str, Any]:
    try:
        return {"task_id": task_id, "versions": artifacts.list_versions(task_id)}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/tasks/{task_id}/versions/diff")
def version_diff(task_id: str, left: str = Query(...), right: str = Query(...)) -> dict[str, Any]:
    try:
        return artifacts.version_diff(task_id, left, right)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/tasks/{task_id}/artifacts")
def list_task_artifacts(task_id: str) -> dict[str, Any]:
    try:
        return {"task_id": task_id, "artifacts": artifacts.list_artifacts(task_id)}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/tasks/{task_id}/artifacts/{artifact_path:path}")
def download_artifact(task_id: str, artifact_path: str) -> FileResponse:
    try:
        path = artifacts._resolve(task_id, artifact_path)
        if not path.is_file():
            raise ArtifactError(f"Artifact does not exist: {artifact_path}")
        return FileResponse(path, filename=Path(artifact_path).name)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/tasks/{task_id}/events")
def list_events(task_id: str) -> dict[str, Any]:
    try:
        return {"task_id": task_id, "events": artifacts.read_events(task_id)}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/tasks/{task_id}/events/stream")
def stream_events(
    task_id: str,
    follow: bool = False,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    async def generate():
        sent = after
        while True:
            events = artifacts.read_events(task_id)
            for index, event in enumerate(events[sent:], start=sent + 1):
                yield (
                    f"id: {index}\n"
                    f"event: message\n"
                    f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                )
            sent = len(events)
            if not follow:
                break
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    if not artifacts.task_exists(task_id):
        raise HTTPException(status_code=404, detail="Task does not exist")
    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/tasks/{task_id}/export")
def export_task(task_id: str) -> FileResponse:
    try:
        path = artifacts.export_task(task_id)
        return FileResponse(path, filename=path.name, media_type="application/zip")
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/contracts")
def contracts() -> dict[str, Any]:
    return {
        "protocol_version": "1.0",
        "stages": [AGENT_SPECS[stage].as_dict() for stage in STAGE_ORDER],
    }


__all__ = ["app", "artifacts", "orchestrator", "registry", "settings"]
