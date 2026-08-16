from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from planning_agent.runtime import (
    PlanningExecutionError,
    PlanningFormatError,
    PlanningLLMClient,
    PlanningLLMConfig,
    StageRunResult,
    parse_json_object,
)
from planning_agent.sample_data import short_sample_planner_input
from planning_agent.workflow_chain import PlanningProtocolRunner
from planning_agent.workflow_chain_report import render_html_report


class FakeProtocolClient:
    configured = True

    def __init__(self, handler: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None):
        self.handler = handler or default_outputs
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def public_summary(self):
        return {
            "configured": True,
            "mode": "local_protocol_compiler",
            "model": "fake-qwen",
            "thinking_enabled": False,
        }

    def run(self, stage, inputs, event_context=None):
        with self._lock:
            self.calls.append((stage, inputs, dict(event_context or {})))
            self.active += 1
            self.peak = max(self.peak, self.active)
            number = len(self.calls)
        try:
            outputs = self.handler(stage, inputs)
        finally:
            with self._lock:
                self.active -= 1
        return StageRunResult(
            stage=stage,
            run_id=f"{stage}-{number}",
            request_id=str(inputs.get("task_id") or ""),
            status="succeeded",
            elapsed_time=0.01,
            total_tokens=10,
            outputs=outputs,
        )


class FakeStream(list):
    closed = False

    def close(self):
        self.closed = True


class FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.kwargs = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def complete_plan(hypothesis_id: str):
    return {
        "schema_version": "experiment_planner_plan_result_v1",
        "agent_name": "ExperimentPlannerAgent",
        "task_id": "task_short_001",
        "iteration": 1,
        "hypothesis_id": hypothesis_id,
        "status": "success",
        "error_message": "",
        "plan": {
            "problem_statement": f"Plan for {hypothesis_id}",
            "rationale": {"text": "why", "logic_chain": []},
            "technical_details": {"required_methods": ["analysis"]},
            "datasets": [],
            "paper_title": "Protocol",
            "paper_abstract": "Expected study",
            "methods": [{"name": "Prepare"}],
            "experiments": [{"objective": "Test"}],
            "results": {"expected_findings": ["pattern"]},
            "references": [],
            "feedback_tasks": [],
            "limitations": [],
        },
    }


def default_outputs(stage: str, inputs: dict[str, Any]):
    hypothesis_id = inputs["hypothesis_id"]
    if stage == "draft":
        return {
            "protocol_draft": {
                "schema_version": "planning_protocol_draft_v1",
                "hypothesis_id": hypothesis_id,
                "status": "success",
                "protocol": complete_plan(hypothesis_id)["plan"],
                "assumptions": [],
                "unresolved_gaps": [],
            },
            "contract_report": {"passed": True, "issues": []},
        }
    if stage.startswith("review_"):
        role = stage.removeprefix("review_")
        return {
            "protocol_review": {
                "schema_version": "planning_protocol_review_v1",
                "hypothesis_id": hypothesis_id,
                "review_role": role,
                "verdict": "pass",
                "summary": "ready",
                "strengths": [],
                "issues": [],
            },
            "contract_report": {"passed": True, "issues": []},
        }
    return {
        "plan_result": complete_plan(hypothesis_id),
        "contract_report": {"passed": True, "issues": [], "repairable": False},
    }


def test_runtime_config_uses_shared_bailian_credentials_and_protocol_summary(monkeypatch):
    monkeypatch.setenv("PLANNING_AGENT_SKIP_DOTENV", "1")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("PLANNING_MODEL", "qwen-test")
    config = PlanningLLMConfig.from_env({"thinking_enabled": True, "max_tokens": 4096})
    summary = config.public_summary("protocol_compiler")
    assert config.configured is True
    assert summary["mode"] == "local_protocol_compiler"
    assert summary["max_repair_attempts"] == 1
    assert "test-key" not in json.dumps(summary)


