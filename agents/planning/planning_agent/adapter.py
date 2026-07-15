from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


REQUIRED_INPUT_FIELDS = (
    "task_id",
    "iteration",
    "question_card",
    "hypothesis_cards",
    "evidence_map",
    "literature_cards",
    "evidence_cards",
)


def validate_planner_input(data: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in REQUIRED_INPUT_FIELDS:
        if field not in data:
            errors.append(f"Missing required field: {field}")

    list_fields = (
        "hypothesis_cards",
        "evidence_map",
        "literature_cards",
        "evidence_cards",
    )
    for field in list_fields:
        if field in data and not isinstance(data[field], list):
            errors.append(f"Field must be a list: {field}")

    if "question_card" in data and not isinstance(data["question_card"], Mapping):
        errors.append("Field must be an object: question_card")

    return errors


def build_hypothesis_evidence_packages(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    evidence_by_id = _index_by(data.get("evidence_cards", []), "evidence_id")
    literature_by_id = _index_by(data.get("literature_cards", []), "literature_id")
    gaps_by_id = _index_by(data.get("knowledge_gaps", []), "gap_id")
    map_by_hypothesis_id = _index_by(data.get("evidence_map", []), "hypothesis_id")

    packages: list[dict[str, Any]] = []
    for hypothesis in data.get("hypothesis_cards", []):
        hypothesis_id = hypothesis.get("hypothesis_id", "")
        evidence_map = map_by_hypothesis_id.get(hypothesis_id, {})
        supporting = _lookup_many(
            evidence_by_id,
            evidence_map.get("supporting_evidence_ids")
            or hypothesis.get("based_on_evidence_ids", []),
        )
        opposing = _lookup_many(evidence_by_id, evidence_map.get("opposing_evidence_ids", []))
        uncertain = _lookup_many(
            evidence_by_id, evidence_map.get("uncertain_evidence_ids", [])
        )
        all_evidence = supporting + opposing + uncertain
        literature_ids = [
            evidence.get("source_literature_id")
            for evidence in all_evidence
            if evidence.get("source_literature_id")
        ]
        source_literature = _lookup_many(literature_by_id, _unique(literature_ids))
        knowledge_gaps = _lookup_many(gaps_by_id, hypothesis.get("related_gap_ids", []))

        packages.append(
            {
                "hypothesis_id": hypothesis_id,
                "hypothesis": hypothesis.get("statement", ""),
                "rationale": hypothesis.get("rationale", ""),
                "target_variables": hypothesis.get("target_variables", []),
                "expected_observation": hypothesis.get("expected_observation", ""),
                "validation_idea": hypothesis.get("validation_idea", ""),
                "scores": {
                    "initial_scores": hypothesis.get("initial_scores", {}),
                    "evidence_strength_score": evidence_map.get(
                        "evidence_strength_score", 0.0
                    ),
                    "selection_score": _selection_score(
                        hypothesis.get("initial_scores", {}),
                        evidence_map.get("evidence_strength_score", 0.0),
                    ),
                },
                "evidence_subset": {
                    "supporting_evidence": supporting,
                    "opposing_evidence": opposing,
                    "uncertain_evidence": uncertain,
                },
                "source_literature": source_literature,
                "knowledge_gaps": knowledge_gaps,
                "limitations": evidence_map.get("main_limitations", []),
                "needs_more_evidence": bool(evidence_map.get("needs_more_evidence")),
                "evidence_summary": evidence_map.get("evidence_summary", {}),
            }
        )
    return packages


def select_top_packages(
    packages: list[dict[str, Any]], max_packages: int = 3
) -> list[dict[str, Any]]:
    return sorted(
        packages,
        key=lambda package: package.get("scores", {}).get("selection_score", 0.0),
        reverse=True,
    )[: max(1, max_packages)]


def build_dify_workflow_inputs(
    data: Mapping[str, Any], package: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "task_id": data.get("task_id"),
        "iteration": data.get("iteration", 1),
        "hypothesis_id": package.get("hypothesis_id", ""),
        "question_card": _json_text(data.get("question_card", {})),
        "hypothesis_evidence_package": _json_text(package),
        "planning_constraints": _json_text(data.get("planning_constraints", {})),
        "user_constraints": _json_text(data.get("user_constraints", {})),
    }


def _index_by(items: Any, key: str) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        return {}
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        if isinstance(item, Mapping) and item.get(key):
            indexed[str(item[key])] = dict(item)
    return indexed


def _lookup_many(index: dict[str, dict[str, Any]], ids: Any) -> list[dict[str, Any]]:
    if not isinstance(ids, list):
        return []
    return [index[item_id] for item_id in ids if item_id in index]


def _selection_score(initial_scores: Mapping[str, Any], evidence_strength: Any) -> float:
    testability = _as_float(initial_scores.get("testability"))
    relevance = _as_float(initial_scores.get("relevance"))
    novelty = _as_float(initial_scores.get("novelty"))
    risk = _as_float(initial_scores.get("risk"))
    strength = _as_float(evidence_strength)
    return (0.3 * testability) + (0.3 * relevance) + (0.25 * strength) + (
        0.15 * novelty
    ) - (0.2 * risk)


def _as_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _unique(items: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = str(item)
        if key not in seen:
            result.append(key)
            seen.add(key)
    return result


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
