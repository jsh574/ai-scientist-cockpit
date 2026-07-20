"""基于 LLM 的假设级证据评审（方向重判 + 四维打分）。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .agent_helpers import build_verdict, find_conflicts
from .llm import LLMClient
from .models import (
    BindingType,
    DetailedReview,
    EvidenceBinding,
    EvidenceCard,
    EvidenceMapItem,
    EvidenceQuality,
    EvidenceSummary,
    GapItem,
    HypothesisCard,
    LiteratureCard,
    SupportDirection,
)
from .prompts import REVIEW_SCHEMA, REVIEW_SYSTEM_PROMPT
from .scorer import aggregate_strength, split_predictions


_DIR_MAP = {d.value: d for d in SupportDirection}
_BIND_MAP = {b.value: b for b in BindingType}


def _clamp01(x: Any, default: float = 0.5) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, v))


def _clamp10(x: Any, default: float = 5.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(10.0, v))


def _weighted_total(d: float, r: float, s: float, a: float) -> float:
    return round(10.0 * (0.30 * d + 0.25 * r + 0.25 * s + 0.20 * a), 2)


def build_llm_payload(
    hypothesis: HypothesisCard,
    evidence_cards: list[EvidenceCard],
    literature_cards: list[LiteratureCard],
    candidate_ids: list[str],
    threshold: float,
) -> dict[str, Any]:
    lit_map = {c.literature_id: c for c in literature_cards}
    evidence_by_id = {e.evidence_id: e for e in evidence_cards}
    predictions = split_predictions(hypothesis)

    evidences = []
    for eid in candidate_ids:
        ev = evidence_by_id.get(eid)
        if not ev:
            continue
        lit = lit_map.get(ev.literature_id) if ev.literature_id else None
        item: dict[str, Any] = {
            "evidence_id": ev.evidence_id,
            "claim": ev.claim,
            "quotes": ev.quotes,
            "related_concepts": ev.related_concepts,
            "population_or_model": ev.population_or_model,
            "method_note": ev.method_note,
            "sample_size": ev.sample_size,
            "confidence": ev.confidence,
            "support_direction_hint": (
                ev.support_direction_hint.value if ev.support_direction_hint else None
            ),
            "source_type": ev.source_type,
        }
        if lit:
            item["literature"] = {
                "literature_id": lit.literature_id,
                "title": lit.title,
                "literature_type": lit.literature_type,
                "doi": lit.doi,
                "url": lit.url,
                "relevance_score": lit.relevance_score,
                "main_findings": lit.main_findings[:3],
            }
        evidences.append(item)

    return {
        "threshold": threshold,
        "hypothesis": {
            "hypothesis_id": hypothesis.hypothesis_id,
            "statement": hypothesis.statement,
            "rationale": hypothesis.rationale,
            "target_variables": hypothesis.target_variables,
            "predictions": predictions,
            "based_on_evidence_ids": hypothesis.based_on_evidence_ids,
        },
        "evidences": evidences,
        "instructions": (
            "对 evidences 中每条证据输出一条 binding；"
            "无关请标 irrelevant；"
            "prediction_index 对应 hypothesis.predictions 下标，无法对应可为 null。"
        ),
    }


def parse_llm_review(
    raw: dict[str, Any],
    *,
    hypothesis: HypothesisCard,
    evidence_cards: list[EvidenceCard],
    candidate_ids: list[str],
    threshold: float,
    review_idx: int,
) -> EvidenceMapItem:
    evidence_by_id = {e.evidence_id: e for e in evidence_cards}
    predictions = split_predictions(hypothesis)
    raw_bindings = raw.get("bindings") or []
    if not isinstance(raw_bindings, list):
        raw_bindings = []

    bindings: list[EvidenceBinding] = []
    recheck_delta: list[dict] = []
    by_dir: dict[SupportDirection, list[str]] = defaultdict(list)
    claims_by_dir: dict[SupportDirection, list[str]] = defaultdict(list)
    quality_pairs: list[tuple[SupportDirection, EvidenceQuality]] = []
    seen: set[str] = set()

    for row in raw_bindings:
        if not isinstance(row, dict):
            continue
        eid = str(row.get("evidence_id") or "")
        if not eid or eid not in evidence_by_id or eid in seen:
            continue
        if candidate_ids and eid not in candidate_ids:
            continue
        seen.add(eid)
        ev = evidence_by_id[eid]

        direction = _DIR_MAP.get(
            str(row.get("support_direction", "")).lower(), SupportDirection.UNCERTAIN
        )
        binding_type = _BIND_MAP.get(
            str(row.get("binding_type", "")).lower(),
            BindingType.UNCERTAIN,
        )
        if direction == SupportDirection.IRRELEVANT:
            continue

        d = _clamp01(row.get("directness"), 0.5)
        r = _clamp01(row.get("reliability"), 0.5)
        s = _clamp01(row.get("sufficiency"), 0.5)
        a = _clamp01(row.get("applicability"), 0.5)
        expected = _weighted_total(d, r, s, a)
        total = row.get("total_score")
        total_f = _clamp10(total, expected) if total is not None else expected
        if abs(total_f - expected) > 2.5:
            total_f = expected

        pred_i = row.get("prediction_index")
        if isinstance(pred_i, int) and 0 <= pred_i < len(predictions):
            pred_text = row.get("prediction_text") or predictions[pred_i]
        else:
            pred_i = None
            pred_text = row.get("prediction_text")

        limitations = row.get("limitations") or []
        if not isinstance(limitations, list):
            limitations = []
        limitations = [
            str(x)
            for x in limitations
            if str(x).strip() and "quotes" not in str(x).lower()
        ]

        note = str(row.get("recheck_note") or "LLM 独立重判")
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

        quality = EvidenceQuality(
            directness=round(d, 3),
            reliability=round(r, 3),
            sufficiency=round(s, 3),
            applicability=round(a, 3),
            total_score=round(total_f, 2),
        )
        binding = EvidenceBinding(
            evidence_id=eid,
            binding_type=binding_type,
            support_direction=direction,
            prediction_index=pred_i,
            prediction_text=str(pred_text) if pred_text else None,
            evidence_quality=quality,
            supporting_quotes=ev.quotes[:3],
            contradictory_quotes=[],
            limitations=limitations[:6],
            recheck_note=note,
        )
        bindings.append(binding)
        by_dir[direction].append(eid)
        claims_by_dir[direction].append(ev.claim)
        quality_pairs.append((direction, quality))

    support_ids = by_dir[SupportDirection.SUPPORT]
    oppose_ids = by_dir[SupportDirection.OPPOSE]
    uncertain_ids = by_dir[SupportDirection.UNCERTAIN]

    summary_raw = raw.get("evidence_summary") or {}
    if not isinstance(summary_raw, dict):
        summary_raw = {}

    def _side(key: SupportDirection, empty: str) -> str:
        text = summary_raw.get(key.value)
        if isinstance(text, str) and text.strip():
            return text.strip()
        claims = claims_by_dir[key][:2]
        if not claims:
            return empty
        prefix = {
            "support": "支持侧：",
            "oppose": "反对侧：",
            "uncertain": "不确定侧：",
        }[key.value]
        return prefix + "；".join(claims)

    summary = EvidenceSummary(
        support=_side(SupportDirection.SUPPORT, "暂无明确支持证据。"),
        oppose=_side(SupportDirection.OPPOSE, "暂无明确反对证据。"),
        uncertain=_side(SupportDirection.UNCERTAIN, "暂无不确定类证据。"),
    )

    gaps: list[GapItem] = []
    for g in raw.get("gaps") or []:
        if not isinstance(g, dict):
            continue
        code = str(g.get("gap_code") or "").strip()
        desc = str(g.get("description") or "").strip()
        if not code or not desc:
            continue
        pi = g.get("prediction_index")
        gaps.append(
            GapItem(
                gap_code=code,
                prediction_index=pi if isinstance(pi, int) else None,
                description=desc,
                suggested_evidence_type=(
                    str(g["suggested_evidence_type"])
                    if g.get("suggested_evidence_type") is not None
                    else None
                ),
            )
        )
    if not oppose_ids and not any(g.gap_code == "why_no_oppose" for g in gaps):
        gaps.append(
            GapItem(
                gap_code="why_no_oppose",
                description="未发现明确反对证据；需确认是全面检索后仍无，还是检索不足",
                suggested_evidence_type="contradictory_or_null_result",
            )
        )
    if not support_ids and not any(g.gap_code == "missing_support" for g in gaps):
        gaps.append(
            GapItem(
                gap_code="missing_support",
                description="当前无支持证据",
                suggested_evidence_type="primary_study",
            )
        )

    strength_raw = raw.get("evidence_strength_score")
    rule_strength = aggregate_strength(quality_pairs)
    if len(support_ids) >= 2:
        rule_strength = min(1.0, rule_strength + 0.05)
    if len(oppose_ids) >= 2:
        rule_strength = max(0.0, rule_strength - 0.05)
    if strength_raw is not None:
        llm_strength = _clamp01(strength_raw, 0.5)
        # 与规则强度折中，避免 LLM 系统性压分
        strength = round(0.55 * llm_strength + 0.45 * rule_strength, 3)
        if support_ids and len(oppose_ids) <= len(support_ids):
            strength = max(strength, 0.42)
    else:
        strength = rule_strength

    binding_scores = [b.evidence_quality.total_score for b in bindings]
    verdict = build_verdict(
        strength, threshold, gaps, len(support_ids), len(oppose_ids), binding_scores
    )

    main_limitations = raw.get("main_limitations") or []
    if not isinstance(main_limitations, list):
        main_limitations = []
    main_limitations = [str(x) for x in main_limitations if str(x).strip()]
    for b in bindings:
        main_limitations.extend(b.limitations)
    main_limitations.extend(g.description for g in gaps[:2])
    main_limitations = list(dict.fromkeys(main_limitations))[:6]

    needs_more = (not verdict.passed) or any(
        g.gap_code.startswith("prediction_uncovered") or g.gap_code == "missing_causal_link"
        for g in gaps
    )

    conflicts = find_conflicts(support_ids, oppose_ids, evidence_by_id)

    return EvidenceMapItem(
        hypothesis_id=hypothesis.hypothesis_id,
        supporting_evidence_ids=support_ids,
        opposing_evidence_ids=oppose_ids,
        uncertain_evidence_ids=uncertain_ids,
        evidence_summary=summary,
        evidence_strength_score=round(strength, 3),
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


def review_hypothesis_with_llm(
    llm: LLMClient,
    *,
    hypothesis: HypothesisCard,
    evidence_cards: list[EvidenceCard],
    literature_cards: list[LiteratureCard],
    candidate_ids: list[str],
    threshold: float,
    review_idx: int,
) -> EvidenceMapItem:
    payload = build_llm_payload(
        hypothesis, evidence_cards, literature_cards, candidate_ids, threshold
    )
    raw = llm.generate_json(
        system_prompt=REVIEW_SYSTEM_PROMPT,
        user_payload=payload,
        expected_schema=REVIEW_SCHEMA,
    )
    return parse_llm_review(
        raw,
        hypothesis=hypothesis,
        evidence_cards=evidence_cards,
        candidate_ids=candidate_ids,
        threshold=threshold,
        review_idx=review_idx,
    )