def test_parse_json_object_strips_fences_and_thinking():
    assert parse_json_object('<think>hidden</think>```json\n{"passed": true}\n```') == {"passed": True}
    with pytest.raises(PlanningFormatError, match="invalid JSON"):
        parse_json_object("ordinary output")


def test_streaming_client_collects_json_usage_and_safe_events():
    stream = FakeStream([
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='{"passed":'))], usage=None),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="true}"))], usage=None),
        SimpleNamespace(choices=[], usage=SimpleNamespace(total_tokens=42)),
    ])
    completions = FakeCompletions([stream])
    events = []
    client = PlanningLLMClient(
        PlanningLLMConfig("test-key", "https://bailian.example/v1", "qwen-test"),
        event_handler=lambda stage, event: events.append((stage, event)),
        sdk_client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    result = client.complete_json(
        stage="draft",
        system_prompt="json",
        user_prompt="json",
        temperature=0.2,
        event_context={"hypothesis_id": "hyp_001"},
        allow_thinking=True,
    )
    assert result.value == {"passed": True}
    assert result.total_tokens == 42
    assert stream.closed is True
    assert completions.kwargs["stream"] is True
    assert all("test-key" not in json.dumps(event) for _, event in events)


def test_invalid_json_is_preserved_as_format_error_without_transport_retry():
    stream = FakeStream([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="not-json"))], usage=None)])
    completions = FakeCompletions([stream])
    client = PlanningLLMClient(
        PlanningLLMConfig("key", "https://bailian.example/v1", "qwen", max_retries=2),
        sdk_client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    with pytest.raises(PlanningFormatError):
        client.complete_json(
            stage="synthesis", system_prompt="json", user_prompt="json",
            temperature=0.1, event_context={}, allow_thinking=False,
        )
    assert completions.calls == 1


def test_transport_failure_retries_and_redacts_errors():
    stream = FakeStream([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='{"ok":true}'))], usage=None)])
    completions = FakeCompletions([TimeoutError("timed out"), stream])
    client = PlanningLLMClient(
        PlanningLLMConfig("secret-key", "https://bailian.example/v1", "qwen", max_retries=1),
        sdk_client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    result = client.complete_json(
        stage="draft", system_prompt="sensitive", user_prompt="sensitive",
        temperature=0.2, event_context={}, allow_thinking=True,
    )
    assert result.value == {"ok": True}
    assert completions.calls == 2


def test_streaming_client_closes_response_immediately_on_cancellation():
    class CancellationRequested(BaseException):
        pass

    stream = FakeStream([
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='{"ok":'))], usage=None),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="true}"))], usage=None),
    ])
    checks = {"count": 0}

    def cancellation_checker():
        checks["count"] += 1
        if checks["count"] == 3:
            raise CancellationRequested()

    client = PlanningLLMClient(
        PlanningLLMConfig("key", "https://bailian.example/v1", "qwen"),
        cancellation_checker=cancellation_checker,
        sdk_client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions([stream]))),
    )
    with pytest.raises(CancellationRequested):
        client.complete_json(
            stage="draft", system_prompt="json", user_prompt="json",
            temperature=0.2, event_context={}, allow_thinking=True,
        )
    assert stream.closed is True


def test_protocol_runner_uses_one_draft_three_reviews_and_one_synthesis():
    client = FakeProtocolClient()
    runner = PlanningProtocolRunner(client, max_parallel_calls=3)
    report = runner.run(short_sample_planner_input(), hypothesis_id="hyp_short_001")

    assert report["status"] == "success"
    assert report["model_call_count"] == 5
    assert [stage for stage, _, _ in client.calls] == [
        "draft", "review_methodology", "review_statistics", "review_feasibility", "synthesis"
    ] or {stage for stage, _, _ in client.calls[1:4]} == {
        "review_methodology", "review_statistics", "review_feasibility"
    }
    assert report["final_result"]["plan"]["problem_statement"].startswith("Plan for")
    review_contexts = [context for stage, _, context in client.calls if stage.startswith("review_")]
    assert {context["review_role"] for context in review_contexts} == {
        "methodology", "statistics", "feasibility"
    }


