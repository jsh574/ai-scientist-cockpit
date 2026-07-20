"""agent 与 llm_review 共用的判定辅助函数。"""

from __future__ import annotations

from .models import (
    ConflictPair,
    EvidenceCard,
    GapItem,
    HypothesisCard,
    RollbackTarget,
    SupportDirection,
    Verdict,
)


def summarize(direction: SupportDirection, claims: list[str], empty: str) -> str:
    if not claims:
        return empty
    joined = "；".join(claims[:2])
    prefix = {
        SupportDirection.SUPPORT: "支持侧：",
        SupportDirection.OPPOSE: "反对侧：",
        SupportDirection.UNCERTAIN: "不确定侧：",
    }.get(direction, "")
    return prefix + joined


def find_conflicts(
    support_ids: list[str],
    oppose_ids: list[str],
    evidence_by_id: dict[str, EvidenceCard],
) -> list[ConflictPair]:
    pairs: list[ConflictPair] = []
    for s in support_ids[:3]:
        for o in oppose_ids[:3]:
            sc = evidence_by_id[s].claim
            oc = evidence_by_id[o].claim
            pairs.append(
                ConflictPair(
                    evidence_id_a=s,
                    evidence_id_b=o,
                    conflict_reason=f"支持证据称「{sc[:40]}…」，反对证据称「{oc[:40]}…」",
                )
            )
    return pairs[:3]


def detect_gaps(
    hypothesis: HypothesisCard,
    predictions: list[str],
    covered: dict[int, set[SupportDirection]],
    support_n: int,
    oppose_n: int,
) -> list[GapItem]:
    gaps: list[GapItem] = []
    for i, pred in enumerate(predictions):
        dirs = covered.get(i, set())
        if SupportDirection.SUPPORT not in dirs:
            gaps.append(
                GapItem(
                    gap_code=f"prediction_uncovered:P{i}",
                    prediction_index=i,
                    description=f"缺少直接支持预测的证据：{pred}",
                    suggested_evidence_type="causal_or_longitudinal",
                )
            )
    if support_n == 0:
        gaps.append(
            GapItem(
                gap_code="missing_support",
                description="当前无支持证据",
                suggested_evidence_type="primary_study",
            )
        )
    if oppose_n == 0:
        gaps.append(
            GapItem(
                gap_code="why_no_oppose",
                description="未发现明确反对证据；需确认是全面检索后仍无，还是检索不足",
                suggested_evidence_type="contradictory_or_null_result",
            )
        )
    # 领域无关：检测常见因果/机制表述（中英）
    stmt = hypothesis.statement.lower()
    causal_markers = (
        "通过",
        "导致",
        "中介",
        "因果",
        "促进",
        "via",
        "cause",
        "causal",
        "mediat",
        "lead to",
        "leads to",
    )
    if any(k in stmt or k in hypothesis.statement for k in causal_markers):
        gaps.append(
            GapItem(
                gap_code="missing_causal_link",
                description="假设含因果/中介主张，但现有证据可能仅为相关，需补因果链证据",
                suggested_evidence_type="mediation_or_intervention",
            )
        )
    seen: set[str] = set()
    uniq: list[GapItem] = []
    for g in gaps:
        if g.gap_code in seen:
            continue
        seen.add(g.gap_code)
        uniq.append(g)
    return uniq


def build_verdict(
    strength: float,
    threshold: float,
    gaps: list[GapItem],
    support_n: int,
    oppose_n: int,
    binding_scores: list[float],
) -> Verdict:
    avg_binding = sum(binding_scores) / len(binding_scores) if binding_scores else 0.0
    score = round(avg_binding * 0.6 + strength * 10 * 0.4, 2)
    reason_codes = [g.gap_code for g in gaps]

    # 有支持且关键预测未明显落空时，阈值按 85% 软通过，避免「有绑定却全军覆没」
    effective_threshold = threshold * 0.85
    passed = (
        score >= effective_threshold
        and support_n >= 1
        and "prediction_uncovered:P0" not in reason_codes
    )
    # 因果缺口仅在证据链明显偏弱时否决，不再要求 strength≥0.8
    if "missing_causal_link" in reason_codes and strength < 0.45:
        passed = False

    if support_n == 0:
        rollback = RollbackTarget.KNOWLEDGE_INTEGRATION
        suggestion = "回退知识整合：补充至少 1 条可直接支持核心预测的证据"
        reason = "无支持证据，无法形成可追踪支持链"
        reason_codes = list(dict.fromkeys(["missing_support", *reason_codes]))
    elif not passed and any(c.startswith("prediction_uncovered") for c in reason_codes):
        rollback = RollbackTarget.KNOWLEDGE_INTEGRATION
        suggestion = "回退知识整合：按缺口预测定向补证据"
        reason = "关键预测未被证据覆盖：" + "；".join(
            g.description for g in gaps if g.gap_code.startswith("prediction_uncovered")
        )[:160]
    elif not passed and oppose_n >= support_n:
        rollback = RollbackTarget.HYPOTHESIS_GENERATION
        suggestion = "回退假设生成：反对证据不少于支持证据，建议改写或收缩假设主张"
        reason = "反对证据强度不低于支持侧，假设需修订"
        reason_codes = list(dict.fromkeys(["evidence_conflict_unresolved", *reason_codes]))
    elif not passed:
        rollback = RollbackTarget.KNOWLEDGE_INTEGRATION
        suggestion = "回退知识整合或人工审核：提升证据直接性/适用性后再进入研究计划"
        reason = gaps[0].description if gaps else f"综合评分 {score} 未达阈值 {threshold}"
    else:
        rollback = RollbackTarget.NONE
        suggestion = None
        reason = "证据链可支撑进入研究计划（仍可能有局限性）"

    return Verdict(
        score=score,
        passed=passed,
        reason=reason,
        reason_codes=reason_codes,
        rollback_target=rollback,
        rollback_suggestion=suggestion,
    )
