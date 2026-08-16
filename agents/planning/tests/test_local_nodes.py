from __future__ import annotations

from pathlib import Path

from planning_agent.adapter import build_hypothesis_evidence_packages
from planning_agent.local_nodes import (
    compact_for_synthesis,
    compile_planning_brief,
    guard_final_plan,
    guard_protocol_draft,
    guard_protocol_review,
    merge_protocol_reviews,
    planning_brief_issues,
)
from planning_agent.sample_data import sample_planner_input


def _brief():
    data = sample_planner_input()
    package = build_hypothesis_evidence_packages(data)[0]
    return compile_planning_brief(
        data["question_card"],
        package["hypothesis_id"],
        package,
        data.get("planning_constraints", {}),
        data.get("user_constraints", {}),
        "Keep the sample bounded.",
    )


def _plan():
    return {
        "problem_statement": "Test the fixed hypothesis.",
        "rationale": {
            "text": "Grounded rationale",
            "logic_chain": [
                {"claim": "claim", "evidence_ids": ["ev_001"], "source_ids": ["lit_001"]}
            ],
        },
        "technical_details": {"required_methods": ["controlled analysis"]},
        "datasets": [{"name": "upstream-described data", "access_status": "verify"}],
        "paper_title": "Protocol",
        "paper_abstract": "Planned study; no experiment has been run.",
        "methods": [{"step_id": "1", "name": "Prepare", "description": "Prepare inputs"}],
        "experiments": [{"objective": "Test", "metrics": ["metric"]}],
        "results": {"expected_findings": ["Expected pattern"]},
        "references": [{"source_id": "lit_001", "title": "Source"}],
        "feedback_tasks": [],
        "limitations": [],
    }


def test_compile_brief_preserves_upstream_boundary_and_detailed_review():
    data = sample_planner_input()
    data["evidence_map"][0]["detailed_review"] = {
        "conflict_pairs": [{"evidence_id_a": "ev_001", "evidence_id_b": "ev_003"}],
        "gaps": [{"gap_code": "g1", "description": "Need stratification"}],
        "verdict": {"passed": True},
    }
    package = build_hypothesis_evidence_packages(data)[0]
    brief = compile_planning_brief(
        data["question_card"], package["hypothesis_id"], package
    )

    assert planning_brief_issues(brief) == []
    assert brief["boundary"]["out_of_scope"]
    assert brief["upstream_evidence"]["detailed_review"]["conflict_pairs"]
    assert brief["guardrails"]["allowed_evidence_ids"] == ["ev_001", "ev_002", "ev_003"]


def test_brief_without_bound_evidence_stops_before_model_calls():
    brief = _brief()
    brief["upstream_evidence"]["evidence_rows"] = []
    assert "no usable upstream evidence" in planning_brief_issues(brief)[0]


def test_context_compression_never_drops_identity_or_allowlists():
    brief = _brief()
    brief["upstream_evidence"]["detailed_review"] = {"large": "x" * 20000}
    compacted = compact_for_synthesis(brief, 4000)
    assert compacted["hypothesis"]["hypothesis_id"] == "hyp_001"
    assert compacted["guardrails"]["allowed_evidence_ids"]
    assert compacted["guardrails"]["allowed_source_ids"]


def test_draft_guard_normalizes_identity_shape_and_removes_unknown_ids():
    brief = _brief()
    plan = _plan()
    plan["rationale"]["logic_chain"][0]["evidence_ids"].append("invented")
    payload = {"schema_version": "wrong", "hypothesis_id": "wrong", "plan": plan}

    draft, report = guard_protocol_draft(payload, brief, "hyp_001")

    assert report["passed"] is True
    assert draft["schema_version"] == "planning_protocol_draft_v1"
    assert draft["hypothesis_id"] == "hyp_001"
    assert draft["protocol"]["rationale"]["logic_chain"][0]["evidence_ids"] == ["ev_001"]
    assert draft["status"] == "partial_success"


