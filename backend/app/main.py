from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .adapters import AgentRegistry, REAL_AGENT_STAGES
from .settings import Settings


class StageRunRequest(BaseModel):
    task_context: dict[str, Any] = Field(default_factory=dict)
    feedback: str | None = None


settings = Settings.from_env()
registry = AgentRegistry(settings)
app = FastAPI(
    title="EurekaLoop Agent Gateway",
    version="0.2.0",
    description="AI Scientist 各阶段 Agent 的统一接入层。",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, Any]:
    sources = settings.source_status()
    return {
        "status": "ok" if all(item["available"] for item in sources.values()) else "degraded",
        "real_agent_stages": sorted(REAL_AGENT_STAGES),
        "sources": sources,
    }


@app.post("/api/stages/{stage}/run")
async def run_stage(stage: str, request: StageRunRequest) -> dict[str, Any]:
    return await run_in_threadpool(
        registry.run,
        stage,
        request.task_context,
        request.feedback,
    )
