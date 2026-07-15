"""证据梳理 Agent 主流程。"""

from __future__ import annotations

from collections import defaultdict

from .models import (
    AgentMetadata,
    AgentResponse,
    ConflictPair,
    DetailedReview,
    EvidenceBinding,
    EvidenceCard,
    EvidenceMapItem,
    EvidenceMapPayload,
    EvidenceMappingInput,
    EvidenceSummary,
    GapItem,
    HypothesisCard,
    LiteratureCard,
    RollbackTarget,
    SelfReview,
    SupportDirection,
    Verdict,
)
from .scorer import (
    aggregate_strength,
    match_prediction,
    recheck_direction,
    score_quality,
    split_predictions,
    _overlap_score,
)
from .validator import validate_payload


def _lit_index(cards: list[LiteratureCard]) -> dict[str, LiteratureCard]:
    return {c.literature_id: c for c in cards}


def _summarize(direction: SupportDirection, claims: list[str], empty: str) -> str:
    if not claims:
        return empty
    # 取最多 2 条，避免过长
    joined = "；".join(claims[:2])
    prefix = {
        SupportDirection.SUPPORT: "支持侧：",
        SupportDirection.OPPOSE: "反对侧：",
        SupportDirection.UNCERTAIN: "不确定侧：",
    }.get(direction, "")
    return prefix + joined


