from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

from planning_agent.sample_data import short_sample_planner_input
from planning_agent.workflow_api import (
    GenericDifyWorkflowClient,
    WorkflowEndpointConfig,
    WorkflowRunResult,
    decode_json_output,
)
from planning_agent.workflow_chain import DEFAULT_VARIANTS, PlanningWorkflowChainRunner
from planning_agent.workflow_chain_report import render_html_report


class FakeWorkflowClient:
    configured = True

    def __init__(self, name: str, handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.name = name
        self.handler = handler
        self.calls: list[dict[str, Any]] = []
        self.event_contexts: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def run(
        self, inputs: dict[str, Any], event_context: dict[str, Any] | None = None
    ) -> WorkflowRunResult:
        with self._lock:
            self.calls.append(inputs)
            self.event_contexts.append(dict(event_context or {}))
            call_number = len(self.calls)
        return WorkflowRunResult(
            workflow=self.name,
            workflow_run_id=f"{self.name}-run-{call_number}",
            task_id=f"{self.name}-task-{call_number}",
            status="succeeded",
            elapsed_time=0.1,
            total_tokens=10,
            outputs=self.handler(inputs),
        )


class FakeHTTPResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class FakeStreamingHTTPResponse:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.lines = [
            f"data: {json.dumps(event)}\n".encode("utf-8") for event in events
        ]

    def __enter__(self) -> FakeStreamingHTTPResponse:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def __iter__(self):
        return iter(self.lines)


def _candidate_outputs(inputs: dict[str, Any]) -> dict[str, Any]:
    hypothesis_id = inputs["hypothesis_id"]
    variant = inputs["variant_mode"]
    candidate = {
        "schema_version": "design_candidate_v1",
        "candidate_id": f"{hypothesis_id}::{variant}",
        "hypothesis_id": hypothesis_id,
        "variant_mode": variant,
        "status": "success",
        "planning_objective": f"Test objective for {variant}",
        "design_type": "controlled_analysis",
    }
    return {
        "design_candidate": candidate,
        "guardrail_report": {"passed": True, "issues": []},
    }


def _accept_outputs(inputs: dict[str, Any]) -> dict[str, Any]:
    candidates = json.loads(inputs["design_candidates"])
    selected = candidates[0]
    selection = {
        "schema_version": "design_selection_v1",
        "task_id": inputs["task_id"],
        "iteration": inputs["iteration"],
        "hypothesis_id": inputs["hypothesis_id"],
        "decision": "accept",
        "selected_candidate_id": selected["candidate_id"],
        "candidate_reviews": [],
        "selected_design": selected,
        "revision_instruction": "",
        "feedback_tasks": [],
        "meta_review": {},
        "limitations": [],
    }
    return {
        "design_selection": selection,
        "selected_design": selected,
        "selection_guardrail_report": {"passed": True, "issues": []},
    }


def _plan_outputs(inputs: dict[str, Any]) -> dict[str, Any]:
    constraints = json.loads(inputs["planning_constraints"])
    return {
        "plan_result": {
            "schema_version": "experiment_planner_plan_result_v1",
            "hypothesis_id": inputs["hypothesis_id"],
            "selected_candidate_id": constraints["selected_design"]["candidate_id"],
            "plan": {"title": "Final test plan"},
        }
    }


def test_endpoint_config_builds_dify_endpoint_without_double_v1():
    root = WorkflowEndpointConfig("a", "https://dify.example", "key")
    versioned = WorkflowEndpointConfig("b", "https://dify.example/v1", "key")

    assert root.endpoint == "https://dify.example/v1/workflows/run"
    assert versioned.endpoint == "https://dify.example/v1/workflows/run"


def test_decode_json_output_removes_fenced_json_but_keeps_plain_text():
    assert decode_json_output('```json\n{"passed": true}\n```') == {"passed": True}
    assert decode_json_output("ordinary output") == "ordinary output"


def test_generic_client_preserves_all_end_outputs(monkeypatch):
    response = {
        "workflow_run_id": "run-a-1",
        "task_id": "task-a-1",
        "data": {
            "status": "succeeded",
            "elapsed_time": 1.25,
            "total_tokens": 42,
            "outputs": {
                "design_candidate": '{"candidate_id":"hyp::minimum"}',
                "guardrail_report": '{"passed":true}',
            },
        },
    }
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda _request, timeout: FakeHTTPResponse(response)
    )
    client = GenericDifyWorkflowClient(
        WorkflowEndpointConfig(
            "workflow_a",
            "https://dify.example",
            "secret",
            response_mode="blocking",
        )
    )

    result = client.run({"task_id": "test"})

    assert result.workflow_run_id == "run-a-1"
    assert result.outputs["design_candidate"]["candidate_id"] == "hyp::minimum"
    assert result.outputs["guardrail_report"] == {"passed": True}


