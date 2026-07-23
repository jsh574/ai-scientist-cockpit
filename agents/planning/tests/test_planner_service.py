from planning_agent.sample_data import sample_planner_input
from planning_agent.service import run_planning_agent


class FakeWorkflowChainRunner:
    def __init__(
        self,
        *,
        decision: str = "accept",
        configured_stages: tuple[str, ...] = ("A", "B", "C"),
        invalid_trace_ids: bool = False,
        wrong_identity: bool = False,
    ) -> None:
        self.decision = decision
        self.configured_stages = configured_stages
        self.invalid_trace_ids = invalid_trace_ids
        self.wrong_identity = wrong_identity
        self.calls = []

    def configuration_summary(self):
        return [
            {
                "name": f"workflow_{stage.lower()}",
                "configured": stage in self.configured_stages,
            }
            for stage in "ABC"
        ]

    def run_batch(self, data, **options):
        self.calls.append((data, options))
        runs = [self._run_for_hypothesis(data, card) for card in data["hypothesis_cards"]]
        status = "success" if self.decision == "accept" else "requires_action"
        return {"status": status, "errors": [], "hypothesis_runs": runs}

    def _run_for_hypothesis(self, data, card):
        hypothesis_id = card["hypothesis_id"]
        if self.decision != "accept":
            return {
                "hypothesis_id": hypothesis_id,
                "status": "requires_action",
                "decision": self.decision,
                "next_action": "request_upstream_feedback",
                "final_result": None,
                "errors": [],
            }
        evidence_ids = ["ev_001", "ev_invented"] if self.invalid_trace_ids else []
        references = (
            [
                {"source_id": "lit_001", "used_for": ["rationale"]},
                {"source_id": "lit_invented", "used_for": ["rationale"]},
            ]
            if self.invalid_trace_ids
            else []
        )
        result = {
            "schema_version": "experiment_planner_plan_result_v1",
            "agent_name": "ExperimentPlannerAgent",
            "task_id": data["task_id"],
            "iteration": data["iteration"],
            "hypothesis_id": hypothesis_id,
            "status": "success",
            "error_message": "",
            "plan": {
                "problem_statement": f"ABC plan for {hypothesis_id}",
                "rationale": {
                    "logic_chain": [
                        {
                            "claim": "Test claim",
                            "evidence_ids": evidence_ids,
                            "source_ids": [item["source_id"] for item in references],
                        }
                    ]
                },
                "references": references,
                "feedback_tasks": [],
            },
        }
        if self.wrong_identity:
            result.update(
                {
                    "schema_version": "wrong",
                    "agent_name": "wrong",
                    "task_id": "wrong",
                    "iteration": 99,
                    "hypothesis_id": "wrong",
                }
            )
        return {
            "hypothesis_id": hypothesis_id,
            "status": "success",
            "decision": "accept",
            "next_action": "continue_to_product",
            "final_result": result,
            "errors": [],
        }


def test_run_planning_agent_runs_complete_chain_for_selected_hypotheses():
    data = sample_planner_input()
    runner = FakeWorkflowChainRunner()

    response = run_planning_agent(data, workflow_runner=runner)

    assert response["metadata"]["task_id"] == data["task_id"]
    assert response["metadata"]["status"] == "success"
    assert response["self_review"]["passed"] is True
    assert [plan["hypothesis_id"] for plan in response["payload"]["plans"]] == [
        "hyp_001",
        "hyp_002",
    ]
    chain_input, options = runner.calls[0]
    assert [card["hypothesis_id"] for card in chain_input["hypothesis_cards"]] == [
        "hyp_001",
        "hyp_002",
    ]
    assert options["max_parallel_hypotheses"] == 1


def test_run_planning_agent_passes_feedback_and_execution_options_to_chain():
    data = sample_planner_input()
    data["_feedback"] = "Reduce the sample size for this revision."
    runner = FakeWorkflowChainRunner()

    response = run_planning_agent(
        data,
        workflow_runner=runner,
        max_packages=1,
        max_parallel_calls=2,
    )

    assert response["metadata"]["status"] == "success"
    chain_input, options = runner.calls[0]
    assert chain_input["_feedback"] == data["_feedback"]
    assert len(chain_input["hypothesis_cards"]) == 1
    assert options["max_parallel_hypotheses"] == 2


def test_validation_failure_returns_failed_response_without_running_chain():
    data = sample_planner_input()
    data.pop("question_card")
    runner = FakeWorkflowChainRunner()

    response = run_planning_agent(data, workflow_runner=runner)

    assert response["metadata"]["status"] == "failed"
    assert response["payload"]["status"] == "failed"
    assert response["self_review"]["passed"] is False
    assert runner.calls == []


def test_incomplete_abc_configuration_returns_failed_response():
    runner = FakeWorkflowChainRunner(configured_stages=("A", "B"))

    response = run_planning_agent(sample_planner_input(), workflow_runner=runner)

    assert response["metadata"]["status"] == "failed"
    assert "DIFY_WORKFLOW_C_API_KEY" in response["self_review"]["issues"][0]
    assert "workflow_c" in response["self_review"]["issues"][0]
    assert runner.calls == []


def test_feedback_required_is_exposed_as_failed_plan_item():
    runner = FakeWorkflowChainRunner(decision="feedback_required")

    response = run_planning_agent(sample_planner_input(), workflow_runner=runner, max_packages=1)

    assert response["metadata"]["status"] == "failed"
    assert "decision=feedback_required" in response["payload"]["plans"][0]["error_message"]


def test_chain_output_reports_unknown_traceability_ids():
    runner = FakeWorkflowChainRunner(invalid_trace_ids=True)

    response = run_planning_agent(sample_planner_input(), workflow_runner=runner, max_packages=1)

    assert response["metadata"]["status"] == "partial_success"
    assert response["self_review"]["passed"] is False
    assert any(
        "unknown source" in issue or "unknown evidence" in issue
        for issue in response["self_review"]["issues"]
    )


def test_service_normalizes_system_identity_from_local_context():
    runner = FakeWorkflowChainRunner(wrong_identity=True)

    response = run_planning_agent(sample_planner_input(), workflow_runner=runner)

    assert response["metadata"]["status"] == "success"
    assert [plan["hypothesis_id"] for plan in response["payload"]["plans"]] == [
        "hyp_001",
        "hyp_002",
    ]
