from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evidence_mapping import EvidenceMappingAgent
from evidence_mapping.models import EvidenceMappingInput


@pytest.fixture
def mock_input() -> dict:
    path = ROOT / "examples" / "mock_input.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_run_produces_evidence_map(mock_input: dict) -> None:
    resp = EvidenceMappingAgent().run(mock_input)
    assert resp.metadata.agent_id == "evidence_mapping_agent"
    assert resp.metadata.stage == "evidence_mapping"
    assert len(resp.payload.evidence_map) == 2

    hyp1 = resp.payload.evidence_map[0]
    assert hyp1.hypothesis_id == "hyp_001"
    # ev_005 应被独立改判为 oppose（模块2 hint 可能是 support）
    assert "ev_005" in hyp1.opposing_evidence_ids
    assert "ev_001" in hyp1.supporting_evidence_ids or "ev_003" in hyp1.supporting_evidence_ids
    assert hyp1.detailed_review.recheck_delta  # 至少有改判记录


def test_three_way_mutex(mock_input: dict) -> None:
    resp = EvidenceMappingAgent().run(mock_input)
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
    resp = EvidenceMappingAgent().run(EvidenceMappingInput.model_validate(data))
    assert resp.payload.evidence_map[0].needs_more_evidence is True
