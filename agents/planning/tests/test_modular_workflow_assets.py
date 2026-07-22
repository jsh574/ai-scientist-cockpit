from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

ACTIVE_WORKFLOWS = (
    "dify/Planning Design Candidate Generator.yml",
    "dify/Planning Design Judge Selector.yml",
    "dify/Research Planning Agent.yml",
)


def _workflow(path: str) -> dict[str, Any]:
    parsed = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _nodes(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(node["id"]): node["data"]
        for node in workflow["workflow"]["graph"]["nodes"]
    }


def _node(workflow: dict[str, Any], node_id: str) -> dict[str, Any]:
    return _nodes(workflow)[node_id]


def _variable(node: dict[str, Any], variable: str) -> dict[str, Any]:
    return next(item for item in node["variables"] if item["variable"] == variable)


def _code_main(node: dict[str, Any]):
    namespace: dict[str, Any] = {}
    exec(compile(node["code"], "<dify-code-node>", "exec"), namespace)
    return namespace["main"]


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def _valid_review(candidate_id: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "hard_gate_passed": True,
        "scores": {
            "hypothesis_alignment": 0.8,
            "evidence_traceability": 0.8,
            "method_and_statistics": 0.8,
            "data_feasibility": 0.8,
            "falsifiability": 0.8,
            "resource_and_risk": 0.8,
            "iteration_readiness": 0.8,
        },
        "strengths": ["well scoped"],
        "issues": [],
        "decision_reason": "Valid candidate.",
    }


def test_candidate_guard_normalizes_system_owned_identity():
    workflow = _workflow(ACTIVE_WORKFLOWS[0])
    guard = _node(workflow, "candidate_guard")
    payload = _variable(guard, "candidate_payload")

    assert payload["value_selector"] == ["design_candidate_llm", "structured_output"]
    assert all(item["variable"] != "candidate_text" for item in guard["variables"])

    candidate = {
        "schema_version": "design_candidate_v1",
        "candidate_id": "invented",
        "hypothesis_id": "wrong",
        "variant_mode": "wrong",
        "status": "success",
        "rationale": {"evidence_ids": [], "source_ids": []},
        "method_steps": [{"step_id": 1}],
        "falsification_matrix": [{"scenario": "test"}],
    }
    context = {
        "guardrails": {"allowed_evidence_ids": [], "allowed_source_ids": []}
    }
    result = _code_main(guard)(
        candidate,
        json.dumps(context),
        "hyp_001",
        "minimum_viable",
    )
    normalized = json.loads(result["design_candidate"])
    report = json.loads(result["guardrail_report"])

    assert normalized["hypothesis_id"] == "hyp_001"
    assert normalized["variant_mode"] == "minimum_viable"
    assert normalized["candidate_id"] == "hyp_001::minimum_viable"
    assert report["passed"] is True
    assert set(report["normalized_identity_fields"]) == {
        "candidate_id",
        "hypothesis_id",
        "variant_mode",
    }


def test_selection_context_and_guard_own_routing_identity():
    workflow = _workflow(ACTIVE_WORKFLOWS[1])
    prepare = _node(workflow, "prepare_selection")
    guard = _node(workflow, "selection_guard")

    assert _variable(prepare, "task_id")["value_selector"] == ["start", "task_id"]
    assert _variable(prepare, "iteration")["value_selector"] == ["start", "iteration"]
    assert _variable(guard, "selection_payload")["value_selector"] == [
        "judge_selector",
        "structured_output",
    ]
    assert all(item["variable"] != "selection_text" for item in guard["variables"])

    candidate = {
        "candidate_id": "hyp_001::minimum_viable",
        "hypothesis_id": "hyp_001",
        "variant_mode": "minimum_viable",
        "status": "success",
    }
    prepared = _code_main(prepare)(
        "task_001",
        2,
        "hyp_001",
        json.dumps([candidate]),
        "{}",
        "{}",
        "{}",
    )
    context = json.loads(prepared["selection_context"])
    assert context["task_id"] == "task_001"
    assert context["iteration"] == 2

    selection = {
        "schema_version": "design_selection_v1",
        "task_id": "invented",
        "iteration": 99,
        "hypothesis_id": "wrong",
        "decision": "accept",
        "selected_candidate_id": candidate["candidate_id"],
        "candidate_reviews": [_valid_review(candidate["candidate_id"])],
        "limitations": [],
    }
    result = _code_main(guard)(
        selection,
        prepared["selection_context"],
        "task_001",
        2,
        "hyp_001",
    )
    normalized = json.loads(result["design_selection"])
    report = json.loads(result["selection_guardrail_report"])

    assert normalized["task_id"] == "task_001"
    assert normalized["iteration"] == 2
    assert normalized["hypothesis_id"] == "hyp_001"
    assert normalized["selected_design"] == candidate
    assert report["passed"] is True
    assert set(report["normalized_identity_fields"]) == {
        "task_id",
        "iteration",
        "hypothesis_id",
    }