def _find_conflicts(
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


def _detect_gaps(
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
    # 因果缺口：假设含机制/中介表述
    stmt = hypothesis.statement
    if any(k in stmt for k in ("通过", "导致", "中介", "因果", "促进")):
        gaps.append(
            GapItem(
                gap_code="missing_causal_link",
                description="假设含因果/中介主张，但现有证据可能仅为相关，需补因果链证据",
                suggested_evidence_type="mediation_or_intervention",
            )
        )
    # 去重 gap_code
    seen = set()
    uniq: list[GapItem] = []
    for g in gaps:
        if g.gap_code in seen:
            continue
        seen.add(g.gap_code)
        uniq.append(g)
    return uniq


def _build_verdict(
    strength: float,
    threshold: float,
    gaps: list[GapItem],
    support_n: int,
    oppose_n: int,
    binding_scores: list[float],
) -> Verdict:
    avg_binding = sum(binding_scores) / len(binding_scores) if binding_scores else 0.0
    # 规范里 detailed_review.verdict.score 用 0-10 量纲
    score = round(avg_binding * 0.6 + strength * 10 * 0.4, 2)
    reason_codes = [g.gap_code for g in gaps]

    passed = score >= threshold and support_n >= 1 and "prediction_uncovered:P0" not in reason_codes
    # 若因果缺口显著且充分性不足，也不通过
    if "missing_causal_link" in reason_codes and strength < 0.8:
        passed = False

    if support_n == 0:
        rollback = RollbackTarget.KNOWLEDGE_INTEGRATION
        suggestion = "回退知识整合：补充至少 1 条可直接支持核心预测的证据，并附 quotes"
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


def review_one_hypothesis(
    hypothesis: HypothesisCard,
    evidence_cards: list[EvidenceCard],
    literature_cards: list[LiteratureCard],
    threshold: float,
    review_idx: int,
) -> EvidenceMapItem:
    lit_map = _lit_index(literature_cards)
    evidence_by_id = {e.evidence_id: e for e in evidence_cards}
    predictions = split_predictions(hypothesis)

    # 候选证据：based_on 必审；其余仅纳入主题相关者，避免串假设污染
    preferred = set(hypothesis.based_on_evidence_ids)
    generic_vars = {"认知下降", "阿尔茨海默病", "alzheimer", "disease", "患者"}
    specific_vars = [v for v in hypothesis.target_variables if v.lower() not in generic_vars]
    candidate_ids: list[str] = []
    for e in evidence_cards:
        if e.evidence_id in preferred:
            candidate_ids.append(e.evidence_id)
            continue
        _, _, ps = match_prediction(e, predictions)
        blob = e.claim + "".join(e.related_concepts)
        var_hit = any(v in blob for v in (specific_vars or hypothesis.target_variables))
        stmt_hit = _overlap_score(e.claim, hypothesis.statement) >= 0.18
        if var_hit or stmt_hit or ps >= 0.18:
            candidate_ids.append(e.evidence_id)
    candidate_ids = list(dict.fromkeys([*hypothesis.based_on_evidence_ids, *candidate_ids]))

    bindings: list[EvidenceBinding] = []
    recheck_delta: list[dict] = []
    by_dir: dict[SupportDirection, list[str]] = defaultdict(list)
    covered: dict[int, set[SupportDirection]] = defaultdict(set)
    quality_pairs: list[tuple[SupportDirection, object]] = []
    claims_by_dir: dict[SupportDirection, list[str]] = defaultdict(list)

    for eid in candidate_ids:
        ev = evidence_by_id.get(eid)
        if not ev:
            continue
        pred_i, pred_text, pred_score = match_prediction(ev, predictions)
        direction, binding_type, note = recheck_direction(ev, hypothesis, pred_score)

        # 记录相对模块2预分类的改判
        hint = ev.support_direction_hint
        if hint and hint != direction:
            recheck_delta.append(
                {
                    "evidence_id": eid,
                    "module2_hint": hint.value,
                    "module4_direction": direction.value,
                    "note": note,
                }
            )

        if direction == SupportDirection.IRRELEVANT:
            continue

        lit = lit_map.get(ev.literature_id) if ev.literature_id else None
        quality = score_quality(ev, lit, direction, binding_type, pred_score)
        limitations: list[str] = []
        if not ev.quotes:
            limitations.append("缺少可核对原文 quotes，可靠性已降权")
        if any(k in (ev.claim + "".join(ev.quotes)) for k in ("相关", "关联", "correlation")):
            limitations.append("表述偏相关，因果强度有限")
        if ev.population_or_model and any(
            k in ev.population_or_model.lower() for k in ("欧美", "western", "us ", "europe")
        ):
            limitations.append("研究人群/模型与目标场景可能不完全匹配")

        binding = EvidenceBinding(
            evidence_id=eid,
            binding_type=binding_type,
            support_direction=direction,
            prediction_index=pred_i,
            prediction_text=pred_text,
            evidence_quality=quality,
            supporting_quotes=ev.quotes[:3],
            contradictory_quotes=[],
            limitations=limitations,
            recheck_note=note,
        )
        bindings.append(binding)
        by_dir[direction].append(eid)
        if pred_i is not None:
            covered[pred_i].add(direction)
        quality_pairs.append((direction, quality))
        claims_by_dir[direction].append(ev.claim)

    support_ids = by_dir[SupportDirection.SUPPORT]
    oppose_ids = by_dir[SupportDirection.OPPOSE]
    uncertain_ids = by_dir[SupportDirection.UNCERTAIN]

    conflicts = _find_conflicts(support_ids, oppose_ids, evidence_by_id)
    gaps = _detect_gaps(
        hypothesis, predictions, covered, len(support_ids), len(oppose_ids)
    )
    strength = aggregate_strength(quality_pairs)
    # 集合充分性微调
    if len(support_ids) >= 2:
        strength = min(1.0, strength + 0.05)
    if len(oppose_ids) >= 2:
        strength = max(0.0, strength - 0.05)
    strength = round(strength, 3)

    binding_scores = [b.evidence_quality.total_score for b in bindings]
    verdict = _build_verdict(
        strength, threshold, gaps, len(support_ids), len(oppose_ids), binding_scores
    )

    main_limitations = []
    for b in bindings:
        main_limitations.extend(b.limitations)
    main_limitations.extend(g.description for g in gaps[:2])
    # 去重保序
    main_limitations = list(dict.fromkeys(main_limitations))[:6]

    summary = EvidenceSummary(
        support=_summarize(
            SupportDirection.SUPPORT,
            claims_by_dir[SupportDirection.SUPPORT],
            "暂无明确支持证据。",
        ),
        oppose=_summarize(
            SupportDirection.OPPOSE,
            claims_by_dir[SupportDirection.OPPOSE],
            "暂无明确反对证据。",
        ),
        uncertain=_summarize(
            SupportDirection.UNCERTAIN,
            claims_by_dir[SupportDirection.UNCERTAIN],
            "暂无不确定类证据。",
        ),
    )

    needs_more = (not verdict.passed) or any(
        g.gap_code.startswith("prediction_uncovered") or g.gap_code == "missing_causal_link"
        for g in gaps
    )

    return EvidenceMapItem(
        hypothesis_id=hypothesis.hypothesis_id,
        supporting_evidence_ids=support_ids,
        opposing_evidence_ids=oppose_ids,
        uncertain_evidence_ids=uncertain_ids,
        evidence_summary=summary,
        evidence_strength_score=strength,
        main_limitations=main_limitations,
        needs_more_evidence=needs_more,
        detailed_review=DetailedReview(
            review_id=f"REV_{review_idx:03d}",
            threshold=threshold,
            evidence_bindings=bindings,
            conflict_pairs=conflicts,
            gaps=gaps,
            recheck_delta=recheck_delta,
            verdict=verdict,
        ),
    )


class EvidenceMappingAgent:
    """模块 4：证据梳理 Agent。

    使用方式（总控侧）：
        agent = EvidenceMappingAgent()
        response = agent.run(EvidenceMappingInput(...))
    """

    agent_id = "evidence_mapping_agent"

    def run(self, data: EvidenceMappingInput | dict) -> AgentResponse:
        inp = (
            data
            if isinstance(data, EvidenceMappingInput)
            else EvidenceMappingInput.model_validate(data)
        )

        items: list[EvidenceMapItem] = []
        for i, hyp in enumerate(inp.hypothesis_cards, start=1):
            items.append(
                review_one_hypothesis(
                    hypothesis=hyp,
                    evidence_cards=inp.evidence_cards,
                    literature_cards=inp.literature_cards,
                    threshold=inp.threshold,
                    review_idx=i,
                )
            )

        payload = EvidenceMapPayload(evidence_map=items)
        issues = validate_payload(payload, inp.evidence_cards)

        passed_n = sum(1 for x in items if x.detailed_review.verdict.passed)
        coverage = passed_n / len(items) if items else 0.0
        avg_strength = (
            sum(x.evidence_strength_score for x in items) / len(items) if items else 0.0
        )
        overall = round(0.5 * coverage + 0.5 * avg_strength, 3)
        self_passed = overall >= 0.75 and not any(
            i.startswith("fatal:") for i in issues
        )

        suggestions: list[str] = []
        for item in items:
            if item.needs_more_evidence and item.detailed_review.verdict.rollback_suggestion:
                suggestions.append(
                    f"{item.hypothesis_id}: {item.detailed_review.verdict.rollback_suggestion}"
                )

        status = "success" if self_passed else "partial_success"
        if not items:
            status = "failed"
            issues.append("fatal: hypothesis_cards 为空")

        return AgentResponse(
            metadata=AgentMetadata(
                task_id=inp.task_id,
                agent_id=self.agent_id,
                stage="evidence_mapping",
                iteration=inp.iteration,
                status=status,
            ),
            payload=payload,
            self_review=SelfReview(
                passed=self_passed,
                overall_score=overall,
                threshold=0.75,
                dimension_scores={
                    "hypothesis_pass_rate": round(coverage, 3),
                    "avg_evidence_strength": round(avg_strength, 3),
                    "schema_validity": 0.0 if any(i.startswith("fatal:") for i in issues) else 1.0,
                },
                issues=issues,
                suggestions=suggestions[:8],
            ),
        )

    def run_dict(self, data: dict) -> dict:
        return self.run(data).model_dump(mode="json")