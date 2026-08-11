"""问题理解模块的数据结构定义 (question_card / 输入输出信封)。

字段设计对齐团队接口文档 v0.2：既覆盖模块2需要的核心契约字段，
也包含赛题强调的"可检验/可迭代"扩展字段。
"""
from __future__ import annotations

from typing import List, Optional, Literal
from pydantic import BaseModel, Field, model_validator


INPUT_SCHEMA_VERSION = "problem_understanding_input_v1"
OUTPUT_SCHEMA_VERSION = "problem_understanding_output_v1"
ROUND_SNAPSHOT_SCHEMA_VERSION = "problem_understanding_round_v1"


QuestionType = Literal[
    "mechanism", "causal", "descriptive", "predictive",
    "comparative", "existence", "optimization", "definition",
]

VariableRole = Literal[
    "target", "independent", "dependent",
    "outcome", "mediator", "condition", "control",
]


class KeyVariable(BaseModel):
    name: str
    role: VariableRole = "independent"
    category: str = ""


class QuestionContext(BaseModel):
    region: Optional[str] = None
    time_scale: Optional[str] = None
    spatial_scale: Optional[str] = None
    conditions: List[str] = Field(default_factory=list)


class ResearchScope(BaseModel):
    included: List[str] = Field(default_factory=list)
    excluded: List[str] = Field(default_factory=list)


class Verifiability(BaseModel):
    is_verifiable: bool = True
    type: str = "observational"
    checkpoints: List[str] = Field(default_factory=list)


class Assumption(BaseModel):
    point: str
    default_choice: str = ""
    need_human: bool = False


class RevisionNote(BaseModel):
    """迭代轮的改动留痕：哪个字段、改了什么、由什么驱动。"""
    field: str
    change: str
    driven_by: str = "user_feedback"


class FeedbackDirectives(BaseModel):
    """迭代反馈在问题理解职责内的结构化落点。"""
    question_reframe: List[str] = Field(default_factory=list)
    scope_changes: List[str] = Field(default_factory=list)
    concept_updates: List[str] = Field(default_factory=list)
    constraint_updates: List[str] = Field(default_factory=list)
    out_of_scope: List[str] = Field(default_factory=list)


class QuestionCard(BaseModel):
    question_id: str
    version: int = 1
    original_question: str
    core_question: str
    question_type: QuestionType = "mechanism"
    domain: List[str] = Field(default_factory=list)
    research_object: str = ""
    context: QuestionContext = Field(default_factory=QuestionContext)
    key_concepts: List[str] = Field(default_factory=list)
    key_variables: List[KeyVariable] = Field(default_factory=list)
    sub_questions: List[str] = Field(default_factory=list)
    research_scope: ResearchScope = Field(default_factory=ResearchScope)
    search_keywords: List[str] = Field(default_factory=list)
    verifiability: Verifiability = Field(default_factory=Verifiability)
    assumptions: List[Assumption] = Field(default_factory=list)
    confidence: float = 0.7
    revision_notes: List[RevisionNote] = Field(default_factory=list)
    unaddressed_feedback: List[str] = Field(default_factory=list)
    feedback_directives: FeedbackDirectives = Field(default_factory=FeedbackDirectives)


class UserConstraints(BaseModel):
    language: str = "zh"
    domain_preference: Optional[str] = None


class PromptSnapshot(BaseModel):
    """某一轮实际发送给 LLM 的完整 Prompt。"""
    system: str = ""
    user: str = ""


class PriorRound(BaseModel):
    """一轮完整快照，可由总控回传，也可由模块内部状态库恢复。"""
    schema_version: str = ROUND_SNAPSHOT_SCHEMA_VERSION
    iteration: int = 1
    user_feedback: str = ""
    prompt_snapshot: PromptSnapshot = Field(default_factory=PromptSnapshot)
    run_result: dict = Field(default_factory=dict)
    question_card: Optional[dict] = None

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_snapshot_fields(cls, value):
        """兼容此前的 round/user_prompt 输入，避免已有调用方立刻失效。"""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "iteration" not in data and "round" in data:
            data["iteration"] = data["round"]
        if "prompt_snapshot" not in data and data.get("user_prompt"):
            data["prompt_snapshot"] = {"user": data["user_prompt"]}
        return data


class UserInput(BaseModel):
    """对应 task_context.user_input"""
    original_question: str
    question_description: Optional[str] = None
    question_id: Optional[str] = None
    user_feedback: str = ""
    prior_rounds: List[PriorRound] = Field(default_factory=list)
    reset_history: bool = False
    user_constraints: UserConstraints = Field(default_factory=UserConstraints)


class ProblemUnderstandingRequest(BaseModel):
    """与其他 Agent 对齐的显式总控请求契约。"""
    schema_version: Literal["problem_understanding_input_v1"] = INPUT_SCHEMA_VERSION
    task_id: str
    stage: Literal["question_understanding"] = "question_understanding"
    iteration: int = Field(default=1, ge=1)
    feedback: str = ""
    input: UserInput

    @model_validator(mode="before")
    @classmethod
    def accept_team_feedback_field(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if not data.get("feedback"):
            data["feedback"] = data.get("_feedback") or data.get("_fedback") or ""
        return data


class AgentMetadata(BaseModel):
    task_id: str
    agent_id: str = "question_understanding_agent"
    stage: Literal["question_understanding"] = "question_understanding"
    iteration: int = 1
    status: Literal["success", "partial_success", "failed"] = "success"


class SelfReview(BaseModel):
    passed: bool
    overall_score: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    dimension_scores: dict[str, float] = Field(default_factory=dict)
    issues: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)


class ProblemUnderstandingPayload(BaseModel):
    schema_version: Literal["problem_understanding_output_v1"] = OUTPUT_SCHEMA_VERSION
    question_card: Optional[QuestionCard] = None
    prompt_snapshot: Optional[PromptSnapshot] = None
    round_snapshot: Optional[PriorRound] = None


class ProblemUnderstandingResponse(BaseModel):
    metadata: AgentMetadata
    payload: ProblemUnderstandingPayload
    self_review: SelfReview


DOWNSTREAM_REQUIRED_FIELDS = [
    "core_question", "research_object", "key_concepts",
    "key_variables", "sub_questions", "search_keywords",
]