def test_generic_streaming_client_adds_local_event_context_only(monkeypatch):
    terminal = {
        "event": "workflow_finished",
        "workflow_run_id": "run-a-stream",
        "task_id": "task-a-stream",
        "data": {
            "id": "run-a-stream",
            "status": "succeeded",
            "elapsed_time": 1.0,
            "total_tokens": 12,
            "outputs": {
                "design_candidate": '{"candidate_id":"hyp_001::minimum_viable"}',
                "guardrail_report": '{"passed":true}',
            },
        },
    }
    streamed_events = [
        {"event": "workflow_started", "data": {"id": "run-a-stream"}},
        {"event": "node_started", "data": {"title": "Generate"}},
        terminal,
    ]
    sent_payloads: list[dict[str, Any]] = []

    def open_stream(request, timeout):
        assert timeout == 300
        sent_payloads.append(json.loads(request.data.decode("utf-8")))
        return FakeStreamingHTTPResponse(streamed_events)

    monkeypatch.setattr("urllib.request.urlopen", open_stream)
    received_events: list[tuple[str, dict[str, Any]]] = []
    client = GenericDifyWorkflowClient(
        WorkflowEndpointConfig(
            "workflow_a",
            "https://dify.example",
            "secret",
            response_mode="streaming",
        ),
        event_handler=lambda workflow, event: received_events.append((workflow, event)),
    )
    context = {
        "workflow_stage": "A",
        "hypothesis_id": "hyp_001",
        "variant_mode": "minimum_viable",
        "round": 1,
        "attempt": 1,
    }

    result = client.run({"task_id": "test"}, event_context=context)

    assert result.workflow_run_id == "run-a-stream"
    assert sent_payloads[0]["inputs"] == {"task_id": "test"}
    assert "planning_context" not in sent_payloads[0]
    assert [workflow for workflow, _event in received_events] == ["workflow_a"] * 3
    assert all(event["planning_context"] == context for _, event in received_events)


def test_chain_runs_three_candidates_then_selector_then_final_plan():
    candidate = FakeWorkflowClient("a", _candidate_outputs)
    selector = FakeWorkflowClient("b", _accept_outputs)
    planner = FakeWorkflowClient("c", _plan_outputs)
    runner = PlanningWorkflowChainRunner(candidate, selector, planner)

    data = short_sample_planner_input()
    data["_feedback"] = "Prioritize a smaller sample for this iteration."
    report = runner.run(data)

    assert report["status"] == "success"
    assert report["decision"] == "accept"
    assert report["next_action"] == "continue_to_product"
    assert len(candidate.calls) == 3
    assert {call["_feedback"] for call in candidate.calls} == {data["_feedback"]}
    assert {call["variant_mode"] for call in candidate.calls} == set(DEFAULT_VARIANTS)
    assert len(json.loads(selector.calls[0]["design_candidates"])) == 3
    c_constraints = json.loads(planner.calls[0]["planning_constraints"])
    assert c_constraints["selected_design"]["candidate_id"].startswith("hyp_short_001::")
    assert c_constraints["design_selection"]["decision"] == "accept"
    assert {context["variant_mode"] for context in candidate.event_contexts} == set(
        DEFAULT_VARIANTS
    )
    assert all(
        context["workflow_stage"] == "A"
        and context["hypothesis_id"] == "hyp_short_001"
        and context["round"] == 1
        and context["attempt"] == 1
        for context in candidate.event_contexts
    )
    assert selector.event_contexts == [
        {
            "workflow_stage": "B",
            "hypothesis_id": "hyp_short_001",
            "round": 1,
            "attempt": 1,
        }
    ]
    assert planner.event_contexts[0]["workflow_stage"] == "C"
    assert planner.event_contexts[0]["hypothesis_id"] == "hyp_short_001"
    assert planner.event_contexts[0]["selected_candidate_id"].startswith("hyp_short_001::")
    assert report["final_result"]["plan"]["title"] == "Final test plan"


