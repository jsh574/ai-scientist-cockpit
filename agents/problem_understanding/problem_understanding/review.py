"""问题卡的确定性质量评审，避免只信任模型自报 confidence。"""
from __future__ import annotations

from typing import Any

from .schema import DOWNSTREAM_REQUIRED_FIELDS, QuestionCard, UserInput


def review_question_card(
    card: QuestionCard,
    user_input: UserInput,
    feedback: str,
    threshold: float = 0.75,
) -> dict[str, Any]:
    data = card.model_dump()
    completeness = sum(bool(data.get(field)) for field in DOWNSTREAM_REQUIRED_FIELDS) / len(
        DOWNSTREAM_REQUIRED_FIELDS
    )

    original = " ".join(user_input.original_question.lower().split())
    core = " ".join(card.core_question.lower().split())
    question_clarity = 1.0 if core and core != original else (0.6 if core else 0.0)
    intent_preservation = 1.0 if card.original_question == user_input.original_question else 0.0

    keyword_score = min(1.0, len(card.search_keywords) / 4.0)
    concept_score = min(1.0, len(card.key_concepts) / 3.0)
    searchability = round(0.6 * keyword_score + 0.4 * concept_score, 3)

    checkpoint_score = min(1.0, len(card.verifiability.checkpoints) / 2.0)
    verifiability = round(
        0.7 * checkpoint_score + 0.3 * float(bool(card.verifiability.type)), 3
    )

    has_directives = any(card.feedback_directives.model_dump().values())
    if not feedback:
        feedback_compliance = 1.0
    elif card.revision_notes:
        feedback_compliance = 1.0
    elif card.unaddressed_feedback or card.feedback_directives.out_of_scope:
        feedback_compliance = 0.8
    elif has_directives:
        feedback_compliance = 0.7
    else:
        feedback_compliance = 0.0

    revision_stability = _revision_stability(card, user_input, feedback)
    confidence_signal = max(0.0, min(1.0, card.confidence))

    dimensions = {
        "field_completeness": round(completeness, 3),
        "question_clarity": round(question_clarity, 3),
        "original_intent_preservation": round(intent_preservation, 3),
        "searchability": searchability,
        "verifiability": verifiability,
        "feedback_compliance": round(feedback_compliance, 3),
        "revision_stability": round(revision_stability, 3),
        "model_confidence": round(confidence_signal, 3),
    }
    overall = round(
        0.20 * completeness
        + 0.12 * question_clarity
        + 0.13 * intent_preservation
        + 0.13 * searchability
        + 0.15 * verifiability
        + 0.12 * feedback_compliance
        + 0.10 * revision_stability
        + 0.05 * confidence_signal,
        3,
    )

    issues: list[str] = []
    suggestions: list[str] = []
    missing = [field for field in DOWNSTREAM_REQUIRED_FIELDS if not data.get(field)]
    if missing:
        issues.append(f"downstream required fields are empty: {missing}")
        suggestions.append("补齐下游必需的问题卡字段。")
    if question_clarity < 1.0:
        issues.append("core_question 未对原问题进行明确消歧或标准化")
        suggestions.append("在保留原意的前提下重写清晰、可研究的核心问题。")
    if verifiability < 0.7:
        issues.append("verifiability.checkpoints 不足，问题缺少可判定验证标准")
        suggestions.append("至少给出两个可回答或可证伪的检查点。")
    if feedback and feedback_compliance == 0.0:
        issues.append("本轮有用户反馈，但输出没有 revision_notes、反馈指令或未处理反馈")
        suggestions.append("显式记录反馈造成的修改，或说明为什么无法处理。")
    if searchability < 0.6:
        issues.append("检索关键词或关键概念覆盖不足")
        suggestions.append("补充中英文检索词和稳定的关键概念。")

    hard_failure = bool(missing) or intent_preservation == 0.0
    return {
        "passed": overall >= threshold and not hard_failure,
        "overall_score": overall,
        "threshold": threshold,
        "dimension_scores": dimensions,
        "issues": issues,
        "suggestions": suggestions,
    }


def _revision_stability(card: QuestionCard, user_input: UserInput, feedback: str) -> float:
    if not user_input.prior_rounds or not user_input.prior_rounds[-1].question_card:
        return 1.0
    prior = user_input.prior_rounds[-1].question_card or {}
    current = card.model_dump()
    ignored = {
        "question_id",
        "version",
        "revision_notes",
        "unaddressed_feedback",
        "feedback_directives",
    }
    comparable = (set(prior) & set(current)) - ignored
    changed = any(prior[key] != current[key] for key in comparable)
    if feedback and changed and card.revision_notes:
        return 1.0
    if feedback and not changed:
        return 0.0
    if changed and not card.revision_notes:
        return 0.4
    return 1.0
