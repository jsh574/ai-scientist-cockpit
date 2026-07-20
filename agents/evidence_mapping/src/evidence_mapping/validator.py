"""输出校验：供本模块自检，也可给总控 Review Gate 参考。"""

from __future__ import annotations

from .models import EvidenceCard, EvidenceMapPayload, SupportDirection


def validate_payload(
    payload: EvidenceMapPayload, evidence_cards: list[EvidenceCard]
) -> list[str]:
    issues: list[str] = []
    known = {e.evidence_id for e in evidence_cards}

    if not payload.evidence_map:
        issues.append("fatal: evidence_map 为空")
        return issues

    for item in payload.evidence_map:
        buckets = [
            ("supporting", item.supporting_evidence_ids),
            ("opposing", item.opposing_evidence_ids),
            ("uncertain", item.uncertain_evidence_ids),
        ]
        seen_in_item: dict[str, str] = {}
        for name, ids in buckets:
            for eid in ids:
                if eid not in known:
                    issues.append(f"fatal: {item.hypothesis_id} 引用未知证据 {eid}")
                if eid in seen_in_item:
                    issues.append(
                        f"{item.hypothesis_id}: 证据 {eid} 同时出现在 "
                        f"{seen_in_item[eid]} 与 {name}，三类应互斥"
                    )
                else:
                    seen_in_item[eid] = name

        # bindings 与三类列表一致性
        binding_ids = {b.evidence_id for b in item.detailed_review.evidence_bindings}
        listed = set(item.supporting_evidence_ids) | set(
            item.opposing_evidence_ids
        ) | set(item.uncertain_evidence_ids)
        if listed - binding_ids:
            issues.append(
                f"{item.hypothesis_id}: 分类列表中的证据未出现在 evidence_bindings"
            )

        for b in item.detailed_review.evidence_bindings:
            if b.support_direction == SupportDirection.SUPPORT and (
                b.evidence_id not in item.supporting_evidence_ids
            ):
                issues.append(
                    f"{item.hypothesis_id}: binding {b.evidence_id} 方向为 support 但未写入 supporting 列表"
                )

        if (
            not item.opposing_evidence_ids
            and "why_no_oppose"
            not in {g.gap_code for g in item.detailed_review.gaps}
        ):
            issues.append(
                f"{item.hypothesis_id}: 无反对证据时必须在 gaps 中说明 why_no_oppose"
            )

    return issues