import json

from planning_agent.sample_data import sample_planner_input
from planning_agent.service import run_planning_agent


class FakeSingleHypothesisDifyClient:
    configured = True

    def __init__(self):
        self.calls = []

    def run_workflow(self, inputs):
        self.calls.append(inputs)
        package = json.loads(inputs["hypothesis_evidence_package"])
        source_ids = [source["literature_id"] for source in package["source_literature"]]
        evidence_ids = [
            evidence["evidence_id"]
            for evidence in package["evidence_subset"]["supporting_evidence"]
        ]
        return {
            "schema_version": "experiment_planner_plan_result_v1",
            "agent_name": "ExperimentPlannerAgent",
            "task_id": inputs["task_id"],
            "iteration": inputs["iteration"],
            "hypothesis_id": package["hypothesis_id"],
            "status": "success",
            "error_message": "",
            "plan": {
                "problem_statement": package["hypothesis"],
                "rationale": {
                    "text": package["rationale"],
                    "logic_chain": [
                        {
                            "step": 1,
                            "claim": package["hypothesis"],
                            "evidence_ids": evidence_ids,
                            "source_ids": source_ids,
                        }
                    ],
                },
                "technical_details": {
                    "required_methods": ["public dataset analysis"],
                    "candidate_models_or_algorithms": ["regression"],
                    "statistical_tests": ["correlation"],
                    "software_stack": ["Python"],
                },
                "datasets": {"source": [], "target": []},
                "paper_title": "demo",
                "paper_abstract": "demo",
                "methods": {"overall_design": "demo", "steps": []},
                "experiments": {
                    "main_experiment": {
                        "objective": package["expected_observation"],
                        "independent_variables": [],
                        "dependent_variables": [],
                        "control_variables": [],
                    },
                    "baselines": [],
                    "metrics": [],
                    "procedure": [],
                    "ablation_or_sensitivity_analysis": [],
                },
                "results": {
                    "result_type": "expected_or_feasibility_result",
                    "expected_findings": [],
                    "feasibility_check": package["validation_idea"],
                    "falsification_criteria": [],
                },
                "references": [
                    {
                        "source_id": source["literature_id"],
                        "title": source["title"],
                        "authors": source["authors"],
                        "year": str(source["year"]),
                        "doi": source["doi"],
                        "url": source["url"],
                        "used_for": ["rationale"],
                    }
                    for source in package["source_literature"]
                ],
                "feedback_tasks": [
                    {
                        "task_id": f"fb_{package['hypothesis_id']}",
                        "task_type": "literature_supplement",
                        "priority": "high",
                        "objective": "补充证据",
                        "input_requirements": [package["hypothesis_id"]],
                        "expected_output": "补充证据卡片",
                    }
                ],
                "limitations": package["limitations"],
            },
        }


class UnconfiguredDifyClient:
    configured = False

    def run_workflow(self, inputs):
        raise AssertionError("unconfigured client should not be called")


def test_run_planning_agent_calls_dify_once_per_selected_hypothesis_and_aggregates_plans():
    data = sample_planner_input()
    client = FakeSingleHypothesisDifyClient()

    response = run_planning_agent(data, dify_client=client)

    assert response["metadata"]["task_id"] == data["task_id"]
    assert response["metadata"]["agent_id"] == "research_planning_agent"
    assert response["metadata"]["stage"] == "research_planning"
    assert response["metadata"]["status"] == "success"
    assert response["self_review"]["passed"] is True
    payload = response["payload"]
    assert payload["schema_version"] == "experiment_planner_output_v1"
    assert payload["task_id"] == data["task_id"]
    assert payload["iteration"] == data["iteration"]
    assert [plan["hypothesis_id"] for plan in payload["plans"]] == ["hyp_001", "hyp_002"]
    assert [json.loads(call["hypothesis_evidence_package"])["hypothesis_id"] for call in client.calls] == [
        "hyp_001",
        "hyp_002",
    ]
    assert all("hypothesis_evidence_packages" not in call for call in client.calls)


def test_generated_plan_uses_only_input_references_and_evidence_ids():
    data = sample_planner_input()
    valid_literature_ids = {item["literature_id"] for item in data["literature_cards"]}
    valid_evidence_ids = {item["evidence_id"] for item in data["evidence_cards"]}

    response = run_planning_agent(data, dify_client=FakeSingleHypothesisDifyClient())
    first_plan = response["payload"]["plans"][0]["plan"]

    assert {ref["source_id"] for ref in first_plan["references"]} <= valid_literature_ids
    logic_evidence_ids = {
        evidence_id
        for step in first_plan["rationale"]["logic_chain"]
        for evidence_id in step["evidence_ids"]
    }
    assert logic_evidence_ids <= valid_evidence_ids


def test_needs_more_evidence_becomes_feedback_task():
    data = sample_planner_input()

    response = run_planning_agent(data, dify_client=FakeSingleHypothesisDifyClient())
    first_plan = response["payload"]["plans"][0]["plan"]

    assert any(
        task["task_type"] == "literature_supplement" for task in first_plan["feedback_tasks"]
    )


def test_validation_failure_returns_failed_response():
    data = sample_planner_input()
    data.pop("question_card")

    response = run_planning_agent(data)

    assert response["metadata"]["status"] == "failed"
    assert response["payload"]["status"] == "failed"
    assert response["self_review"]["passed"] is False
    assert response["self_review"]["issues"]


def test_dify_not_configured_returns_failed_response():
    data = sample_planner_input()

    response = run_planning_agent(data, dify_client=UnconfiguredDifyClient())

    assert response["metadata"]["status"] == "failed"
    assert response["payload"]["status"] == "failed"
    assert "Dify workflow is not configured" in response["self_review"]["issues"][0]


class RecordingDifyClient(FakeSingleHypothesisDifyClient):
    pass


def test_run_planning_agent_can_call_selected_hypotheses_in_parallel_and_keep_order():
    data = sample_planner_input()
    client = RecordingDifyClient()

    response = run_planning_agent(data, dify_client=client, max_parallel_calls=2)

    assert response["metadata"]["status"] == "success"
    assert [plan["hypothesis_id"] for plan in response["payload"]["plans"]] == [
        "hyp_001",
        "hyp_002",
    ]
    assert len(client.calls) == 2
