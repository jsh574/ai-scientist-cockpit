"""与数据规范 v0.1 对齐的数据结构。"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class SupportDirection(str, Enum):
    SUPPORT = "support"
    OPPOSE = "oppose"
    UNCERTAIN = "uncertain"
    IRRELEVANT = "irrelevant"


class BindingType(str, Enum):
    DIRECT_SUPPORT = "direct_support"
    INDIRECT_SUPPORT = "indirect_support"
    DIRECT_OPPOSE = "direct_oppose"
    INDIRECT_OPPOSE = "indirect_oppose"
    UNCERTAIN = "uncertain"
    IRRELEVANT = "irrelevant"


class RollbackTarget(str, Enum):
    KNOWLEDGE_INTEGRATION = "knowledge_integration"
    HYPOTHESIS_GENERATION = "hypothesis_generation"
    NONE = "none"


# ---------- 上游输入（模块 2 / 3） ----------


class LiteratureCard(BaseModel):
    literature_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: Optional[int] = None
    source: Optional[str] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    literature_type: Optional[str] = None
    relevance_score: float = 0.5
    main_findings: list[str] = Field(default_factory=list)
    related_concepts: list[str] = Field(default_factory=list)


class EvidenceCard(BaseModel):
    evidence_id: str
    claim: str
    literature_id: Optional[str] = None
    source_type: Optional[str] = None
    support_direction_hint: Optional[SupportDirection] = None
    confidence: float = 0.5
    quotes: list[str] = Field(default_factory=list)
    related_concepts: list[str] = Field(default_factory=list)
    population_or_model: Optional[str] = None
    method_note: Optional[str] = None
    sample_size: Optional[int] = None


class HypothesisCard(BaseModel):
    hypothesis_id: str
    statement: str
    rationale: str = ""
    based_on_evidence_ids: list[str] = Field(default_factory=list)
    related_gap_ids: list[str] = Field(default_factory=list)
    target_variables: list[str] = Field(default_factory=list)
    expected_observation: str = ""
    validation_idea: str = ""
    initial_scores: dict[str, float] = Field(default_factory=dict)
    predictions: list[str] = Field(
        default_factory=list,
        description="可检验预测列表；若为空，将从 expected_observation 轻量拆分",
    )


class EvidenceMappingInput(BaseModel):
    """总控裁剪后传给模块 4 的输入切片。"""

    task_id: str = "task_001"
    stage: Literal["evidence_mapping"] = "evidence_mapping"
    iteration: int = 1
    hypothesis_cards: list[HypothesisCard]
    evidence_cards: list[EvidenceCard]
    literature_cards: list[LiteratureCard] = Field(default_factory=list)
    threshold: float = 7.0


# ---------- 模块 4 输出 ----------


class EvidenceQuality(BaseModel):
    directness: float = Field(ge=0.0, le=1.0)
    reliability: float = Field(ge=0.0, le=1.0)
    sufficiency: float = Field(ge=0.0, le=1.0)
    applicability: float = Field(ge=0.0, le=1.0)
    total_score: float = Field(ge=0.0, le=10.0)


class EvidenceBinding(BaseModel):
    evidence_id: str
    binding_type: BindingType
    support_direction: SupportDirection
    prediction_index: Optional[int] = None
    prediction_text: Optional[str] = None
    evidence_quality: EvidenceQuality
    supporting_quotes: list[str] = Field(default_factory=list)
    contradictory_quotes: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    recheck_note: Optional[str] = None


class ConflictPair(BaseModel):
    evidence_id_a: str
    evidence_id_b: str
    conflict_reason: str


class GapItem(BaseModel):
    gap_code: str
    prediction_index: Optional[int] = None
    description: str
    suggested_evidence_type: Optional[str] = None


class Verdict(BaseModel):
    score: float
    passed: bool
    reason: str
    reason_codes: list[str] = Field(default_factory=list)
    rollback_target: RollbackTarget = RollbackTarget.NONE
    rollback_suggestion: Optional[str] = None


class DetailedReview(BaseModel):
    review_id: str
    threshold: float
    evidence_bindings: list[EvidenceBinding]
    conflict_pairs: list[ConflictPair] = Field(default_factory=list)
    gaps: list[GapItem] = Field(default_factory=list)
    recheck_delta: list[dict[str, Any]] = Field(default_factory=list)
    verdict: Verdict


class EvidenceSummary(BaseModel):
    support: str
    oppose: str
    uncertain: str


class EvidenceMapItem(BaseModel):
    hypothesis_id: str
    supporting_evidence_ids: list[str]
    opposing_evidence_ids: list[str]
    uncertain_evidence_ids: list[str]
    evidence_summary: EvidenceSummary
    evidence_strength_score: float = Field(ge=0.0, le=1.0)
    main_limitations: list[str] = Field(default_factory=list)
    needs_more_evidence: bool
    detailed_review: DetailedReview

    @field_validator(
        "supporting_evidence_ids",
        "opposing_evidence_ids",
        "uncertain_evidence_ids",
    )
    @classmethod
    def unique_ids(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))


class EvidenceMapPayload(BaseModel):
    evidence_map: list[EvidenceMapItem]


class SelfReview(BaseModel):
    passed: bool
    overall_score: float
    threshold: float = 0.75
    dimension_scores: dict[str, float] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class AgentMetadata(BaseModel):
    task_id: str
    agent_id: str = "evidence_mapping_agent"
    stage: str = "evidence_mapping"
    iteration: int = 1
    status: Literal["success", "partial_success", "failed"] = "success"


class AgentResponse(BaseModel):
    """统一 Agent 响应外壳，供总控合并。"""

    metadata: AgentMetadata
    payload: EvidenceMapPayload
    self_review: SelfReview