"""四维证据质量评分与方向重判（规则引擎兜底，领域无关）。

权重（数据规范 8.5）：
- 直接性 30%
- 可靠性 25%
- 充分性 25%
- 适用性 20%

说明：生产路径优先走 LLM（见 llm_review.py）；本模块在无 API Key /
强制 rules 模式 / LLM 失败时作为 fallback。关键词表仅保留跨领域通用极性词，
不含特定病例示例用语。
"""

from __future__ import annotations

import re
from typing import Optional

from .models import (
    BindingType,
    EvidenceCard,
    EvidenceQuality,
    HypothesisCard,
    LiteratureCard,
    SupportDirection,
)

WEIGHTS = {
    "directness": 0.30,
    "reliability": 0.25,
    "sufficiency": 0.25,
    "applicability": 0.20,
}

OPPOSE_HINTS = (
    "反对",
    "否定",
    "相反",
    "不支持",
    "未能证实",
    "结果而非原因",
    "而非原因",
    "混杂",
    "不足以确立",
    "缺少直接证明",
    "因果证据不足",
    "contradict",
    "oppose",
    "against",
    "not support",
    "does not support",
    "no evidence",
    "consequence rather",
    "rather than a cause",
    "refute",
    "falsif",
)

SUPPORT_HINTS = (
    "研究结果支持",
    "证据支持",
    "证实了",
    "表明了",
    "显著促进",
    "显著提高",
    "显著降低",
    "可减缓",
    "associated with",
    "indicate that",
    "supports the",
    "consistent with",
    "demonstrated that",
    "加速",
    "减缓",
)

CAUSAL_WEAK = (
    "相关",
    "关联",
    "correlation",
    "associated",
    "观察性",
    "observational",
)

# 过泛变量：过短或常见占位词，筛选候选时降权（非领域绑定）
GENERIC_VARIABLE_STOP = {
    "effect",
    "outcome",
    "result",
    "disease",
    "患者",
    "结果",
    "效应",
    "影响",
    "研究",
    "data",
    "analysis",
}


def split_predictions(hypothesis: HypothesisCard) -> list[str]:
    if hypothesis.predictions:
        return [p.strip() for p in hypothesis.predictions if p.strip()]
    text = (hypothesis.expected_observation or "").strip()
    if not text:
        return [hypothesis.statement]
    parts = re.split(r"[；;。\n]+", text)
    parts = [p.strip(" ，,") for p in parts if p.strip()]
    return parts or [hypothesis.statement]


def specific_target_variables(hypothesis: HypothesisCard) -> list[str]:
    """过滤过泛变量，保留更有区分度的目标变量。"""
    out: list[str] = []
    for v in hypothesis.target_variables:
        key = v.strip()
        if not key:
            continue
        if key.lower() in GENERIC_VARIABLE_STOP or key in GENERIC_VARIABLE_STOP:
            continue
        if len(key) <= 1:
            continue
        out.append(key)
    return out or list(hypothesis.target_variables)


def _tokens(text: str) -> set[str]:
    """中英混合粗分词：英文词 + 中文连续串再切二元组，避免整句成一个 token。"""
    text = text.lower()
    out: set[str] = set()
    for eng in re.findall(r"[a-z0-9_]+", text):
        if len(eng) > 1:
            out.add(eng)
    for zh in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(zh) <= 2:
            out.add(zh)
        else:
            out.add(zh)
            out.update(zh[i : i + 2] for i in range(len(zh) - 1))
            out.update(zh[i : i + 3] for i in range(len(zh) - 2))
    return out


def _overlap_score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    al, bl = a.lower(), b.lower()
    if len(b) <= 12 and b.lower() in al:
        return 1.0
    if len(a) <= 12 and a.lower() in bl:
        return 1.0
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _hit_any(text: str, keywords: tuple[str, ...]) -> bool:
    t = text.lower()
    return any(k.lower() in t for k in keywords)


def match_prediction(
    evidence: EvidenceCard, predictions: list[str]
) -> tuple[Optional[int], Optional[str], float]:
    if not predictions:
        return None, None, 0.0
    best_i, best_s = 0, -1.0
    blob = " ".join([evidence.claim, *evidence.quotes, *evidence.related_concepts])
    for i, pred in enumerate(predictions):
        s = _overlap_score(blob, pred)
        if s > best_s:
            best_i, best_s = i, s
    return best_i, predictions[best_i], best_s