def test_draft_guard_soft_normalizes_common_object_wrappers():
    plan = _plan()
    plan["datasets"] = {"data_requirements": [{"description": "bounded data"}]}
    plan["methods"] = {"overall_design": "controlled", "steps": [{"name": "step"}]}
    plan["experiments"] = {
        "main_experiment": {"objective": "test"},
        "metrics": ["primary metric"],
    }
    plan["references"] = [{"literature_id": "lit_001", "title": "Source"}]
    draft, report = guard_protocol_draft(
        {"protocol": plan}, _brief(), "hyp_001"
    )
    assert report["passed"] is True
    assert draft["protocol"]["datasets"]["source"][0]["description"] == "bounded data"
    assert draft["protocol"]["methods"]["steps"][0]["name"] == "step"
    assert draft["protocol"]["experiments"]["metrics"] == [
        {"name": "primary metric", "description": ""}
    ]
    assert draft["protocol"]["references"][0]["source_id"] == "lit_001"


def test_review_guard_scopes_identity_and_traceability():
    review, report = guard_protocol_review(
        {
            "verdict": "revise",
            "summary": "One issue",
            "strengths": ["bounded"],
            "issues": [
                {
                    "severity": "major",
                    "category": "analysis",
                    "description": "Specify estimand",
                    "required_change": "Add an estimand",
                    "evidence_ids": ["ev_001", "invented"],
                    "source_ids": ["lit_001", "invented"],
                }
            ],
        },
        _brief(),
        "hyp_001",
        "statistics",
    )

    assert report["passed"] is True
    assert review["review_role"] == "statistics"
    assert review["issues"][0]["issue_id"] == "statistics_1"
    assert review["issues"][0]["evidence_ids"] == ["ev_001"]
    assert review["issues"][0]["source_ids"] == ["lit_001"]


def test_review_merge_is_stable_severity_first_and_deduplicated():
    shared = {
        "issue_id": "i1",
        "severity": "major",
        "category": "controls",
        "description": "Add a negative control",
        "required_change": "Add a negative control",
        "evidence_ids": [],
        "source_ids": [],
    }
    merged = merge_protocol_reviews(
        [
            {"review_role": "methodology", "verdict": "revise", "summary": "", "strengths": [], "issues": [shared]},
            {"review_role": "statistics", "verdict": "revise", "summary": "", "strengths": [], "issues": [{**shared, "issue_id": "i2"}]},
            {"review_role": "feasibility", "verdict": "revise", "summary": "", "strengths": [], "issues": [{**shared, "issue_id": "i3", "severity": "critical", "description": "Data unavailable"}]},
        ],
        {"statistics": "format failure"},
    )

    assert [item["severity"] for item in merged["required_changes"]] == ["critical", "major"]
    assert merged["failed_roles"] == ["statistics"]


def test_final_guard_accepts_complete_grounded_plan():
    result, report = guard_final_plan(
        {"status": "success", "plan": _plan()}, _brief(), "task_demo_001", 1, "hyp_001"
    )
    assert report["passed"] is True
    assert result["schema_version"] == "experiment_planner_plan_result_v1"
    assert result["agent_name"] == "ExperimentPlannerAgent"


def test_final_guard_normalizes_string_traceability_shorthand():
    plan = _plan()
    plan["references"] = ["lit_001"]
    plan["rationale"]["logic_chain"] = ["ev_001", "Expected relationship"]

    result, report = guard_final_plan(
        {"plan": plan}, _brief(), "task_demo_001", 1, "hyp_001"
    )

    assert report["passed"] is True
    assert result["plan"]["references"][0]["source_id"] == "lit_001"
    assert result["plan"]["references"][0]["title"]
    assert result["plan"]["rationale"]["logic_chain"][0]["evidence_ids"] == ["ev_001"]
    assert result["plan"]["rationale"]["logic_chain"][1]["claim"] == "Expected relationship"


def test_final_guard_rejects_unresolvable_string_reference_without_crashing():
    plan = _plan()
    plan["references"] = ["not-an-upstream-source"]

    result, report = guard_final_plan(
        {"plan": plan}, _brief(), "task_demo_001", 1, "hyp_001"
    )

    assert result["status"] == "failed"
    assert report["repairable"] is False
    assert any("unknown source_ids" in issue for issue in report["issues"])