def test_selection_guard_rejects_unknown_candidate_review_reference():
    workflow = _workflow(ACTIVE_WORKFLOWS[1])
    guard = _node(workflow, "selection_guard")
    candidate = {
        "candidate_id": "hyp_001::minimum_viable",
        "status": "success",
    }
    context = json.dumps({"candidates": [candidate]})
    selection = {
        "schema_version": "design_selection_v1",
        "decision": "accept",
        "selected_candidate_id": candidate["candidate_id"],
        "candidate_reviews": [_valid_review("invented")],
        "limitations": [],
    }

    result = _code_main(guard)(selection, context, "task_001", 1, "hyp_001")
    report = json.loads(result["selection_guardrail_report"])
    normalized = json.loads(result["design_selection"])

    assert report["passed"] is False
    assert normalized["decision"] == "failed"
    assert any("unknown candidate_id" in issue for issue in report["issues"])
    assert any("missing candidate_id" in issue for issue in report["issues"])


def test_workflow_c_uses_structured_outputs_and_normalizes_final_identity():
    workflow = _workflow(ACTIVE_WORKFLOWS[2])
    nodes = _nodes(workflow)
    full_plan = nodes["full_plan"]
    final_contract = nodes["final_contract"]
    end = nodes["end"]

    assert sum(node.get("type") == "llm" for node in nodes.values()) == 1
    assert full_plan["model"]["completion_params"]["enable_thinking"] is False
    assert _variable(final_contract, "plan_payload")["value_selector"] == [
        "full_plan",
        "structured_output",
    ]
    assert _variable(final_contract, "normalized_evidence_context")["value_selector"] == [
        "normalize_evidence",
        "normalized_evidence_context",
    ]
    assert {
        item["variable"]: item["value_selector"] for item in end["outputs"]
    } == {
        "plan_result": ["final_contract", "plan_result"],
        "contract_report": ["final_contract", "contract_report"],
    }

    result = _code_main(final_contract)(
        {
            "schema_version": "wrong",
            "agent_name": "wrong",
            "task_id": "wrong",
            "iteration": 99,
            "hypothesis_id": "wrong",
            "status": "success",
            "error_message": "",
            "plan": {},
        },
        json.dumps({"guardrails": {"allowed_evidence_ids": [], "allowed_source_ids": []}}),
        "task_001",
        2,
        "hyp_001",
    )
    normalized = json.loads(result["plan_result"])
    report = json.loads(result["contract_report"])

    assert normalized["schema_version"] == "experiment_planner_plan_result_v1"
    assert normalized["agent_name"] == "ExperimentPlannerAgent"
    assert normalized["task_id"] == "task_001"
    assert normalized["iteration"] == 2
    assert normalized["hypothesis_id"] == "hyp_001"
    assert report["passed"] is True
    assert set(report["normalized_identity_fields"]) == {
        "schema_version",
        "agent_name",
        "task_id",
        "iteration",
        "hypothesis_id",
    }


