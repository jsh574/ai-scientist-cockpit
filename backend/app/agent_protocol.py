from __future__ import annotations

from dataclasses import asdict, dataclass
from collections.abc import Callable
from typing import Any, Protocol

STAGE_ORDER = (
    "question_understanding",
    "knowledge_integration",
    "hypothesis_generation",
    "evidence_mapping",
    "research_planning",
    "final_review",
)

ProgressHandler = Callable[[dict[str, Any]], None]
CancellationChecker = Callable[[], None]


class CancellationRequested(BaseException):
    pass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1
    backoff_seconds: float = 0
    retry_on: tuple[str, ...] = ("review_gate_retry",)


@dataclass(frozen=True)
class NodeSpec:
    stage: str
    agent_id: str
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    schema_version: str = "node_spec_v1"
    interruptible: bool = False
    retry_policy: RetryPolicy = RetryPolicy()
    hybrid_review: bool = False
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"node_id": self.stage, **asdict(self)}


AgentSpec = NodeSpec


AGENT_SPECS: dict[str, NodeSpec] = {
    "question_understanding": AgentSpec(
        stage="question_understanding",
        agent_id="question_understanding_agent",
        reads=("task_id", "iteration", "user_input"),
        writes=("question_card",),
        description="Normalize a scientific question into a testable question card.",
    ),
    "knowledge_integration": AgentSpec(
        stage="knowledge_integration",
        agent_id="knowledge_integration_agent",
        reads=("task_id", "iteration", "question_card", "user_input"),
        writes=("literature_cards", "evidence_cards", "knowledge_gaps"),
        description="Retrieve and structure traceable literature and evidence.",
    ),
    "hypothesis_generation": AgentSpec(
        stage="hypothesis_generation",
        agent_id="hypothesis_generation_agent",
        reads=(
            "task_id",
            "iteration",
            "question_card",
            "evidence_cards",
            "knowledge_gaps",
            "user_input",
        ),
        writes=("hypothesis_cards",),
        description="Generate evidence-bound and falsifiable hypotheses.",
    ),
    "evidence_mapping": AgentSpec(
        stage="evidence_mapping",
        agent_id="evidence_mapping_agent",
        reads=(
            "task_id",
            "iteration",
            "hypothesis_cards",
            "evidence_cards",
            "literature_cards",
        ),
        writes=("evidence_map",),
        hybrid_review=True,
        description="Bind hypotheses to supporting, opposing, and uncertain evidence.",
    ),
    "research_planning": AgentSpec(
        stage="research_planning",
        agent_id="research_planning_agent",
        reads=(
            "task_id",
            "iteration",
            "question_card",
            "hypothesis_cards",
            "evidence_map",
            "evidence_cards",
            "literature_cards",
            "knowledge_gaps",
            "user_input",
        ),
        writes=("research_plan",),
        hybrid_review=True,
        description="Produce an executable plan with metrics and falsification criteria.",
    ),
    "final_review": AgentSpec(
        stage="final_review",
        agent_id="orchestrator_review_gate",
        reads=(
            "task_id",
            "iteration",
            "question_card",
            "literature_cards",
            "evidence_cards",
            "knowledge_gaps",
            "hypothesis_cards",
            "evidence_map",
            "research_plan",
            "reviews",
            "versions",
            "feedback_events",
        ),
        writes=("final_review",),
        hybrid_review=True,
        description="Audit completeness, traceability, and iteration readiness.",
    ),
}


class AgentRunner(Protocol):
    def run(
        self,
        stage: str,
        task_context: dict[str, Any],
        feedback: str | None = None,
        *,
        progress_handler: ProgressHandler | None = None,
        cancellation_checker: CancellationChecker | None = None,
    ) -> dict[str, Any]: ...


def get_agent_spec(stage: str) -> AgentSpec:
    try:
        return AGENT_SPECS[stage]
    except KeyError as exc:
        raise ValueError(f"Unknown stage: {stage}") from exc


def slice_context(context: dict[str, Any], spec: AgentSpec) -> dict[str, Any]:
    sliced = {key: context.get(key) for key in spec.reads}
    extensions = context.get("extensions")
    if isinstance(extensions, dict) and isinstance(extensions.get(spec.agent_id), dict):
        sliced["extensions"] = dict(extensions[spec.agent_id])
    return sliced


def merge_payload(
    context: dict[str, Any], spec: AgentSpec, payload: dict[str, Any]
) -> dict[str, Any]:
    unexpected = sorted(set(payload) - set(spec.writes) - {"extensions"})
    if unexpected:
        raise ValueError(
            f"{spec.stage} attempted to write fields outside its contract: {unexpected}"
        )
    merged = dict(context)
    for key in spec.writes:
        if key in payload:
            merged[key] = payload[key]
    extension_payload = payload.get("extensions")
    if extension_payload is not None:
        if not isinstance(extension_payload, dict):
            raise ValueError(f"{spec.stage} extensions must be an object")
        extensions = dict(merged.get("extensions") or {})
        namespace = dict(extensions.get(spec.agent_id) or {})
        namespace.update(extension_payload)
        extensions[spec.agent_id] = namespace
        merged["extensions"] = extensions
    return merged


def next_stage(stage: str) -> str | None:
    index = STAGE_ORDER.index(stage)
    return STAGE_ORDER[index + 1] if index + 1 < len(STAGE_ORDER) else None