def test_revise_once_runs_exactly_one_bounded_candidate_revision():
    candidate = FakeWorkflowClient("a", _candidate_outputs)
    selector_calls = 0
    selector_lock = threading.Lock()

    def revise_then_accept(inputs: dict[str, Any]) -> dict[str, Any]:
        nonlocal selector_calls
        with selector_lock:
            selector_calls += 1
            current = selector_calls
        if current == 1:
            selection = {
                "decision": "revise_once",
                "selected_candidate_id": "",
                "selected_design": {},
                "revision_instruction": "Tighten the resource budget and stopping criteria.",
                "candidate_reviews": [],
                "feedback_tasks": [],
                "meta_review": {},
                "limitations": [],
            }
            return {
                "design_selection": selection,
                "selected_design": {},
                "selection_guardrail_report": {"passed": True, "issues": []},
            }
        return _accept_outputs(inputs)

    selector = FakeWorkflowClient("b", revise_then_accept)
    planner = FakeWorkflowClient("c", _plan_outputs)
    runner = PlanningWorkflowChainRunner(candidate, selector, planner)

    report = runner.run(short_sample_planner_input(), max_revisions=1)

    assert report["status"] == "success"
    assert len(candidate.calls) == 6
    assert len(selector.calls) == 2
    assert len(planner.calls) == 1
    revised_calls = [
        call
        for call in candidate.calls
        if "candidate_revision" in json.loads(call["planning_constraints"])
    ]
    assert len(revised_calls) == 3
    assert len(report["intermediate_results"]["candidate_rounds"]) == 2


def test_human_review_stops_before_workflow_c():
    candidate = FakeWorkflowClient("a", _candidate_outputs)

    def human_review(inputs: dict[str, Any]) -> dict[str, Any]:
        selection = {
            "decision": "human_review",
            "selected_candidate_id": "",
            "selected_design": {},
            "revision_instruction": "",
            "candidate_reviews": [],
            "feedback_tasks": [],
            "meta_review": {},
            "limitations": ["Candidates are tied."],
        }
        return {
            "design_selection": selection,
            "selected_design": {},
            "selection_guardrail_report": {"passed": True, "issues": []},
        }

    selector = FakeWorkflowClient("b", human_review)
    planner = FakeWorkflowClient("c", _plan_outputs)
    runner = PlanningWorkflowChainRunner(candidate, selector, planner)

    report = runner.run(short_sample_planner_input())

    assert report["status"] == "requires_action"
    assert report["next_action"] == "human_review"
    assert planner.calls == []
    assert report["final_result"] is None


def test_html_report_escapes_model_output_and_shows_intermediate_sections():
    candidate = FakeWorkflowClient("a", _candidate_outputs)
    selector = FakeWorkflowClient("b", _accept_outputs)
    planner = FakeWorkflowClient("c", _plan_outputs)
    report = PlanningWorkflowChainRunner(candidate, selector, planner).run(
        short_sample_planner_input()
    )
    report["final_result"]["plan"]["title"] = "<script>alert(1)</script>"

    rendered = render_html_report(report)

    assert "Workflow A candidates" in rendered
    assert "Workflow B selection" in rendered
    assert "Workflow C result" in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<script>alert(1)</script>" not in rendered
def test_workflow_c_system_identity_is_normalized_locally():
    candidate = FakeWorkflowClient("a", _candidate_outputs)
    selector = FakeWorkflowClient("b", _accept_outputs)

    def wrong_identity_plan(inputs: dict[str, Any]) -> dict[str, Any]:
        output = _plan_outputs(inputs)
        output["plan_result"].update(
            {
                "schema_version": "wrong",
                "agent_name": "wrong",
                "task_id": "wrong",
                "iteration": 99,
                "hypothesis_id": "wrong",
                "status": "success",
            }
        )
        return output

    planner = FakeWorkflowClient("c", wrong_identity_plan)
    report = PlanningWorkflowChainRunner(candidate, selector, planner).run(
        short_sample_planner_input()
    )

    final = report["final_result"]
    assert final["schema_version"] == "experiment_planner_plan_result_v1"
    assert final["agent_name"] == "ExperimentPlannerAgent"
    assert final["task_id"] == "task_short_001"
    assert final["iteration"] == 1
    assert final["hypothesis_id"] == "hyp_short_001"
    assert set(report["stages"][-1]["normalized_identity_fields"]) == {
        "schema_version",
        "agent_name",
        "task_id",
        "iteration",
        "hypothesis_id",
    }


