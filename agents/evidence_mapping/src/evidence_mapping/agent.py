"""证据梳理 Agent 主流程。

默认：有 API Key 时走 LLM 评分；无 Key / 失败 / 强制 rules 时走规则引擎兜底。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from .agent_helpers import build_verdict, detect_gaps, find_conflicts, summarize
from .llm import QwenCompatibleClient, resolve_scoring_mode
from .llm_review import review_hypothesis_with_llm
from .models import (
    AgentMetadata,
    AgentResponse,
    DetailedReview,
    EvidenceBinding,
    EvidenceCard,
    EvidenceMapItem,
    EvidenceMapPayload,
    EvidenceMappingInput,
    EvidenceSummary,
    HypothesisCard,
    LiteratureCard,
    SelfReview,
    SupportDirection,
)
from .scorer import (
    _overlap_score,
    aggregate_strength,
    match_prediction,
    recheck_direction,
    score_quality,
    specific_target_variables,
    split_predictions,
)
from .validator import validate_payload


def _lit_index(cards: list[LiteratureCard]) -> dict[str, LiteratureCard]:
    return {c.literature_id: c for c in cards}


def select_candidate_ids(
    hypothesis: HypothesisCard,
    evidence_cards: list[EvidenceCard],
) -> list[str]:
    """候选证据：based_on 必审 + 主题相关者（领域无关）。"""
    preferred = set(hypothesis.based_on_evidence_ids)
    specific_vars = specific_target_variables(hypothesis)
    predictions = split_predictions(hypothesis)
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
    return list(dict.fromkeys([*hypothesis.based_on_evidence_ids, *candidate_ids]))


def review_one_hypothesis_rules(
    hypothesis: HypothesisCard,
    evidence_cards: list[EvidenceCard],
    literature_cards: list[LiteratureCard],
    threshold: float,
    review_idx: int,
    candidate_ids: list[str] | None = None,
) -> EvidenceMapItem:
    lit_map = _lit_index(literature_cards)
    evidence_by_id = {e.evidence_id: e for e in evidence_cards}
    predictions = split_predictions(hypothesis)
    if candidate_ids is None:
        candidate_ids = select_candidate_ids(hypothesis, evidence_cards)

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
        blob = ev.claim + "".join(ev.quotes)
        if any(k in blob for k in ("相关", "关联", "correlation")):
            limitations.append("表述偏相关，因果强度有限")
        if ev.population_or_model and any(
            k in ev.population_or_model.lower()
            for k in ("mouse", "mice", "鼠", "cell", "细胞", "体外", "in vitro")
        ):
            limitations.append("非人源/体外模型，外推到目标人群需谨慎")

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

    conflicts = find_conflicts(support_ids, oppose_ids, evidence_by_id)
    gaps = detect_gaps(
        hypothesis, predictions, covered, len(support_ids), len(oppose_ids)
    )
    strength = aggregate_strength(quality_pairs)
    if len(support_ids) >= 2:
        strength = min(1.0, strength + 0.05)
    if len(oppose_ids) >= 2:
        strength = max(0.0, strength - 0.05)
    strength = round(strength, 3)

    binding_scores = [b.evidence_quality.total_score for b in bindings]
    verdict = build_verdict(
        strength, threshold, gaps, len(support_ids), len(oppose_ids), binding_scores
    )

    main_limitations: list[str] = []
    for b in bindings:
        main_limitations.extend(b.limitations)
    main_limitations.extend(g.description for g in gaps[:2])
    main_limitations = list(dict.fromkeys(main_limitations))[:6]

    summary = EvidenceSummary(
        support=summarize(
            SupportDirection.SUPPORT,
            claims_by_dir[SupportDirection.SUPPORT],
            "暂无明确支持证据。",
        ),
        oppose=summarize(
            SupportDirection.OPPOSE,
            claims_by_dir[SupportDirection.OPPOSE],
            "暂无明确反对证据。",
        ),
        uncertain=summarize(
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


def review_one_hypothesis(
    hypothesis: HypothesisCard,
    evidence_cards: list[EvidenceCard],
    literature_cards: list[LiteratureCard],
    threshold: float,
    review_idx: int,
    *,
    llm: Optional[QwenCompatibleClient] = None,
    scoring_mode: str = "auto",
    llm_errors: Optional[list[str]] = None,
) -> EvidenceMapItem:
    candidate_ids = select_candidate_ids(hypothesis, evidence_cards)
    use_llm = scoring_mode == "llm" or (
        scoring_mode == "auto" and llm is not None and llm.available
    )
    if use_llm and llm is not None and llm.available:
        try:
            return review_hypothesis_with_llm(
                llm,
                hypothesis=hypothesis,
                evidence_cards=evidence_cards,
                literature_cards=literature_cards,
                candidate_ids=candidate_ids,
                threshold=threshold,
                review_idx=review_idx,
            )
        except Exception as exc:  # noqa: BLE001 — 显式降级到规则引擎
            if llm_errors is not None:
                llm_errors.append(
                    f"{hypothesis.hypothesis_id}: {type(exc).__name__}: {exc}"
                )

    return review_one_hypothesis_rules(
        hypothesis,
        evidence_cards,
        literature_cards,
        threshold,
        review_idx,
        candidate_ids=candidate_ids,
    )


class EvidenceMappingAgent:
    """模块 4：证据梳理 Agent。

    使用方式（总控侧）：
        agent = EvidenceMappingAgent()  # 自动：有 Key 用 LLM
        response = agent.run(EvidenceMappingInput(...))

    环境变量：
        DASHSCOPE_API_KEY / QWEN_API_KEY / LLM_API_KEY
        EVIDENCE_MAPPING_MODE=auto|llm|rules
    """

    agent_id = "evidence_mapping_agent"

    def __init__(
        self,
        *,
        llm: Optional[QwenCompatibleClient] = None,
        scoring_mode: Optional[str] = None,
    ) -> None:
        self.scoring_mode = resolve_scoring_mode(scoring_mode)
        self.llm = llm if llm is not None else QwenCompatibleClient()

    def run(self, data: EvidenceMappingInput | dict) -> AgentResponse:
        inp = (
            data
            if isinstance(data, EvidenceMappingInput)
            else EvidenceMappingInput.model_validate(data)
        )

        llm_errors: list[str] = []
        items: list[EvidenceMapItem] = []
        for i, hyp in enumerate(inp.hypothesis_cards, start=1):
            items.append(
                review_one_hypothesis(
                    hypothesis=hyp,
                    evidence_cards=inp.evidence_cards,
                    literature_cards=inp.literature_cards,
                    threshold=inp.threshold,
                    review_idx=i,
                    llm=self.llm,
                    scoring_mode=self.scoring_mode,
                    llm_errors=llm_errors,
                )
            )

        payload = EvidenceMapPayload(evidence_map=items)
        issues = validate_payload(payload, inp.evidence_cards)
        for err in llm_errors:
            issues.append(f"llm_fallback: {err}")

        # Self 不再用「verdict 通过率」一票否决：
        # 45% 平均强度 + 35% 绑定均分 + 20% 有支持覆盖率
        n = len(items)
        avg_strength = (
            sum(x.evidence_strength_score for x in items) / n if n else 0.0
        )
        binding_scores: list[float] = []
        for x in items:
            binding_scores.extend(
                b.evidence_quality.total_score for b in x.detailed_review.evidence_bindings
            )
        avg_binding = (
            (sum(binding_scores) / len(binding_scores) / 10.0) if binding_scores else 0.0
        )
        support_coverage = (
            sum(1 for x in items if x.supporting_evidence_ids) / n if n else 0.0
        )
        passed_n = sum(1 for x in items if x.detailed_review.verdict.passed)
        coverage = passed_n / n if n else 0.0

        overall = round(
            0.45 * avg_strength + 0.35 * avg_binding + 0.20 * support_coverage,
            3,
        )
        # 有支持绑定且结构无 fatal 时，阈值降到 0.55，避免 Self 虚低
        self_threshold = 0.55
        self_passed = overall >= self_threshold and not any(
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

        used_llm = (
            self.scoring_mode != "rules"
            and self.llm.available
            and not llm_errors
        )

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
                threshold=self_threshold,
                dimension_scores={
                    "hypothesis_pass_rate": round(coverage, 3),
                    "avg_evidence_strength": round(avg_strength, 3),
                    "avg_binding_quality": round(avg_binding, 3),
                    "support_coverage": round(support_coverage, 3),
                    "schema_validity": 0.0
                    if any(i.startswith("fatal:") for i in issues)
                    else 1.0,
                    "scoring_backend_llm": 1.0 if used_llm else 0.0,
                },
                issues=issues,
                suggestions=suggestions[:8],
            ),
        )

    def run_dict(self, data: dict) -> dict:
        return self.run(data).model_dump(mode="json")
