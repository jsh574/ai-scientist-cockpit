from __future__ import annotations

import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from planning_agent.env import ensure_dotenv_loaded


class DifyWorkflowError(RuntimeError):
    """Raised when the configured Dify Workflow cannot return usable output."""


StreamEventHandler = Callable[[dict[str, Any]], None]
_KNOWN_OUTPUT_KEYS = ("plan_result", "research_plan", "result", "output", "text")
_EXPECTED_SCHEMA_VERSIONS = {
    "experiment_planner_plan_result_v1",
    "experiment_planner_output_v1",
}
_TEXT_CHUNK_KEYS = ("text", "chunk", "answer", "delta", "content")


class DifyWorkflowClient:
    def __init__(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
        user: str | None = None,
        timeout_seconds: int | None = None,
        response_mode: str | None = None,
        event_handler: StreamEventHandler | None = None,
        show_progress: bool | None = None,
        cancellation_checker: Callable[[], None] | None = None,
    ) -> None:
        ensure_dotenv_loaded()
        self.api_url = (api_url or os.getenv("DIFY_API_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("DIFY_API_KEY", "")
        self.user = user or os.getenv("DIFY_USER", "research-planning-agent")
        self.timeout_seconds = timeout_seconds or _env_int("DIFY_TIMEOUT_SECONDS", 180)
        self.response_mode = response_mode or os.getenv("DIFY_RESPONSE_MODE", "blocking")
        progress_enabled = _env_bool("DIFY_SHOW_PROGRESS", False) if show_progress is None else show_progress
        self.event_handler = event_handler or (_StreamProgressPrinter() if progress_enabled else None)
        self.cancellation_checker = cancellation_checker

    @property
    def configured(self) -> bool:
        return bool(self.api_url and self.api_key)

    def run_workflow(self, inputs: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise DifyWorkflowError("Dify workflow is not configured.")

        endpoint = f"{self.api_url}/v1/workflows/run"
        payload = {
            "inputs": inputs,
            "response_mode": self.response_mode,
            "user": self.user,
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                if self.response_mode == "streaming":
                    response_data = _read_streaming_response(
                        response, self.event_handler, self.cancellation_checker
                    )
                else:
                    with _cancellation_watch(response, self.cancellation_checker):
                        raw = response.read().decode("utf-8")
                    response_data = _parse_response_json(raw)
        except urllib.error.HTTPError as exc:
            detail = _http_error_detail(exc)
            raise DifyWorkflowError(f"Dify request to {endpoint} failed: {detail}") from exc
        except TimeoutError as exc:
            raise DifyWorkflowError(
                f"Dify request to {endpoint} timed out after {self.timeout_seconds}s"
            ) from exc
        except urllib.error.URLError as exc:
            raise DifyWorkflowError(f"Dify request to {endpoint} failed: {exc}") from exc

        _raise_for_workflow_failure(response_data)
        return _coerce_output_to_dict(_extract_output(response_data))


def _read_streaming_response(
    response: Any,
    event_handler: StreamEventHandler | None = None,
    cancellation_checker: Callable[[], None] | None = None,
) -> dict[str, Any]:
    last_event: dict[str, Any] | None = None
    with _cancellation_watch(response, cancellation_checker):
        for raw_line in response:
            if cancellation_checker:
                cancellation_checker()
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data_text = line.removeprefix("data:").strip()
            if data_text == "[DONE]":
                break
            try:
                event = json.loads(data_text)
            except json.JSONDecodeError:
                continue
            last_event = event
            if event_handler:
                event_handler(event)
            if event.get("event") in {"workflow_finished", "workflow_failed"}:
                return event
    if last_event:
        return last_event
    raise DifyWorkflowError("Dify streaming response ended without workflow result.")


@contextmanager
def _cancellation_watch(
    response: Any, cancellation_checker: Callable[[], None] | None
):
    if cancellation_checker is None:
        yield
        return
    stopped = threading.Event()
    cancelled = threading.Event()

    def watch() -> None:
        while not stopped.wait(0.1):
            try:
                cancellation_checker()
            except BaseException:
                cancelled.set()
                try:
                    response.close()
                except Exception:
                    pass
                return

    thread = threading.Thread(target=watch, name="dify-cancel-watch", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=0.3)
        if cancelled.is_set():
            cancellation_checker()


def _parse_response_json(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DifyWorkflowError("Dify returned non-JSON response.") from exc


def _raise_for_workflow_failure(response_data: dict[str, Any]) -> None:
    data = response_data.get("data", {})
    if not isinstance(data, dict):
        return
    status = data.get("status")
    error = data.get("error")
    if status in {"failed", "stopped"} or error or response_data.get("event") == "workflow_failed":
        run_id = data.get("id") or response_data.get("workflow_run_id") or "unknown"
        detail = error or f"status={status}"
        raise DifyWorkflowError(f"Dify workflow run failed: run_id={run_id}; {detail}")


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    body = ""
    try:
        body = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        body = ""
    status = f"HTTP {exc.code} {exc.reason}"
    if body:
        return f"{status}: {_truncate(body)}"
    return status


def _truncate(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<truncated>"


def _extract_output(response_data: dict[str, Any]) -> Any:
    data = response_data.get("data", {})
    outputs = data.get("outputs", {}) if isinstance(data, dict) else {}
    for key in _KNOWN_OUTPUT_KEYS:
        if key in outputs:
            return outputs[key]
    return outputs


def _coerce_output_to_dict(output: Any) -> dict[str, Any]:
    if isinstance(output, dict):
        nested = _nested_known_output(output)
        if nested is not None:
            return _coerce_output_to_dict(nested)
        return output
    if isinstance(output, str):
        return _parse_json_text(output)
    raise DifyWorkflowError("Dify response did not contain a supported output.")


def _nested_known_output(output: dict[str, Any]) -> Any | None:
    if _looks_like_planning_output(output):
        return None
    for key in _KNOWN_OUTPUT_KEYS:
        if key in output and isinstance(output[key], (dict, str)):
            return output[key]
    return None


def _looks_like_planning_output(value: dict[str, Any]) -> bool:
    schema_version = value.get("schema_version")
    return schema_version in _EXPECTED_SCHEMA_VERSIONS or "plan" in value or "plans" in value


def _parse_json_text(text: str) -> dict[str, Any]:
    cleaned = _clean_model_text(text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        parsed = _extract_json_object(cleaned)
    if not isinstance(parsed, dict):
        raise DifyWorkflowError("Dify output JSON root is not an object.")
    return parsed


def _clean_model_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    cleaned = re.sub(r"(?is)<think>.*?</think>", "", cleaned).strip()
    return cleaned


def _extract_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            if _looks_like_planning_output(parsed):
                return parsed
            candidates.append(parsed)
    if candidates:
        return candidates[-1]
    preview = _truncate(text.replace("\n", "\\n"), limit=500)
    raise DifyWorkflowError(f"Dify output could not be parsed as JSON. Output preview: {preview}")


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


class _StreamProgressPrinter:
    def __init__(self) -> None:
        self.text_events = 0
        self.text_chars = 0
        self.in_thinking = False
        self.json_started = False
        self.show_text_preview = _env_bool("DIFY_SHOW_TEXT_CHUNKS", False)

    def __call__(self, event: dict[str, Any]) -> None:
        event_name = str(event.get("event", "event"))
        if event_name == "text_chunk":
            self._print_text_chunk(event)
            return
        self._print_node_event(event_name, event)

    def _print_text_chunk(self, event: dict[str, Any]) -> None:
        chunk = _extract_text_chunk(event)
        self.text_events += 1
        self.text_chars += len(chunk)
        visible_chunk, phase = self._visible_chunk_and_phase(chunk)
        parts = [
            "[dify]",
            "text_chunk",
            f"#{self.text_events}",
            f"+{len(chunk)} chars",
            f"total={self.text_chars}",
            f"phase={phase}",
        ]
        preview = _chunk_preview(visible_chunk)
        if self.show_text_preview and preview:
            parts.append(f"preview={json.dumps(preview, ensure_ascii=False)}")
        print(" ".join(parts), file=sys.stderr, flush=True)

    def _visible_chunk_and_phase(self, chunk: str) -> tuple[str, str]:
        if not chunk:
            return "", "empty"
        visible_parts: list[str] = []
        remaining = chunk
        while remaining:
            lowered = remaining.lower()
            if self.in_thinking:
                end_index = lowered.find("</think>")
                if end_index == -1:
                    return "", "thinking"
                remaining = remaining[end_index + len("</think>") :]
                self.in_thinking = False
                continue

            start_index = lowered.find("<think")
            if start_index == -1:
                visible_parts.append(remaining)
                break

            visible_parts.append(remaining[:start_index])
            tag_end_index = lowered.find(">", start_index)
            if tag_end_index == -1:
                self.in_thinking = True
                break
            remaining = remaining[tag_end_index + 1 :]
            self.in_thinking = True

        visible = "".join(visible_parts)
        if "{" in visible:
            self.json_started = True
        if self.json_started:
            return visible, "json"
        if visible.strip():
            return visible, "answer"
        if self.in_thinking:
            return "", "thinking"
        return "", "empty"

    def _print_node_event(self, event_name: str, event: dict[str, Any]) -> None:
        data = event.get("data", {})
        if not isinstance(data, dict):
            data = {}
        title = data.get("title") or data.get("node_title") or data.get("node_type")
        status = data.get("status")
        error = data.get("error")
        elapsed = data.get("elapsed_time") or data.get("elapsed")
        parts = ["[dify]", event_name]
        if title:
            parts.append(str(title))
        if status:
            parts.append(f"status={status}")
        if elapsed is not None:
            parts.append(f"elapsed={elapsed}s")
        if error:
            parts.append(f"error={_truncate(str(error), 240)}")
        print(" ".join(parts), file=sys.stderr, flush=True)


def _extract_text_chunk(event: dict[str, Any]) -> str:
    data = event.get("data", {})
    if isinstance(data, dict):
        for key in _TEXT_CHUNK_KEYS:
            value = data.get(key)
            if isinstance(value, str):
                return value
        for value in data.values():
            if isinstance(value, dict):
                nested = _first_string_value(value, _TEXT_CHUNK_KEYS)
                if nested is not None:
                    return nested
    for key in _TEXT_CHUNK_KEYS:
        value = event.get(key)
        if isinstance(value, str):
            return value
    return ""


def _first_string_value(data: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            return value
    return None


def _chunk_preview(text: str, limit: int = 80) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return ""
    return _truncate(cleaned, limit)