def test_workflow_c_failed_business_status_is_not_reported_as_success():
    candidate = FakeWorkflowClient("a", _candidate_outputs)
    selector = FakeWorkflowClient("b", _accept_outputs)

    def failed_plan(inputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "plan_result": {
                "status": "failed",
                "error_message": "Final plan contract failed.",
                "hypothesis_id": inputs["hypothesis_id"],
                "plan": {},
            }
        }

    planner = FakeWorkflowClient("c", failed_plan)
    report = PlanningWorkflowChainRunner(candidate, selector, planner).run(
        short_sample_planner_input()
    )

    assert report["status"] == "failed"
    assert report["next_action"] == "inspect_failure"
    assert report["stages"][-1]["status"] == "failed"
    assert report["final_result"]["error_message"] == "Final plan contract failed."
def test_workflow_b_failed_guardrail_is_promoted_to_top_level_errors():
    candidate = FakeWorkflowClient("a", _candidate_outputs)

    def failed_selection(_inputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "design_selection": {
                "decision": "failed",
                "selected_candidate_id": "",
                "candidate_reviews": [],
                "limitations": ["Unknown candidate reference."],
            },
            "selected_design": {},
            "selection_guardrail_report": {
                "passed": False,
                "issues": ["candidate_reviews contain unknown candidate_id: invented"],
            },
        }

    selector = FakeWorkflowClient("b", failed_selection)
    planner = FakeWorkflowClient("c", _plan_outputs)
    report = PlanningWorkflowChainRunner(candidate, selector, planner).run(
        short_sample_planner_input()
    )

    assert report["status"] == "failed"
    assert report["next_action"] == "inspect_failure"
    assert report["errors"] == [
        "Workflow B guardrail: candidate_reviews contain unknown candidate_id: invented"
    ]
    assert planner.calls == []


def test_workflow_c_success_with_empty_plan_is_converted_to_failure():
    candidate = FakeWorkflowClient("a", _candidate_outputs)
    selector = FakeWorkflowClient("b", _accept_outputs)

    def empty_plan(_inputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "plan_result": {
                "status": "success",
                "error_message": "",
                "plan": {},
            }
        }

    planner = FakeWorkflowClient("c", empty_plan)
    report = PlanningWorkflowChainRunner(candidate, selector, planner).run(
        short_sample_planner_input()
    )

    assert report["status"] == "failed"
    assert report["stages"][-1]["status"] == "failed"
    assert report["final_result"]["status"] == "failed"
    assert report["errors"] == ["Workflow C returned an empty plan."]
def test_workflow_a_soft_rejection_is_repaired_and_included_in_selection():
    def one_rejected_candidate(inputs: dict[str, Any]) -> dict[str, Any]:
        output = _candidate_outputs(inputs)
        if inputs["variant_mode"] == "resource_efficient":
            output["design_candidate"]["status"] = "partial_success"
            output["guardrail_report"] = {
                "passed": False,
                "issues": [
                    "method_steps must be non-empty",
                    "unknown evidence_id: invented",
                ],
            }
        return output

    candidate = FakeWorkflowClient("a", one_rejected_candidate)
    selector = FakeWorkflowClient("b", _accept_outputs)
    planner = FakeWorkflowClient("c", _plan_outputs)
    report = PlanningWorkflowChainRunner(candidate, selector, planner).run(
        short_sample_planner_input()
    )

    selected_inputs = json.loads(selector.calls[0]["design_candidates"])
    candidate_stage = report["stages"][0]
    candidate_round = report["intermediate_results"]["candidate_rounds"][0]

    assert len(selected_inputs) == 3
    assert {item["variant_mode"] for item in selected_inputs} == {
        "minimum_viable",
        "high_information",
        "resource_efficient",
    }
    assert candidate_stage["status"] == "partial_success"
    assert candidate_stage["accepted_candidate_count"] == 3
    assert candidate_stage["rejected_candidate_count"] == 0
    assert len(candidate_round["guardrail_reports"]) == 3
    assert candidate_stage["degraded_candidate_count"] == 1
    repaired = next(
        item for item in selected_inputs if item["variant_mode"] == "resource_efficient"
    )
    assert repaired["schema_version"] == "design_candidate_v1"
    assert repaired["method_steps"]
    assert repaired["falsification_matrix"]
    assert "invented" not in repaired["rationale"]["evidence_ids"]


