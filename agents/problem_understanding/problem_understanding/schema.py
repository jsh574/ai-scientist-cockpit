"""问题理解模块的数据结构定义 (question_card / 输入输出信封)。

字段设计对齐团队接口文档 v0.2：既覆盖模块2需要的核心契约字段，
也包含赛题强调的"可检验/可迭代"扩展字段。
"""
from __future__ import annotations

from typing import List, Optional, Literal
from pydantic import BaseModel, Field


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


class UserConstraints(BaseModel):
    language: str = "zh"
    domain_preference: Optional[str] = None


class UserInput(BaseModel):
    """对应 task_context.user_input"""
    original_question: str
    question_description: Optional[str] = None
    question_id: Optional[str] = None
    user_constraints: UserConstraints = Field(default_factory=UserConstraints)


DOWNSTREAM_REQUIRED_FIELDS = [
    "core_question", "research_object", "key_concepts",
    "key_variables", "sub_questions", "search_keywords",
]
