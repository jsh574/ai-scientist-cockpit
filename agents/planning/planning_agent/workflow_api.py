from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from planning_agent.env import ensure_dotenv_loaded


class DifyWorkflowAPIError(RuntimeError):
    """Raised when a generic Dify Workflow request cannot produce End outputs."""


WorkflowEventHandler = Callable[[str, dict[str, Any]], None]
CancellationChecker = Callable[[], None]


@dataclass(frozen=True)
class WorkflowEndpointConfig:
    name: str
    api_url: str
    api_key: str
    user: str = "research-planning-agent-chain-test"
    response_mode: str = "streaming"
    timeout_seconds: int = 300

    @property
    def configured(self) -> bool:
        return bool(self.api_url and self.api_key)

    @property
    def endpoint(self) -> str:
        base = self.api_url.rstrip("/")
        if base.endswith("/v1/workflows/run"):
            return base
        if base.endswith("/v1"):
            return f"{base}/workflows/run"
        return f"{base}/v1/workflows/run"

    def public_summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "configured": self.configured,
            "endpoint": self.endpoint if self.api_url else "",
            "api_key_present": bool(self.api_key),
            "user": self.user,
            "response_mode": self.response_mode,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_env(cls, workflow: str) -> WorkflowEndpointConfig:
        ensure_dotenv_loaded()
        stage = workflow.upper()
        prefix = f"DIFY_WORKFLOW_{stage}"
        api_url = os.getenv(f"{prefix}_API_URL") or os.getenv("DIFY_API_URL", "")
        api_key = os.getenv(f"{prefix}_API_KEY", "")
        response_mode = (
            (
                os.getenv("DIFY_CHAIN_RESPONSE_MODE")
                or os.getenv("DIFY_RESPONSE_MODE")
                or "streaming"
            )
            .strip()
            .lower()
        )
        if response_mode not in {"blocking", "streaming"}:
            response_mode = "streaming"
        timeout_seconds = _env_int(
            "DIFY_CHAIN_TIMEOUT_SECONDS",
            _env_int("DIFY_TIMEOUT_SECONDS", 300),
        )
        return cls(
            name=f"workflow_{stage.lower()}",
            api_url=api_url.rstrip("/"),
            api_key=api_key,
            user=os.getenv("DIFY_CHAIN_USER") or os.getenv("DIFY_USER") or cls.user,
            response_mode=response_mode,
            timeout_seconds=max(1, timeout_seconds),
        )


@dataclass(frozen=True)
class WorkflowRunResult:
    workflow: str
    workflow_run_id: str
    task_id: str
    status: str
    elapsed_time: float | None
    total_tokens: int | None
    outputs: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class GenericDifyWorkflowClient:
    """Dify Workflow API client that preserves every End-node output."""

    def __init__(
        self,
        config: WorkflowEndpointConfig,
        event_handler: WorkflowEventHandler | None = None,
        cancellation_checker: CancellationChecker | None = None,
    ) -> None:
        self.config = config
        self.event_handler = event_handler
        self.cancellation_checker = cancellation_checker

    @property
    def configured(self) -> bool:
        return self.config.configured

    def run(
        self, inputs: dict[str, Any], event_context: dict[str, Any] | None = None
    ) -> WorkflowRunResult:
        if self.cancellation_checker:
            self.cancellation_checker()
        if not self.configured:
            raise DifyWorkflowAPIError(
                f"{self.config.name} is not configured; set its API URL and API key."
            )
        payload = {
            "inputs": inputs,
            "response_mode": self.config.response_mode,
            "user": self.config.user,
        }
        request = urllib.request.Request(
            self.config.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                if self.config.response_mode == "streaming":
                    response_data = self._read_stream(response, event_context)
                else:
                    response_data = _parse_response_json(
                        response.read().decode("utf-8", errors="replace")
                    )
        except urllib.error.HTTPError as exc:
            raise DifyWorkflowAPIError(
                f"{self.config.name} request failed: {_http_error_detail(exc)}"
            ) from exc
        except TimeoutError as exc:
            raise DifyWorkflowAPIError(
                f"{self.config.name} timed out after {self.config.timeout_seconds}s"
            ) from exc
        except urllib.error.URLError as exc:
            raise DifyWorkflowAPIError(
                f"{self.config.name} request failed: {exc.reason or exc}"
            ) from exc

        if self.cancellation_checker:
            self.cancellation_checker()
        return _normalize_result(self.config.name, response_data)

    def _read_stream(
        self, response: Any, event_context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        last_event: dict[str, Any] | None = None
        for raw_line in response:
            if self.cancellation_checker:
                self.cancellation_checker()
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data_text = line.removeprefix("data:").strip()
            if data_text == "[DONE]":
                break
            try:
                event = json.loads(data_text)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            last_event = event
            if self.event_handler:
                handler_event = event
                if event_context:
                    handler_event = dict(event)
                    handler_event["planning_context"] = dict(event_context)
                self.event_handler(self.config.name, handler_event)
            if event.get("event") in {"workflow_finished", "workflow_failed"}:
                return event
        if last_event is not None:
            return last_event
        raise DifyWorkflowAPIError(
            f"{self.config.name} streaming response ended without a workflow result."
        )


def decode_json_output(value: Any) -> Any:
    """Decode JSON End outputs while preserving ordinary scalar text."""
    if not isinstance(value, str):
        return value
    cleaned = re.sub(r"(?is)<think>.*?</think>", "", value).strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    if not cleaned or cleaned[0] not in "[{":
        return value
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return value


def _normalize_result(workflow: str, response_data: dict[str, Any]) -> WorkflowRunResult:
    data = response_data.get("data")
    if not isinstance(data, dict):
        data = {}
    event = str(response_data.get("event") or "")
    status = str(data.get("status") or ("failed" if event == "workflow_failed" else ""))
    error = data.get("error")
    if status in {"failed", "stopped"} or error or event == "workflow_failed":
        run_id = data.get("id") or response_data.get("workflow_run_id") or "unknown"
        detail = str(error or f"status={status or 'failed'}")
        raise DifyWorkflowAPIError(
            f"{workflow} run failed: workflow_run_id={run_id}; {_truncate(detail, 1000)}"
        )
    outputs = data.get("outputs")
    if not isinstance(outputs, dict):
        raise DifyWorkflowAPIError(f"{workflow} response did not contain data.outputs.")
    decoded_outputs = {key: decode_json_output(value) for key, value in outputs.items()}
    elapsed = data.get("elapsed_time")
    tokens = data.get("total_tokens")
    return WorkflowRunResult(
        workflow=workflow,
        workflow_run_id=str(data.get("id") or response_data.get("workflow_run_id") or ""),
        task_id=str(response_data.get("task_id") or data.get("task_id") or ""),
        status=status or "succeeded",
        elapsed_time=float(elapsed) if isinstance(elapsed, int | float) else None,
        total_tokens=int(tokens)
        if isinstance(tokens, int) and not isinstance(tokens, bool)
        else None,
        outputs=decoded_outputs,
    )


def _parse_response_json(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DifyWorkflowAPIError("Dify returned a non-JSON response.") from exc
    if not isinstance(data, dict):
        raise DifyWorkflowAPIError("Dify response JSON root is not an object.")
    return data


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        body = ""
    status = f"HTTP {exc.code} {exc.reason}"
    return f"{status}: {_truncate(body, 2000)}" if body else status


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}...<truncated>"


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default
