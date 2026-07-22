import json

from planning_agent.adapter import (
    build_dify_workflow_inputs,
    build_hypothesis_evidence_packages,
    select_top_packages,
    validate_planner_input,
)
from planning_agent.sample_data import sample_planner_input, short_sample_planner_input


def test_builds_evidence_packages_from_module_outputs():
    data = sample_planner_input()

    packages = build_hypothesis_evidence_packages(data)

    assert [package["hypothesis_id"] for package in packages] == ["hyp_001", "hyp_002"]
    first = packages[0]
    assert first["hypothesis"] == data["hypothesis_cards"][0]["statement"]
    assert [e["evidence_id"] for e in first["evidence_subset"]["supporting_evidence"]] == [
        "ev_001",
        "ev_002",
    ]
    assert [e["evidence_id"] for e in first["evidence_subset"]["opposing_evidence"]] == [
        "ev_003"
    ]
    assert [source["literature_id"] for source in first["source_literature"]] == [
        "lit_001",
        "lit_002",
    ]
    assert [gap["gap_id"] for gap in first["knowledge_gaps"]] == ["gap_001"]
    assert first["needs_more_evidence"] is True


def test_selects_top_packages_by_relevance_testability_strength_and_risk():
    data = sample_planner_input()
    packages = build_hypothesis_evidence_packages(data)

    selected = select_top_packages(packages, max_packages=1)

    assert len(selected) == 1
    assert selected[0]["hypothesis_id"] == "hyp_001"


def test_validate_planner_input_reports_missing_required_fields():
    data = sample_planner_input()
    data.pop("evidence_cards")

    errors = validate_planner_input(data)

    assert "Missing required field: evidence_cards" in errors


def test_validate_planner_input_enforces_protocol_version_and_request_mode():
    data = sample_planner_input()
    data["schema_version"] = "legacy_planner_input"
    data["request_mode"] = "interactive"

    errors = validate_planner_input(data)

    assert "Field must equal experiment_planner_input_v1: schema_version" in errors
    assert "Field must be single or batch: request_mode" in errors


def test_build_dify_workflow_inputs_serializes_one_package_for_one_dify_run():
    data = sample_planner_input()
    package = build_hypothesis_evidence_packages(data)[0]

    inputs = build_dify_workflow_inputs(data, package)

    assert inputs["task_id"] == data["task_id"]
    assert inputs["iteration"] == data["iteration"]
    assert inputs["hypothesis_id"] == "hyp_001"
    assert isinstance(inputs["question_card"], str)
    assert isinstance(inputs["hypothesis_evidence_package"], str)
    parsed_package = json.loads(inputs["hypothesis_evidence_package"])
    assert parsed_package["hypothesis_id"] == "hyp_001"
    assert "hypothesis_evidence_packages" not in inputs


def test_short_sample_keeps_two_hypotheses_with_smaller_payload():
    full_data = sample_planner_input()
    short_data = short_sample_planner_input()

    assert len(short_data["hypothesis_cards"]) == 2
    assert len(short_data["evidence_cards"]) == 2
    assert len(short_data["literature_cards"]) == 2
    assert len(json.dumps(short_data, ensure_ascii=False)) < len(json.dumps(full_data, ensure_ascii=False))
    assert validate_planner_input(short_data) == []
