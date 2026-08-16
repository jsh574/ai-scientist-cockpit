from __future__ import annotations

from planning_agent.sample_data import sample_planner_input
from planning_agent.service import run_planning_agent
from planning_agent.workflow_chain import PlanningProtocolRunner


class FakeProtocolRunner:
    def __init__(
        self,
        *,
        configured=True,
        fail_id="",
        invalid_trace_ids=False,
        wrong_identity=False,
        string_traceability=False,
    ):
        self.configured = configured
        self.fail_id = fail_id
        self.invalid_trace_ids = invalid_trace_ids
        self.wrong_identity = wrong_identity
        self.string_traceability = string_traceability
        self.calls = []

    def configuration_summary(self):
        names = (
            "draft", "review_methodology", "review_statistics",
            "review_feasibility", "synthesis", "repair",
        )
        return [
            {"name": name, "configured": self.configured, "optional": name == "repair"}
            for name in names
        ]

    def run_batch(self, data, **options):
        self.calls.append((data, options))
        runs = [self._run(data, card["hypothesis_id"]) for card in data["hypothesis_cards"]]
        return {"status": "partial_success" if self.fail_id else "success", "errors": [], "hypothesis_runs": runs}

    def _run(self, data, hypothesis_id):
        if hypothesis_id == self.fail_id:
            return {
                "hypothesis_id": hypothesis_id,
                "status": "failed",
                "next_action": "inspect_failure",
                "final_result": None,
                "errors": ["review and synthesis unavailable"],
            }
        evidence_ids = ["ev_001", "invented"] if self.invalid_trace_ids else []
        source_ids = ["lit_001", "invented"] if self.invalid_trace_ids else []
        logic_chain = ["ev_001"] if self.string_traceability else [
            {"evidence_ids": evidence_ids, "source_ids": source_ids}
        ]
        references = ["lit_001"] if self.string_traceability else [
            {"source_id": source_id} for source_id in source_ids
        ]
        result = {
            "schema_version": "experiment_planner_plan_result_v1",
            "agent_name": "ExperimentPlannerAgent",
            "task_id": data["task_id"],
            "iteration": data["iteration"],
            "hypothesis_id": hypothesis_id,
            "status": "success",
            "error_message": "",
            "plan": {
                "problem_statement": f"Protocol for {hypothesis_id}",
                "rationale": {"logic_chain": logic_chain},
                "references": references,
                "feedback_tasks": [],
            },
        }
        if self.wrong_identity:
            result.update({"task_id": "wrong", "iteration": 99, "hypothesis_id": "wrong"})
        return {
            "hypothesis_id": hypothesis_id,
            "status": "success",
            "next_action": "continue_to_product",
            "final_result": result,
            "errors": [],
        }


def test_run_planning_agent_preserves_selected_hypothesis_order_and_contract():
    data = sample_planner_input()
    runner = FakeProtocolRunner()
    response = run_planning_agent(data, workflow_runner=runner)
    assert response["metadata"]["status"] == "success"
    assert [item["hypothesis_id"] for item in response["payload"]["plans"]] == ["hyp_001", "hyp_002"]
    chain_input, options = runner.calls[0]
    assert [item["hypothesis_id"] for item in chain_input["hypothesis_cards"]] == ["hyp_001", "hyp_002"]
    assert options["max_parallel_hypotheses"] == 1
    assert options["max_parallel_calls"] == 1


def test_feedback_and_parallel_limit_reach_protocol_runner():
    data = sample_planner_input()
    data["_feedback"] = "Reduce the sample size."
    runner = FakeProtocolRunner()
    response = run_planning_agent(data, workflow_runner=runner, max_packages=1, max_parallel_calls=2)
    assert response["metadata"]["status"] == "success"
    chain_input, options = runner.calls[0]
    assert chain_input["_feedback"] == "Reduce the sample size."
    assert len(chain_input["hypothesis_cards"]) == 1
    assert options["max_parallel_calls"] == 2


def test_invalid_input_fails_without_running_protocol():
    data = sample_planner_input()
    data.pop("question_card")
    runner = FakeProtocolRunner()
    response = run_planning_agent(data, workflow_runner=runner)
    assert response["metadata"]["status"] == "failed"
    assert runner.calls == []


def test_incomplete_protocol_configuration_reports_missing_stage():
    runner = FakeProtocolRunner(configured=False)
    response = run_planning_agent(sample_planner_input(), workflow_runner=runner)
    assert response["metadata"]["status"] == "failed"
    assert "DASHSCOPE_API_KEY" in response["self_review"]["issues"][0]
    assert "synthesis" in response["self_review"]["issues"][0]


def test_one_hypothesis_failure_becomes_partial_success_with_error_item():
    response = run_planning_agent(
        sample_planner_input(), workflow_runner=FakeProtocolRunner(fail_id="hyp_002")
    )
    assert response["metadata"]["status"] == "partial_success"
    failed = response["payload"]["plans"][1]
    assert failed["status"] == "failed"
    assert "review and synthesis unavailable" in failed["error_message"]


def test_unknown_traceability_is_reported_by_service_guardrail():
    response = run_planning_agent(
        sample_planner_input(), workflow_runner=FakeProtocolRunner(invalid_trace_ids=True), max_packages=1
    )
    assert response["metadata"]["status"] == "partial_success"
    assert any("unknown source" in issue or "unknown evidence" in issue for issue in response["self_review"]["issues"])


def test_string_traceability_never_crashes_service_aggregation():
    response = run_planning_agent(
        sample_planner_input(),
        workflow_runner=FakeProtocolRunner(string_traceability=True),
        max_packages=1,
    )

    assert response["metadata"]["status"] == "partial_success"
    assert len(response["payload"]["plans"]) == 1
    assert response["payload"]["plans"][0]["plan"]["references"] == ["lit_001"]
    assert any("must be an object" in issue for issue in response["self_review"]["issues"])


def test_service_normalizes_system_identity_from_local_context():
    response = run_planning_agent(
        sample_planner_input(), workflow_runner=FakeProtocolRunner(wrong_identity=True)
    )
    assert [item["hypothesis_id"] for item in response["payload"]["plans"]] == ["hyp_001", "hyp_002"]


def test_model_policy_and_execution_handler_reach_local_runner(monkeypatch):
    runner = FakeProtocolRunner()
    captured = {}

    def fake_from_env(**kwargs):
        captured.update(kwargs)
        return runner

    monkeypatch.setattr(PlanningProtocolRunner, "from_env", fake_from_env)
    handler = lambda _stage, _event: None
    response = run_planning_agent(
        sample_planner_input(),
        model_policy={"model": "qwen-test", "thinking_enabled": True},
        execution_event_handler=handler,
    )
    assert response["metadata"]["status"] == "success"
    assert captured["model_policy"]["model"] == "qwen-test"
    assert captured["event_handler"] is handler


def test_execution_event_handler_and_compatibility_alias_are_mutually_exclusive():
    handler = lambda _stage, _event: None
    try:
        run_planning_agent(
            sample_planner_input(), workflow_event_handler=handler, execution_event_handler=handler
        )
    except ValueError as exc:
        assert "compatibility alias" in str(exc)
    else:
        raise AssertionError("both event handler names must be rejected")