def test_workflow_a_nonrepairable_guardrail_rejection_is_excluded_from_selection():
    def one_hard_rejection(inputs: dict[str, Any]) -> dict[str, Any]:
        output = _candidate_outputs(inputs)
        if inputs["variant_mode"] == "resource_efficient":
            output["design_candidate"]["status"] = "partial_success"
            output["guardrail_report"] = {
                "passed": False,
                "issues": ["fabricated execution result"],
            }
        return output

    candidate = FakeWorkflowClient("a", one_hard_rejection)
    selector = FakeWorkflowClient("b", _accept_outputs)
    planner = FakeWorkflowClient("c", _plan_outputs)
    report = PlanningWorkflowChainRunner(candidate, selector, planner).run(
        short_sample_planner_input()
    )

    selected_inputs = json.loads(selector.calls[0]["design_candidates"])
    candidate_stage = report["stages"][0]

    assert report["status"] == "success"
    assert len(selected_inputs) == 2
    assert all(item["variant_mode"] != "resource_efficient" for item in selected_inputs)
    assert candidate_stage["accepted_candidate_count"] == 2
    assert candidate_stage["rejected_candidate_count"] == 1
    assert candidate_stage["degraded_candidate_count"] == 0


def test_workflow_a_all_soft_rejections_still_reach_workflow_c():
    def fragmented_candidate(inputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "design_candidate": {
                "hypothesis_id": inputs["hypothesis_id"],
                "variant_mode": inputs["variant_mode"],
                "candidate_id": (
                    f'{inputs["hypothesis_id"]}::{inputs["variant_mode"]}'
                ),
                "status": "partial_success",
                "text": "Model returned only a grounded fragment.",
            },
            "guardrail_report": {
                "passed": False,
                "issues": [
                    "schema_version is invalid",
                    "method_steps must be non-empty",
                    "falsification_matrix must be non-empty",
                ],
            },
        }

    candidate = FakeWorkflowClient("a", fragmented_candidate)
    selector = FakeWorkflowClient("b", _accept_outputs)
    planner = FakeWorkflowClient("c", _plan_outputs)
    report = PlanningWorkflowChainRunner(candidate, selector, planner).run(
        short_sample_planner_input()
    )

    selected_inputs = json.loads(selector.calls[0]["design_candidates"])
    candidate_stage = report["stages"][0]

    assert report["status"] == "success"
    assert report["decision"] == "accept"
    assert len(selected_inputs) == 3
    assert len(planner.calls) == 1
    assert candidate_stage["status"] == "partial_success"
    assert candidate_stage["accepted_candidate_count"] == 3
    assert candidate_stage["rejected_candidate_count"] == 0
    assert candidate_stage["degraded_candidate_count"] == 3
    assert all(item["method_steps"] for item in selected_inputs)
    assert all(item["falsification_matrix"] for item in selected_inputs)


def test_workflow_b_malformed_structured_output_is_retried_once():
    candidate = FakeWorkflowClient("a", _candidate_outputs)
    call_count = 0

    def malformed_then_accept(inputs: dict[str, Any]) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "design_selection": {
                    "decision": "failed",
                    "selected_candidate_id": "",
                    "limitations": ["schema_version is invalid"],
                },
                "selected_design": {},
                "selection_guardrail_report": {
                    "passed": False,
                    "issues": [
                        "schema_version is invalid",
                        "candidate_reviews are missing candidate_id",
                    ],
                },
            }
        return _accept_outputs(inputs)

    selector = FakeWorkflowClient("b", malformed_then_accept)
    planner = FakeWorkflowClient("c", _plan_outputs)
    report = PlanningWorkflowChainRunner(candidate, selector, planner).run(
        short_sample_planner_input()
    )

    selection_rounds = report["intermediate_results"]["selection_rounds"]
    retry_constraints = json.loads(selector.calls[1]["planning_constraints"])

    assert report["status"] == "success"
    assert len(selector.calls) == 2
    assert len(planner.calls) == 1
    assert [item["attempt"] for item in selection_rounds] == [1, 2]
    assert retry_constraints["selection_format_retry"]["attempt"] == 2
    assert "schema_version is invalid" in retry_constraints["selection_format_retry"][
        "previous_issues"
    ]


def test_workflow_b_format_retry_can_be_disabled():
    candidate = FakeWorkflowClient("a", _candidate_outputs)

    def malformed(_inputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "design_selection": {"decision": "failed"},
            "selected_design": {},
            "selection_guardrail_report": {
                "passed": False,
                "issues": ["schema_version is invalid"],
            },
        }

    selector = FakeWorkflowClient("b", malformed)
    planner = FakeWorkflowClient("c", _plan_outputs)
    report = PlanningWorkflowChainRunner(
        candidate,
        selector,
        planner,
        max_selection_retries=0,
    ).run(short_sample_planner_input())

    assert report["status"] == "failed"
    assert len(selector.calls) == 1
    assert planner.calls == []