def test_one_review_failure_is_isolated_and_marks_plan_partial():
    def handler(stage, inputs):
        if stage == "review_statistics":
            raise PlanningExecutionError("statistics review unavailable")
        return default_outputs(stage, inputs)

    report = PlanningProtocolRunner(FakeProtocolClient(handler), max_parallel_calls=3).run(
        short_sample_planner_input(), hypothesis_id="hyp_short_001"
    )
    assert report["status"] == "partial_success"
    assert report["final_result"]["status"] == "partial_success"
    assert report["intermediate_results"]["merged_reviews"]["failed_roles"] == ["statistics"]


def test_synthesis_invalid_json_uses_exactly_one_contract_repair():
    def handler(stage, inputs):
        if stage == "synthesis":
            raise PlanningFormatError("Planning model returned invalid JSON.")
        return default_outputs(stage, inputs)

    client = FakeProtocolClient(handler)
    report = PlanningProtocolRunner(
        client, max_parallel_calls=3, max_repair_attempts=1
    ).run(short_sample_planner_input(), hypothesis_id="hyp_short_001")
    assert report["status"] == "success"
    assert report["model_call_count"] == 6
    assert [stage for stage, _, _ in client.calls].count("repair") == 1


def test_nonrepairable_final_contract_failure_does_not_call_repair():
    def handler(stage, inputs):
        if stage == "synthesis":
            result = default_outputs(stage, inputs)
            result["contract_report"] = {
                "passed": False,
                "repairable": False,
                "issues": ["unknown source_ids: invented"],
            }
            return result
        return default_outputs(stage, inputs)

    client = FakeProtocolClient(handler)
    report = PlanningProtocolRunner(client, max_parallel_calls=3).run(
        short_sample_planner_input(), hypothesis_id="hyp_short_001"
    )
    assert report["status"] == "failed"
    assert all(stage != "repair" for stage, _, _ in client.calls)


def test_no_evidence_stops_before_any_model_call():
    data = short_sample_planner_input()
    data["evidence_map"][0]["supporting_evidence_ids"] = []
    data["hypothesis_cards"][0]["based_on_evidence_ids"] = []
    client = FakeProtocolClient()
    report = PlanningProtocolRunner(client).run(data, hypothesis_id="hyp_short_001")
    assert report["status"] == "failed"
    assert report["next_action"] == "request_upstream_evidence"
    assert client.calls == []


def test_batch_preserves_input_order_when_completion_order_differs():
    def handler(stage, inputs):
        if stage == "draft" and inputs["hypothesis_id"] == "hyp_short_001":
            time.sleep(0.03)
        return default_outputs(stage, inputs)

    report = PlanningProtocolRunner(FakeProtocolClient(handler), max_parallel_calls=4).run_batch(
        short_sample_planner_input(), max_parallel_hypotheses=2, max_parallel_calls=4
    )
    assert [item["hypothesis_id"] for item in report["hypothesis_runs"]] == [
        "hyp_short_001", "hyp_short_002"
    ]


def test_global_model_concurrency_limit_applies_across_hypotheses_and_reviews():
    def handler(stage, inputs):
        time.sleep(0.005)
        return default_outputs(stage, inputs)

    client = FakeProtocolClient(handler)
    report = PlanningProtocolRunner(client, max_parallel_calls=2).run_batch(
        short_sample_planner_input(), max_parallel_hypotheses=2, max_parallel_calls=2
    )
    assert report["status"] == "success"
    assert 1 < client.peak <= 2


def test_html_report_escapes_final_model_output_and_shows_protocol_stages():
    report = PlanningProtocolRunner(FakeProtocolClient(), max_parallel_calls=3).run(
        short_sample_planner_input(), hypothesis_id="hyp_short_001"
    )
    report["final_result"]["plan"]["paper_title"] = "<script>alert(1)</script>"
    rendered = render_html_report(report)
    assert "Protocol stages" in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<script>alert(1)</script>" not in rendered