def test_workflow_c_final_contract_rejects_unknown_evidence_and_source_ids():
    workflow = _workflow(ACTIVE_WORKFLOWS[2])
    final_contract = _node(workflow, "final_contract")
    plan_payload = {
        "schema_version": "experiment_planner_plan_result_v1",
        "agent_name": "ExperimentPlannerAgent",
        "task_id": "task_001",
        "iteration": 1,
        "hypothesis_id": "hyp_001",
        "status": "success",
        "error_message": "",
        "plan": {
            "rationale": {
                "logic_chain": [
                    {
                        "step": 1,
                        "claim": "test",
                        "evidence_ids": ["ev_allowed", "ev_invented"],
                        "source_ids": ["lit_allowed"],
                    }
                ]
            },
            "references": [
                {
                    "source_id": "lit_invented",
                    "title": "Invented",
                }
            ],
        },
    }
    context = json.dumps(
        {
            "guardrails": {
                "allowed_evidence_ids": ["ev_allowed"],
                "allowed_source_ids": ["lit_allowed"],
            }
        }
    )

    result = _code_main(final_contract)(
        plan_payload, context, "task_001", 1, "hyp_001"
    )
    normalized = json.loads(result["plan_result"])
    report = json.loads(result["contract_report"])

    assert normalized["status"] == "failed"
    assert report["passed"] is False
    assert report["unknown_evidence_ids"] == ["ev_invented"]
    assert report["unknown_source_ids"] == ["lit_invented"]


def test_thinking_structured_llms_have_no_text_consumers():
    reference_pattern = re.compile(r"\{\{#([^.}]+)\.text#\}\}")

    for path in ACTIVE_WORKFLOWS:
        workflow = _workflow(path)
        nodes = _nodes(workflow)
        structured_thinking_ids = {
            node_id
            for node_id, data in nodes.items()
            if data.get("type") == "llm"
            and data.get("structured_output_enabled") is True
            and data.get("model", {})
            .get("completion_params", {})
            .get("enable_thinking")
            is True
        }

        for consumer_id, data in nodes.items():
            for variable in data.get("variables", []):
                selector = variable.get("value_selector", [])
                if selector and selector[0] in structured_thinking_ids:
                    assert selector[1] == "structured_output", (
                        path,
                        consumer_id,
                        selector,
                    )
            for text in _strings(data.get("prompt_template", [])):
                unsafe = structured_thinking_ids & set(reference_pattern.findall(text))
                assert not unsafe, (path, consumer_id, sorted(unsafe))


def test_all_active_workflow_edges_reference_existing_nodes_and_code_compiles():
    for path in ACTIVE_WORKFLOWS:
        workflow = _workflow(path)
        nodes = _nodes(workflow)
        for edge in workflow["workflow"]["graph"]["edges"]:
            assert str(edge["source"]) in nodes
            assert str(edge["target"]) in nodes
        for node_id, data in nodes.items():
            if data.get("type") == "code":
                compile(data["code"], f"{path}:{node_id}", "exec")

def test_selection_schema_fully_constrains_reviews_and_scores():
    workflow = _workflow(ACTIVE_WORKFLOWS[1])
    judge = _node(workflow, "judge_selector")
    schema = judge["structured_output"]["schema"]
    review = schema["properties"]["candidate_reviews"]["items"]
    scores = review["properties"]["scores"]
    expected_scores = {
        "hypothesis_alignment",
        "evidence_traceability",
        "method_and_statistics",
        "data_feasibility",
        "falsifiability",
        "resource_and_risk",
        "iteration_readiness",
    }

    assert review["additionalProperties"] is False
    assert set(review["required"]) == {
        "candidate_id",
        "hard_gate_passed",
        "scores",
        "strengths",
        "issues",
        "decision_reason",
    }
    assert scores["additionalProperties"] is False
    assert set(scores["properties"]) == expected_scores
    assert set(scores["required"]) == expected_scores
    assert all(item["minimum"] == 0 for item in scores["properties"].values())
    assert all(item["maximum"] == 1 for item in scores["properties"].values())