def recheck_direction(
    evidence: EvidenceCard,
    hypothesis: HypothesisCard,
    pred_score: float,
) -> tuple[SupportDirection, BindingType, str]:
    """独立重判方向，不信任模块 2 预分类。"""
    text = " ".join([evidence.claim, *evidence.quotes])
    hyp = hypothesis.statement

    oppose_hit = _hit_any(text, OPPOSE_HINTS)
    support_hit = _hit_any(text, SUPPORT_HINTS)
    if _hit_any(text, ("不支持", "反对", "rather than a cause", "consequence rather", "refute")):
        oppose_hit = True
        support_hit = False

    var_overlap = 0.0
    vars_ = specific_target_variables(hypothesis)
    if vars_:
        var_overlap = max(
            (_overlap_score(evidence.claim, v) for v in vars_),
            default=0.0,
        )
        var_overlap = max(
            var_overlap,
            max(
                (
                    _overlap_score(" ".join(evidence.related_concepts), v)
                    for v in vars_
                ),
                default=0.0,
            ),
        )
    concept_overlap = max(
        (_overlap_score(evidence.claim, c) for c in evidence.related_concepts),
        default=0.0,
    )
    topical = max(pred_score, var_overlap, concept_overlap, _overlap_score(text, hyp))

    if topical < 0.12 and not oppose_hit and not support_hit:
        return (
            SupportDirection.IRRELEVANT,
            BindingType.IRRELEVANT,
            "与假设核心预测重叠极低，判为无关",
        )

    if oppose_hit:
        btype = (
            BindingType.DIRECT_OPPOSE if topical >= 0.25 else BindingType.INDIRECT_OPPOSE
        )
        return SupportDirection.OPPOSE, btype, "文本含反对/否定信号，独立改判为 oppose"

    if support_hit:
        btype = (
            BindingType.DIRECT_SUPPORT
            if topical >= 0.25
            else BindingType.INDIRECT_SUPPORT
        )
        return SupportDirection.SUPPORT, btype, "文本含支持信号，独立改判为 support"

    if topical >= 0.22:
        if _hit_any(text, CAUSAL_WEAK) or _hit_any(
            text, ("缺少", "不足", "有限", "limited", "unclear")
        ):
            return (
                SupportDirection.UNCERTAIN,
                BindingType.UNCERTAIN,
                "仅见相关性/证据不足表述，判为 uncertain",
            )
        return (
            SupportDirection.SUPPORT,
            BindingType.INDIRECT_SUPPORT,
            "主题相关但极性弱，暂记为间接支持",
        )

    return (
        SupportDirection.UNCERTAIN,
        BindingType.UNCERTAIN,
        "极性不清且主题关联有限，判为 uncertain",
    )


def score_quality(
    evidence: EvidenceCard,
    literature: Optional[LiteratureCard],
    direction: SupportDirection,
    binding_type: BindingType,
    pred_score: float,
) -> EvidenceQuality:
    if binding_type in (BindingType.DIRECT_SUPPORT, BindingType.DIRECT_OPPOSE):
        directness = 0.85 + 0.15 * min(pred_score, 1.0)
    elif binding_type in (BindingType.INDIRECT_SUPPORT, BindingType.INDIRECT_OPPOSE):
        directness = 0.45 + 0.35 * min(pred_score, 1.0)
    elif direction == SupportDirection.UNCERTAIN:
        directness = 0.35
    else:
        directness = 0.1

    # 上游模块 2 当前不产出 quotes，可靠性不按 quotes 奖惩，改看文献可追溯性/类型/样本量
    reliability = 0.5
    if literature:
        if literature.doi or literature.url:
            reliability += 0.2
        if literature.literature_type in {"meta_analysis", "rct", "systematic_review"}:
            reliability += 0.15
        elif literature.literature_type in {"review", "cohort"}:
            reliability += 0.08
        reliability = max(reliability, literature.relevance_score * 0.5)
    if evidence.sample_size:
        if evidence.sample_size >= 500:
            reliability += 0.1
        elif evidence.sample_size >= 100:
            reliability += 0.05
    if evidence.method_note or evidence.claim:
        reliability += 0.1
    reliability = min(max(reliability, 0.0), 1.0)

    sufficiency = 0.45 + 0.2 * evidence.confidence
    if evidence.method_note:
        sufficiency += 0.1
    sufficiency = min(sufficiency, 1.0)

    # 领域无关适用性：人源/临床 vs 动物/体外，不做国家/病例偏好
    applicability = 0.55
    if evidence.population_or_model:
        pm = evidence.population_or_model.lower()
        if any(
            k in pm
            for k in (
                "human",
                "patient",
                "cohort",
                "clinical",
                "患者",
                "临床",
                "队列",
                "participant",
            )
        ):
            applicability = 0.75
        elif any(
            k in pm
            for k in (
                "mouse",
                "mice",
                "rat",
                "鼠",
                "cell",
                "细胞",
                "体外",
                "in vitro",
                "organoid",
            )
        ):
            applicability = 0.45
        else:
            applicability = 0.55
    if literature and literature.relevance_score:
        applicability = 0.6 * applicability + 0.4 * literature.relevance_score

    directness = min(max(directness, 0.0), 1.0)
    applicability = min(max(applicability, 0.0), 1.0)

    total = 10.0 * (
        WEIGHTS["directness"] * directness
        + WEIGHTS["reliability"] * reliability
        + WEIGHTS["sufficiency"] * sufficiency
        + WEIGHTS["applicability"] * applicability
    )
    return EvidenceQuality(
        directness=round(directness, 3),
        reliability=round(reliability, 3),
        sufficiency=round(sufficiency, 3),
        applicability=round(applicability, 3),
        total_score=round(total, 2),
    )


def aggregate_strength(
    bindings: list[tuple[SupportDirection, EvidenceQuality]],
) -> float:
    """综合 strength：支持加分、反对减分、不确定弱影响。"""
    if not bindings:
        return 0.0
    score = 0.55
    support_n = 0
    oppose_n = 0
    for direction, quality in bindings:
        w = quality.total_score / 10.0
        if direction == SupportDirection.SUPPORT:
            score += 0.22 * w
            support_n += 1
        elif direction == SupportDirection.OPPOSE:
            score -= 0.18 * w
            oppose_n += 1
        elif direction == SupportDirection.UNCERTAIN:
            score -= 0.03 * w
    # 有支持、反对不多时给温和下限，避免「已绑定仍只有 0.3x」
    if support_n >= 1 and oppose_n <= support_n:
        score = max(score, 0.42 + 0.04 * min(support_n, 3))
    return round(min(max(score, 0.0), 1.0), 3)
