from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

RunMode = Literal["auto", "manual", "hybrid"]
AgentStatus = Literal["success", "partial_success", "failed"]
ReviewDecision = Literal["accept", "human_review", "retry", "rollback", "fail"]
NodeEventKind = Literal[
    "queued",
    "started",
    "progress",
    "partial_output",
    "final_output",
    "review",
    "pause_requested",
    "paused",
    "resumed",
    "cancel_requested",
    "cancelled",
    "completed",
    "human_review",
    "retry",
    "interrupted",
    "failed",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModelPolicy(BaseModel):
    provider: str = "dashscope"
    model: str = "qwen3.7-max"
    reasoning: Literal["low", "medium", "high", "ultra"] = "high"
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=6144, ge=256, le=131072)
    timeout_seconds: float = Field(default=120, gt=0, le=3600)
    max_retries: int = Field(default=0, ge=0, le=10)
    response_format: Literal["json_object", "text"] = "json_object"
    thinking_enabled: bool = False


class AgentMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    task_id: str
    agent_id: str
    stage: str
    iteration: int = Field(ge=1)
    status: AgentStatus
    trace_id: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class SelfReview(BaseModel):
    model_config = ConfigDict(extra="allow")

    passed: bool
    overall_score: float = Field(ge=0, le=1)
    threshold: float = Field(ge=0, le=1)
    dimension_scores: dict[str, float] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: AgentMetadata
    payload: dict[str, Any]
    self_review: SelfReview


class ReviewScore(BaseModel):
    schema_validity: float = Field(ge=0, le=1)
    required_fields: float = Field(ge=0, le=1)
    downstream_readiness: float = Field(ge=0, le=1)
    evidence_traceability: float = Field(ge=0, le=1)
    iteration_value: float = Field(ge=0, le=1)


class ReviewRecord(BaseModel):
    review_id: str
    task_id: str
    stage: str
    decision: ReviewDecision
    comment: str = ""
    score: ReviewScore
    overall_score: float = Field(ge=0, le=1)
    operator: Literal["system", "human"] = "system"
    issues: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class TaskCreateRequest(BaseModel):
    task_id: str | None = None
    mode: RunMode = "auto"
    original_question: str = Field(min_length=3, max_length=10_000)
    user_constraints: dict[str, Any] = Field(default_factory=dict)
    model_policy: ModelPolicy | None = None

    @field_validator("task_id")
    @classmethod
    def validate_task_id(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.replace("-", "").replace("_", "").isalnum():
            raise ValueError("task_id may contain only letters, numbers, '-' and '_'")
        return value


class StageRunRequest(BaseModel):
    feedback: str | None = Field(default=None, max_length=20_000)


class LegacyStageRunRequest(BaseModel):
    task_context: dict[str, Any] = Field(default_factory=dict)
    feedback: str | None = Field(default=None, max_length=20_000)


class HumanReviewRequest(BaseModel):
    stage: str
    decision: Literal["accept", "retry", "rollback"]
    comment: str = Field(default="", max_length=20_000)
    approval_id: str | None = Field(default=None, max_length=160)


class FeedbackRequest(BaseModel):
    target_stage: str
    comment: str = Field(min_length=1, max_length=20_000)
    rerun_downstream: bool = True
    execute: bool = True
    mode: RunMode | None = None
    reasoning_level: Literal["low", "medium", "high", "ultra"] | None = None
    memory_level: Literal["low", "medium", "high"] | None = None


class TaskArchiveRequest(BaseModel):
    archived: bool = True


class WorkflowStartRequest(BaseModel):
    start_stage: str = "question_understanding"
    feedback: str | None = Field(default=None, max_length=20_000)


class RunInstructionRequest(BaseModel):
    comment: str = Field(min_length=1, max_length=20_000)
    target_stage: str | None = None
    action: Literal["append", "pause_modify"] = "append"


class ControllerRouteRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    execute: bool = True


class PlanEvaluationRequest(BaseModel):
    user_score: int = Field(ge=1, le=5)
    comment: str = Field(min_length=1, max_length=20_000)
    problem_type: str | None = None
    execute: bool = True


class NodeExecuteRequest(BaseModel):
    mode: Literal["only", "to", "from"] = "only"
    input_override: dict[str, Any] = Field(default_factory=dict)
    feedback: str | None = Field(default=None, max_length=20_000)
    validate_only: bool = False


class NodeEvent(BaseModel):
    schema_version: Literal["node_event_v1"] = "node_event_v1"
    event_id: str
    task_id: str
    run_id: str
    node_id: str
    stage: str | None = None
    sequence: int = Field(ge=1)
    kind: NodeEventKind
    message: str
    progress: float | None = Field(default=None, ge=0, le=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    operation: Literal["append", "replace"] = "append"
    created_at: str = Field(default_factory=utc_now)


class TaskEvent(BaseModel):
    event_id: str
    task_id: str
    type: str
    stage: str | None = None
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
