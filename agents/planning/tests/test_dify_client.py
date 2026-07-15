import json
import urllib.error

import pytest

from planning_agent.dify_client import DifyWorkflowClient, DifyWorkflowError


class ErrorBody:
    def __init__(self, text: str) -> None:
        self._text = text.encode("utf-8")

    def read(self) -> bytes:
        return self._text

    def close(self) -> None:
        pass


class StreamingResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = [line.encode("utf-8") for line in lines]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def __iter__(self):
        return iter(self._lines)


class SuccessfulResponse:
    def __init__(self, data: dict) -> None:
        self._body = json.dumps(data, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self._body


def test_http_error_includes_dify_response_body(monkeypatch):
    def raise_http_error(request, timeout):
        raise urllib.error.HTTPError(
            url="http://example.test/v1/workflows/run",
            code=500,
            msg="INTERNAL SERVER ERROR",
            hdrs={},
            fp=ErrorBody('{"code":"internal_server_error","message":"Start variable missing"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", raise_http_error)
    client = DifyWorkflowClient(
        api_url="http://example.test", api_key="test-key", response_mode="blocking"
    )

    with pytest.raises(DifyWorkflowError) as exc_info:
        client.run_workflow({"task_id": "task_demo_001"})

    message = str(exc_info.value)
    assert "HTTP 500 INTERNAL SERVER ERROR" in message
    assert "Start variable missing" in message


def test_failed_workflow_response_raises_dify_error(monkeypatch):
    def return_failed_workflow(request, timeout):
        return SuccessfulResponse(
            {
                "workflow_run_id": "run_001",
                "data": {
                    "id": "run_001",
                    "status": "failed",
                    "outputs": {},
                    "error": "Variable hypothesis_evidence_packages is required",
                },
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", return_failed_workflow)
    client = DifyWorkflowClient(
        api_url="http://example.test", api_key="test-key", response_mode="blocking"
    )

    with pytest.raises(DifyWorkflowError) as exc_info:
        client.run_workflow({"task_id": "task_demo_001"})

    message = str(exc_info.value)
    assert "Dify workflow run failed" in message
    assert "Variable hypothesis_evidence_packages is required" in message


def test_research_plan_output_json_string_is_parsed(monkeypatch):
    plan = {
        "schema_version": "experiment_planner_output_v1",
        "agent_name": "ExperimentPlannerAgent",
        "task_id": "task_demo_001",
        "iteration": 1,
        "status": "success",
        "plans": [{"hypothesis_id": "hyp_001", "plan": {}}],
    }

    def return_successful_workflow(request, timeout):
        return SuccessfulResponse(
            {
                "workflow_run_id": "run_001",
                "data": {
                    "id": "run_001",
                    "status": "succeeded",
                    "outputs": {"research_plan": json.dumps(plan)},
                    "error": None,
                },
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", return_successful_workflow)
    client = DifyWorkflowClient(
        api_url="http://example.test", api_key="test-key", response_mode="blocking"
    )

    assert client.run_workflow({"task_id": "task_demo_001"}) == plan


def test_plan_result_output_json_string_is_parsed(monkeypatch):
    plan_result = {
        "schema_version": "experiment_planner_plan_result_v1",
        "agent_name": "ExperimentPlannerAgent",
        "task_id": "task_demo_001",
        "iteration": 1,
        "hypothesis_id": "hyp_001",
        "status": "success",
        "error_message": "",
        "plan": {"problem_statement": "demo"},
    }

    def return_successful_workflow(request, timeout):
        return SuccessfulResponse(
            {
                "workflow_run_id": "run_001",
                "data": {
                    "id": "run_001",
                    "status": "succeeded",
                    "outputs": {"plan_result": json.dumps(plan_result)},
                    "error": None,
                },
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", return_successful_workflow)
    client = DifyWorkflowClient(
        api_url="http://example.test", api_key="test-key", response_mode="blocking"
    )

    assert client.run_workflow({"task_id": "task_demo_001"}) == plan_result


def test_thinking_wrapped_plan_result_output_is_parsed(monkeypatch):
    plan_result = {
        "schema_version": "experiment_planner_plan_result_v1",
        "agent_name": "ExperimentPlannerAgent",
        "task_id": "task_demo_001",
        "iteration": 1,
        "hypothesis_id": "hyp_002",
        "status": "success",
        "error_message": "",
        "plan": {"problem_statement": "demo"},
    }
    wrapped_output = (
        '<think>Thinking Process with a misleading JSON example: '
        '{"schema_version": "not_the_answer"}</think>'
        + json.dumps(plan_result, ensure_ascii=False)
    )

    def return_successful_workflow(request, timeout):
        return SuccessfulResponse(
            {
                "workflow_run_id": "run_001",
                "data": {
                    "id": "run_001",
                    "status": "succeeded",
                    "outputs": {"plan_result": wrapped_output},
                    "error": None,
                },
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", return_successful_workflow)
    client = DifyWorkflowClient(
        api_url="http://example.test", api_key="test-key", response_mode="blocking"
    )

    assert client.run_workflow({"task_id": "task_demo_001"}) == plan_result


def test_unparseable_plan_result_error_includes_output_preview(monkeypatch):
    def return_successful_workflow(request, timeout):
        return SuccessfulResponse(
            {
                "workflow_run_id": "run_001",
                "data": {
                    "id": "run_001",
                    "status": "succeeded",
                    "outputs": {"plan_result": "<think>only reasoning</think>not json"},
                    "error": None,
                },
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", return_successful_workflow)
    client = DifyWorkflowClient(
        api_url="http://example.test", api_key="test-key", response_mode="blocking"
    )

    with pytest.raises(DifyWorkflowError) as exc_info:
        client.run_workflow({"task_id": "task_demo_001"})

    message = str(exc_info.value)
    assert "Dify output could not be parsed as JSON." in message
    assert "Output preview:" in message
    assert "not json" in message


def test_socket_timeout_is_wrapped_as_dify_error(monkeypatch):
    def raise_timeout(request, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", raise_timeout)
    client = DifyWorkflowClient(
        api_url="http://example.test", api_key="test-key", response_mode="blocking"
    )

    with pytest.raises(DifyWorkflowError) as exc_info:
        client.run_workflow({"task_id": "task_demo_001"})

    message = str(exc_info.value)
    assert "Dify request to http://example.test/v1/workflows/run timed out" in message


def test_streaming_workflow_finished_plan_result_is_parsed(monkeypatch):
    plan_result = {
        "schema_version": "experiment_planner_plan_result_v1",
        "agent_name": "ExperimentPlannerAgent",
        "task_id": "task_demo_001",
        "iteration": 1,
        "hypothesis_id": "hyp_001",
        "status": "success",
        "error_message": "",
        "plan": {"problem_statement": "demo"},
    }

    def return_streaming_workflow(request, timeout):
        request_payload = json.loads(request.data.decode("utf-8"))
        assert request_payload["response_mode"] == "streaming"
        event = {
            "event": "workflow_finished",
            "data": {
                "id": "run_001",
                "status": "succeeded",
                "outputs": {"plan_result": json.dumps(plan_result)},
                "error": None,
            },
        }
        return StreamingResponse(
            [
                'data: {"event":"workflow_started","data":{"id":"run_001"}}\n',
                f"data: {json.dumps(event)}\n",
            ]
        )

    monkeypatch.setattr("urllib.request.urlopen", return_streaming_workflow)
    client = DifyWorkflowClient(
        api_url="http://example.test", api_key="test-key", response_mode="streaming"
    )

    assert client.run_workflow({"task_id": "task_demo_001"}) == plan_result


def test_streaming_events_are_forwarded_to_event_handler(monkeypatch):
    plan_result = {
        "schema_version": "experiment_planner_plan_result_v1",
        "agent_name": "ExperimentPlannerAgent",
        "task_id": "task_demo_001",
        "iteration": 1,
        "hypothesis_id": "hyp_001",
        "status": "success",
        "error_message": "",
        "plan": {"problem_statement": "demo"},
    }

    def return_streaming_workflow(request, timeout):
        event = {
            "event": "workflow_finished",
            "data": {
                "id": "run_001",
                "status": "succeeded",
                "outputs": {"plan_result": json.dumps(plan_result)},
                "error": None,
            },
        }
        return StreamingResponse(
            [
                'data: {"event":"workflow_started","data":{"id":"run_001"}}\n',
                'data: {"event":"node_started","data":{"title":"Generate"}}\n',
                f"data: {json.dumps(event)}\n",
            ]
        )

    events = []
    monkeypatch.setattr("urllib.request.urlopen", return_streaming_workflow)
    client = DifyWorkflowClient(
        api_url="http://example.test",
        api_key="test-key",
        response_mode="streaming",
        event_handler=events.append,
    )

    assert client.run_workflow({"task_id": "task_demo_001"}) == plan_result
    assert [event["event"] for event in events] == [
        "workflow_started",
        "node_started",
        "workflow_finished",
    ]


def test_streaming_progress_prints_text_chunk_metadata_without_thinking_preview(monkeypatch, capsys):
    monkeypatch.setenv("DIFY_SHOW_TEXT_CHUNKS", "1")
    plan_result = {
        "schema_version": "experiment_planner_plan_result_v1",
        "agent_name": "ExperimentPlannerAgent",
        "task_id": "task_demo_001",
        "iteration": 1,
        "hypothesis_id": "hyp_001",
        "status": "success",
        "error_message": "",
        "plan": {"problem_statement": "demo"},
    }

    def return_streaming_workflow(request, timeout):
        event = {
            "event": "workflow_finished",
            "data": {
                "id": "run_001",
                "status": "succeeded",
                "outputs": {"plan_result": json.dumps(plan_result)},
                "error": None,
            },
        }
        return StreamingResponse(
            [
                'data: {"event":"workflow_started","data":{"id":"run_001"}}\n',
                'data: {"event":"text_chunk","data":{"text":"<think>hidden reasoning"}}\n',
                'data: {"event":"text_chunk","data":{"text":"</think>{\\"schema_version\\":"}}\n',
                f"data: {json.dumps(event)}\n",
            ]
        )

    monkeypatch.setattr("urllib.request.urlopen", return_streaming_workflow)
    client = DifyWorkflowClient(
        api_url="http://example.test",
        api_key="test-key",
        response_mode="streaming",
        show_progress=True,
    )

    assert client.run_workflow({"task_id": "task_demo_001"}) == plan_result
    stderr = capsys.readouterr().err
    assert "text_chunk #1" in stderr
    assert "phase=thinking" in stderr
    assert "text_chunk #2" in stderr
    assert "phase=json" in stderr
    assert "hidden reasoning" not in stderr
    assert "schema_version" in stderr
