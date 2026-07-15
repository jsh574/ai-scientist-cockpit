from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from .adapters import REAL_AGENT_STAGES, AgentRegistry
from .agent_protocol import AGENT_SPECS, STAGE_ORDER
from .artifact_service import ArtifactError, ArtifactService
from .contracts import (
    FeedbackRequest,
    HumanReviewRequest,
    LegacyStageRunRequest,
    StageRunRequest,
    TaskCreateRequest,
)
from .orchestrator import OrchestrationError, Orchestrator
from .review_gate import ReviewGate
from .settings import Settings

settings = Settings.from_env()
registry = AgentRegistry(settings)
artifacts = ArtifactService(settings.artifacts_root)
orchestrator = Orchestrator(
    registry,
    artifacts,
    ReviewGate(settings.review_threshold),
    max_iterations=settings.max_iterations,
)

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
    if isinstance(exc, (ArtifactError, OrchestrationError, ValueError)):
        message = str(exc)
        status = 404 if "does not exist" in message else 409
        return HTTPException(status_code=status, detail=message)
    return HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


@app.get("/api/health")
def health() -> dict[str, Any]:
    sources = settings.source_status()
    agent_sources = {key: value for key, value in sources.items() if key in REAL_AGENT_STAGES}
    return {
        "status": "ok" if all(item["available"] for item in agent_sources.values()) else "degraded",
        "version": app.version,
        "protocol_version": "1.0",
        "model": "qwen3.7-max",
        "real_agent_stages": sorted(REAL_AGENT_STAGES),
        "workflow": list(STAGE_ORDER),
        "sources": sources,
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
def list_tasks() -> dict[str, Any]:
    return {"tasks": artifacts.list_tasks()}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    try:
        return orchestrator.get_task(task_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/tasks/{task_id}/start")
async def start_task(task_id: str) -> dict[str, Any]:
    try:
        return await run_in_threadpool(orchestrator.run_from, task_id)
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
        for key, path in {
            "input": f"stages/{stage}/i{iteration:03d}.input.json",
            "output": f"stages/{stage}/latest.output.json",
            "review": f"reviews/{stage}.latest.review.json",
        }.items():
            with suppress(ArtifactError):
                result[key] = artifacts.read_json(task_id, path)
        return result
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
def stream_events(task_id: str, follow: bool = False) -> StreamingResponse:
    async def generate():
        sent = 0
        while True:
            events = artifacts.read_events(task_id)
            for event in events[sent:]:
                yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
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