def test_batch_chain_runs_every_hypothesis_concurrently_and_preserves_order():
    data = short_sample_planner_input()
    barrier = threading.Barrier(6)
    progress_messages: list[str] = []

    def synchronized_candidate(inputs: dict[str, Any]) -> dict[str, Any]:
        barrier.wait(timeout=5)
        return _candidate_outputs(inputs)

    candidate = FakeWorkflowClient("a", synchronized_candidate)
    selector = FakeWorkflowClient("b", _accept_outputs)
    planner = FakeWorkflowClient("c", _plan_outputs)
    runner = PlanningWorkflowChainRunner(
        candidate,
        selector,
        planner,
        progress_handler=progress_messages.append,
    )

    report = runner.run_batch(data, max_parallel_hypotheses=2)

    assert report["schema_version"] == "planning_workflow_chain_batch_test_v1"
    assert report["status"] == "success"
    assert report["next_action"] == "continue_to_product"
    assert report["hypothesis_ids"] == ["hyp_short_001", "hyp_short_002"]
    assert report["summary"] == {
        "total": 2,
        "success": 2,
        "requires_action": 0,
        "failed": 0,
    }
    assert [item["hypothesis_id"] for item in report["hypothesis_runs"]] == [
        "hyp_short_001",
        "hyp_short_002",
    ]
    assert all(item["decision"] == "accept" for item in report["hypothesis_runs"])
    assert all(item["final_result"]["plan"] for item in report["hypothesis_runs"])
    assert len(candidate.calls) == 6
    assert len(selector.calls) == 2
    assert len(planner.calls) == 2
    assert any("[hyp_short_001]" in message for message in progress_messages)
    assert any("[hyp_short_002]" in message for message in progress_messages)

    statements = {
        item["hypothesis_id"]: item["statement"] for item in data["hypothesis_cards"]
    }
    for call in candidate.calls:
        package = json.loads(call["hypothesis_evidence_package"])
        assert package["hypothesis"] == statements[call["hypothesis_id"]]


def test_batch_chain_requires_action_when_one_hypothesis_stops_at_b():
    data = short_sample_planner_input()
    candidate = FakeWorkflowClient("a", _candidate_outputs)

    def accept_or_review(inputs: dict[str, Any]) -> dict[str, Any]:
        if inputs["hypothesis_id"] == "hyp_short_001":
            return _accept_outputs(inputs)
        return {
            "design_selection": {
                "decision": "human_review",
                "selected_candidate_id": "",
                "selected_design": {},
                "revision_instruction": "",
                "candidate_reviews": [],
                "feedback_tasks": [],
                "meta_review": {},
                "limitations": ["Candidates are tied."],
            },
            "selected_design": {},
            "selection_guardrail_report": {"passed": True, "issues": []},
        }

    selector = FakeWorkflowClient("b", accept_or_review)
    planner = FakeWorkflowClient("c", _plan_outputs)

    report = PlanningWorkflowChainRunner(candidate, selector, planner).run_batch(
        data, max_parallel_hypotheses=2
    )

    assert report["status"] == "requires_action"
    assert report["next_action"] == "resolve_hypothesis_actions"
    assert report["summary"] == {
        "total": 2,
        "success": 1,
        "requires_action": 1,
        "failed": 0,
    }
    assert [item["status"] for item in report["hypothesis_runs"]] == [
        "success",
        "requires_action",
    ]
    assert len(planner.calls) == 1


def test_batch_html_report_shows_each_hypothesis_and_escapes_output():
    candidate = FakeWorkflowClient("a", _candidate_outputs)
    selector = FakeWorkflowClient("b", _accept_outputs)
    planner = FakeWorkflowClient("c", _plan_outputs)
    report = PlanningWorkflowChainRunner(candidate, selector, planner).run_batch(
        short_sample_planner_input()
    )
    report["hypothesis_runs"][0]["final_result"]["plan"]["title"] = (
        "<script>alert(1)</script>"
    )

    rendered = render_html_report(report)

    assert "Planning workflow batch chain test" in rendered
    assert "hyp_short_001" in rendered
    assert "hyp_short_002" in rendered
    assert "Complete A/B/C subreports" in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<script>alert(1)</script>" not in rendered
