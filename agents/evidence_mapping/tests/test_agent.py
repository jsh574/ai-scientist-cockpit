from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evidence_mapping import EvidenceMappingAgent
from evidence_mapping.llm_review import parse_llm_review
from evidence_mapping.models import EvidenceMappingInput, HypothesisCard, EvidenceCard


@pytest.fixture
def mock_input() -> dict:
    path = ROOT / "examples" / "mock_input.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_run_produces_evidence_map(mock_input: dict) -> None:
    resp = EvidenceMappingAgent(scoring_mode="rules").run(mock_input)
    assert resp.metadata.agent_id == "evidence_mapping_agent"
    assert resp.metadata.stage == "evidence_mapping"
    assert len(resp.payload.evidence_map) == 2

    hyp1 = resp.payload.evidence_map[0]
    assert hyp1.hypothesis_id == "hyp_001"
    assert "ev_005" in hyp1.opposing_evidence_ids
    assert "ev_001" in hyp1.supporting_evidence_ids or "ev_003" in hyp1.supporting_evidence_ids
    assert hyp1.detailed_review.recheck_delta


def test_three_way_mutex(mock_input: dict) -> None:
    resp = EvidenceMappingAgent(scoring_mode="rules").run(mock_input)
    for item in resp.payload.evidence_map:
        s = set(item.supporting_evidence_ids)
        o = set(item.opposing_evidence_ids)
        u = set(item.uncertain_evidence_ids)
        assert not (s & o)
        assert not (s & u)
        assert not (o & u)


def test_unknown_evidence_reported() -> None:
    data = {
        "task_id": "t",
        "hypothesis_cards": [
            {
                "hypothesis_id": "hyp_x",
                "statement": "测试假设",
                "based_on_evidence_ids": ["ev_missing"],
                "expected_observation": "应观察到 X",
            }
        ],
        "evidence_cards": [],
    }
    resp = EvidenceMappingAgent(scoring_mode="rules").run(
        EvidenceMappingInput.model_validate(data)
    )
    assert resp.payload.evidence_map[0].needs_more_evidence is True


def test_no_ad_hardcode_in_scorer_source() -> None:
    scorer = (ROOT / "src" / "evidence_mapping" / "scorer.py").read_text(encoding="utf-8")
    agent = (ROOT / "src" / "evidence_mapping" / "agent.py").read_text(encoding="utf-8")
    for bad in ("阿尔茨海默", "alzheimer", "reduced tau", "promote tau", "中国", "asian"):
        assert bad.lower() not in scorer.lower()
        assert bad.lower() not in agent.lower()


def test_self_review_not_killed_by_pass_rate(mock_input: dict) -> None:
    """有支持绑定时，Self 不应被「通过率=0」打到极低。"""
    resp = EvidenceMappingAgent(scoring_mode="rules").run(mock_input)
    assert resp.self_review.overall_score >= 0.45
    assert "avg_binding_quality" in resp.self_review.dimension_scores
    assert resp.self_review.dimension_scores["support_coverage"] > 0


def test_parse_llm_allows_high_reliability_without_quotes() -> None:
    """上游无 quotes 时，不得因缺 quotes 压低 reliability。"""
    hyp = HypothesisCard(
        hypothesis_id="hyp_x",
        statement="X causes Y",
        predictions=["Y increases"],
        based_on_evidence_ids=["ev_a"],
    )
    ev = EvidenceCard(evidence_id="ev_a", claim="X supports Y", quotes=[])
    raw = {
        "bindings": [
            {
                "evidence_id": "ev_a",
                "support_direction": "support",
                "binding_type": "direct_support",
                "prediction_index": 0,
                "directness": 0.9,
                "reliability": 0.95,
                "sufficiency": 0.7,
                "applicability": 0.8,
                "total_score": 9.0,
                "recheck_note": "x",
                "limitations": ["缺少 quotes"],
            }
        ],
        "evidence_summary": {"support": "s", "oppose": "o", "uncertain": "u"},
        "gaps": [
            {
                "gap_code": "why_no_oppose",
                "description": "none",
                "suggested_evidence_type": "x",
            }
        ],
        "evidence_strength_score": 0.3,
        "main_limitations": [],
    }
    item = parse_llm_review(
        raw,
        hypothesis=hyp,
        evidence_cards=[ev],
        candidate_ids=["ev_a"],
        threshold=7.0,
        review_idx=1,
    )
    q = item.detailed_review.evidence_bindings[0].evidence_quality
    assert q.reliability == 0.95
    assert item.evidence_strength_score >= 0.42
    assert not any("quotes" in x.lower() for x in item.detailed_review.evidence_bindings[0].limitations)


def test_parse_llm_review_bindings() -> None:
    hyp = HypothesisCard(
        hypothesis_id="hyp_x",
        statement="X 通过 Y 导致 Z",
        predictions=["Y 升高先于 Z"],
        based_on_evidence_ids=["ev_a"],
    )
    ev = EvidenceCard(
        evidence_id="ev_a",
        claim="证据支持 X 促进 Y",
        quotes=["supports the claim"],
        support_direction_hint="oppose",
    )
    raw = {
        "bindings": [
            {
                "evidence_id": "ev_a",
                "support_direction": "support",
                "binding_type": "direct_support",
                "prediction_index": 0,
                "prediction_text": "Y 升高先于 Z",
                "directness": 0.9,
                "reliability": 0.85,
                "sufficiency": 0.7,
                "applicability": 0.8,
                "total_score": 8.2,
                "recheck_note": "LLM 改判为 support",
                "limitations": [],
            }
        ],
        "evidence_summary": {
            "support": "支持侧：证据支持 X 促进 Y",
            "oppose": "暂无明确反对证据。",
            "uncertain": "暂无不确定类证据。",
        },
        "gaps": [
            {
                "gap_code": "why_no_oppose",
                "description": "未发现反对证据",
                "suggested_evidence_type": "contradictory_or_null_result",
            }
        ],
        "evidence_strength_score": 0.72,
        "main_limitations": ["需更多因果证据"],
    }
    item = parse_llm_review(
        raw,
        hypothesis=hyp,
        evidence_cards=[ev],
        candidate_ids=["ev_a"],
        threshold=7.0,
        review_idx=1,
    )
    assert item.supporting_evidence_ids == ["ev_a"]
    assert item.evidence_strength_score >= 0.42
    assert item.detailed_review.recheck_delta
    assert item.detailed_review.evidence_bindings[0].evidence_quality.total_score >= 7.0
