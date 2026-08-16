from __future__ import annotations

from backend.app.review_gate import ReviewGate


def test_research_planning_string_logic_chain_is_a_review_issue_not_an_exception():
    score, issues = ReviewGate()._traceability(
        "research_planning",
        {
            "research_plan": {
                "plans": [
                    {
                        "hypothesis_id": "hyp_001",
                        "plan": {"rationale": {"logic_chain": ["ev_001"]}},
                    }
                ]
            }
        },
        {
            "evidence_cards": [{"evidence_id": "ev_001"}],
            "literature_cards": [{"literature_id": "lit_001"}],
        },
    )

    assert score == 0.0
    assert issues == ["Research plan logic_chain entries must be objects."]