def test_final_guard_canonicalizes_real_qwen_alias_shape_without_losing_content():
    plan = _plan()
    plan["technical_details"] = {
        "methods_required": ["collocation analysis"],
        "candidate_algorithms": ["GAM", "LMM"],
        "statistical_tests": ["TOST"],
        "software_environment": "Python 3.9+ with xarray and statsmodels",
        "reproducibility_settings": "Fixed seed and locked dependencies",
    }
    plan["datasets"] = [
        {"name": "Satellite product", "description": "Input observations"},
        {"name": "Validation cohort", "role": "target", "fields": ["outcome"]},
    ]
    plan["methods"] = [
        {"step_id": "M1", "name": "Prepare", "inputs": ["raw"], "outputs": ["clean"]}
    ]
    plan["experiments"] = [
        {
            "objective": "Primary comparison",
            "variables": {
                "independent": "treatment",
                "dependent": "outcome",
                "control": ["age"],
            },
            "baselines": "Local null baseline",
            "metrics": ["RMSD", {"name": "Bias", "description": "Mean error"}],
            "procedure": "Run the primary protocol",
            "ablation_or_sensitivity": "Vary the matching window",
            "stopping_or_falsification": "Stop when the confidence interval crosses zero",
        },
        {
            "objective": "Secondary attribution",
            "variables": {"independent": ["driver"], "dependent": "residual"},
            "baselines": "Null attribution model",
            "metrics": "Adjusted R2",
            "procedure": "Fit the secondary model",
        },
    ]
    plan["references"] = [{"literature_id": "lit_001", "used_for": ["methods"]}]
    plan["feedback_tasks"] = ["Confirm data availability"]

    result, report = guard_final_plan(
        {"plan": plan}, _brief(), "task_demo_001", 1, "hyp_001"
    )
    canonical = result["plan"]

    assert report["passed"] is True
    assert canonical["technical_details"]["required_methods"] == ["collocation analysis"]
    assert canonical["technical_details"]["candidate_models_or_algorithms"] == ["GAM", "LMM"]
    assert canonical["technical_details"]["software_stack"] == [
        "Python 3.9+ with xarray and statsmodels"
    ]
    assert "methods_required" not in canonical["technical_details"]
    assert "software_environment" not in canonical["technical_details"]
    assert canonical["datasets"]["source"][0]["name"] == "Satellite product"
    assert canonical["datasets"]["target"][0]["required_fields"] == ["outcome"]
    assert canonical["methods"]["steps"][0]["inputs"] == ["raw"]
    assert len(canonical["experiments"]["items"]) == 2
    assert canonical["experiments"]["main_experiment"]["independent_variables"] == [
        "treatment"
    ]
    assert [item["name"] for item in canonical["experiments"]["baselines"]] == [
        "Local null baseline",
        "Null attribution model",
    ]
    assert canonical["references"][0]["title"]
    assert canonical["feedback_tasks"][0]["objective"] == "Confirm data availability"
    assert set(canonical["feedback_tasks"][0]) == {
        "task_id",
        "task_type",
        "priority",
        "objective",
        "input_requirements",
        "expected_output",
    }


def test_final_unknown_traceability_is_nonrepairable():
    plan = _plan()
    plan["references"].append({"source_id": "invented"})
    result, report = guard_final_plan(
        {"plan": plan}, _brief(), "task_demo_001", 1, "hyp_001"
    )
    assert result["status"] == "failed"
    assert report["repairable"] is False
    assert any("unknown source_ids" in issue for issue in report["issues"])


def test_final_missing_protocol_section_is_repairable():
    plan = _plan()
    plan["methods"] = []
    _, report = guard_final_plan({"plan": plan}, _brief(), "task_demo_001", 1, "hyp_001")
    assert report["passed"] is False
    assert report["repairable"] is True


def test_dataset_url_is_rejected_when_not_supplied_upstream():
    plan = _plan()
    plan["datasets"][0]["url"] = "https://invented.example/data"
    _, report = guard_final_plan({"plan": plan}, _brief(), "task_demo_001", 1, "hyp_001")
    assert report["repairable"] is False
    assert any("dataset URLs" in issue for issue in report["issues"])


def test_local_runtime_does_not_execute_dynamic_code():
    root = Path("planning_agent")
    runtime = "\n".join(
        (root / name).read_text(encoding="utf-8")
        for name in ("local_nodes.py", "stage_clients.py", "workflow_chain.py")
    )
    assert "exec(" not in runtime
    assert "eval(" not in runtime
