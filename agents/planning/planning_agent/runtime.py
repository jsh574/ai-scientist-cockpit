from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4

from planning_agent.env import ensure_dotenv_loaded

ExecutionEventHandler = Callable[[str, dict[str, Any]], None]
CancellationChecker = Callable[[], None]


class PlanningExecutionError(RuntimeError):
    """Raised when a local Planning stage cannot produce validated outputs."""


class PlanningFormatError(PlanningExecutionError):
    """Raised when a model response is not a parseable JSON object."""


@dataclass(frozen=True)
class PlanningLLMConfig:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 120
    max_retries: int = 0
    max_completion_tokens: int = 8192
    thinking_enabled: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def public_summary(self, name: str) -> dict[str, Any]:
        return {
            "name": name,
            "configured": self.configured,
            "mode": "local_protocol_compiler",
            "provider": "bailian_openai_compatible",
            "model": self.model,
            "base_url": self.base_url,
            "api_key_present": bool(self.api_key),
            "thinking_enabled": self.thinking_enabled
            and name not in {"synthesis", "repair"},
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "max_completion_tokens": self.max_completion_tokens,
            "max_hypotheses": max(1, min(3, _env_int("PLANNING_MAX_HYPOTHESES", 2))),
            "max_parallel_calls": max(
                1, min(8, _env_int("PLANNING_MAX_PARALLEL_CALLS", 1))
            ),
            "max_repair_attempts": max(
                0, min(1, _planning_repair_attempts())
            ),
            "synthesis_context_max_chars": max(4000, _planning_context_limit()),
            "stages": [
                "draft",
                "review_methodology",
                "review_statistics",
                "review_feasibility",
                "synthesis",
                "repair_optional",
            ],
        }

    @classmethod
    def from_env(
        cls, model_policy: Mapping[str, Any] | None = None
    ) -> PlanningLLMConfig:
        ensure_dotenv_loaded()
        policy = dict(model_policy or {})
        configured_tokens = _env_int("LLM_MAX_TOKENS", 8192)
        return cls(
            api_key=(
                os.getenv("DASHSCOPE_API_KEY")
                or os.getenv("QWEN_API_KEY")
                or os.getenv("LLM_API_KEY")
                or ""
            ),
            base_url=(
                os.getenv("DASHSCOPE_BASE_URL")
                or os.getenv("LLM_BASE_URL")
                or "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ).rstrip("/"),
            model=str(
                policy.get("model")
                or os.getenv("PLANNING_MODEL")
                or os.getenv("QWEN_MODEL")
                or os.getenv("LLM_MODEL")
                or "qwen3.7-max"
            ),
            timeout_seconds=float(
                policy.get("timeout_seconds")
                or os.getenv("LLM_TIMEOUT_SECONDS", "120")
            ),
            max_retries=max(
                0,
                int(
                    policy.get("max_retries")
                    if policy.get("max_retries") is not None
                    else _env_int("PLANNING_MAX_RETRIES", 0)
                ),
            ),
            max_completion_tokens=max(
                256,
                int(policy.get("max_tokens") or configured_tokens),
            ),
            thinking_enabled=_as_bool(
                policy.get("thinking_enabled"),
                _env_bool("QWEN_ENABLE_THINKING", False),
            ),
        )


@dataclass(frozen=True)
class ModelCallResult:
    value: dict[str, Any]
    run_id: str
    elapsed_time: float
    total_tokens: int | None
    output_chars: int


@dataclass(frozen=True)
class StageRunResult:
    stage: str
    run_id: str
    request_id: str
    status: str
    elapsed_time: float | None
    total_tokens: int | None
    outputs: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def workflow_run_id(self) -> str:
        return self.run_id

    @property
    def task_id(self) -> str:
        return self.request_id


class PlanningLLMClient:
    def __init__(
        self,
        config: PlanningLLMConfig,
        event_handler: ExecutionEventHandler | None = None,
        cancellation_checker: CancellationChecker | None = None,
        sdk_client: Any | None = None,
    ) -> None:
        self.config = config
        self.event_handler = event_handler
        self.cancellation_checker = cancellation_checker
        self._sdk_client = sdk_client

    @property
    def configured(self) -> bool:
        return self.config.configured

    def complete_json(
        self,
        *,
        stage: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        event_context: dict[str, Any],
        allow_thinking: bool,
    ) -> ModelCallResult:
        if not self.configured:
            raise PlanningExecutionError(
                "Planning model is not configured; set DASHSCOPE_API_KEY and a Qwen model."
            )
        self._check_cancelled()
        run_id = f"plan_{uuid4().hex[:16]}"
        self._emit(stage, "stage_started", event_context, run_id=run_id)
        last_error: Exception | None = None
        for attempt in range(1, self.config.max_retries + 2):
            started = time.monotonic()
            stream = None
            try:
                stream = self._client().chat.completions.create(
                    model=self.config.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=temperature,
                    max_tokens=self.config.max_completion_tokens,
                    extra_body={
                        "enable_thinking": bool(
                            allow_thinking and self.config.thinking_enabled
                        )
                    },
                    stream=True,
                    stream_options={"include_usage": True},
                )
                content: list[str] = []
                output_chars = 0
                total_tokens: int | None = None
                for chunk in stream:
                    self._check_cancelled()
                    usage = getattr(chunk, "usage", None)
                    usage_tokens = getattr(usage, "total_tokens", None)
                    if isinstance(usage_tokens, int):
                        total_tokens = usage_tokens
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    delta = getattr(choices[0], "delta", None)
                    text = getattr(delta, "content", None)
                    if not isinstance(text, str) or not text:
                        continue
                    content.append(text)
                    output_chars += len(text)
                    self._emit(
                        stage,
                        "model_stream_progress",
                        event_context,
                        run_id=run_id,
                        output_chars=output_chars,
                    )
                raw = "".join(content).strip()
                value = parse_json_object(raw)
                elapsed = round(time.monotonic() - started, 3)
                return ModelCallResult(
                    value=value,
                    run_id=run_id,
                    elapsed_time=elapsed,
                    total_tokens=total_tokens,
                    output_chars=output_chars,
                )
            except BaseException as exc:
                if not isinstance(exc, Exception):
                    self._close_stream(stream)
                    raise
                last_error = exc
                if isinstance(exc, PlanningFormatError):
                    safe_error = _safe_error(exc, self.config.api_key)
                    self._emit(
                        stage,
                        "stage_failed",
                        event_context,
                        run_id=run_id,
                        attempt=attempt,
                        error=safe_error,
                    )
                    raise
                should_retry = (
                    attempt <= self.config.max_retries
                    and _is_retryable_transport_error(exc)
                )
                if not should_retry:
                    safe_error = _safe_error(exc, self.config.api_key)
                    self._emit(
                        stage,
                        "stage_failed",
                        event_context,
                        run_id=run_id,
                        attempt=attempt,
                        error=safe_error,
                    )
                    raise PlanningExecutionError(
                        f"Planning stage {stage} failed after {attempt} attempt(s): {safe_error}"
                    ) from exc
            finally:
                self._close_stream(stream)
        raise PlanningExecutionError(
            f"Planning stage {stage} failed: "
            f"{_safe_error(last_error or RuntimeError('unknown'), self.config.api_key)}"
        )

    def emit_finished(
        self,
        stage: str,
        context: dict[str, Any],
        result: ModelCallResult,
    ) -> None:
        self._emit(
            stage,
            "stage_finished",
            context,
            run_id=result.run_id,
            output_chars=result.output_chars,
            total_tokens=result.total_tokens,
            elapsed_time=result.elapsed_time,
        )

    def emit_validation_failed(
        self,
        stage: str,
        context: dict[str, Any],
        result: ModelCallResult,
        issues: list[str],
    ) -> None:
        self._emit(
            stage,
            "stage_failed",
            context,
            run_id=result.run_id,
            output_chars=result.output_chars,
            total_tokens=result.total_tokens,
            elapsed_time=result.elapsed_time,
            error="Structured output failed deterministic validation.",
            issue_count=len(issues),
        )

    def _client(self) -> Any:
        if self._sdk_client is not None:
            return self._sdk_client
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise PlanningExecutionError(
                "The openai package is required; install backend/requirements.txt."
            ) from exc
        self._sdk_client = OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout_seconds,
            max_retries=0,
        )
        return self._sdk_client

    def _check_cancelled(self) -> None:
        if self.cancellation_checker:
            self.cancellation_checker()

    def _emit(
        self,
        stage: str,
        event_name: str,
        context: dict[str, Any],
        **payload: Any,
    ) -> None:
        if not self.event_handler:
            return
        self.event_handler(
            stage,
            {
                "event": event_name,
                "stage": stage,
                **context,
                **payload,
            },
        )

    @staticmethod
    def _close_stream(stream: Any) -> None:
        close = getattr(stream, "close", None)
        if callable(close):
            close()


def parse_json_object(raw: str) -> dict[str, Any]:
    cleaned = re.sub(r"(?is)<think>.*?</think>", "", raw).strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    if not cleaned:
        raise PlanningFormatError("Planning model returned empty content.")
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise PlanningFormatError("Planning model returned invalid JSON.") from exc
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError as nested:
            raise PlanningFormatError("Planning model returned invalid JSON.") from nested
    if not isinstance(value, dict):
        raise PlanningFormatError("Planning model must return a JSON object.")
    return value


def _safe_error(exc: Exception, api_key: str = "") -> str:
    name = type(exc).__name__
    # Never forward provider error bodies: they may echo request content. Only
    # locally controlled validation errors and non-content metadata are exposed.
    if isinstance(exc, PlanningExecutionError):
        text = str(exc).replace("\r", " ").replace("\n", " ").strip()
        return f"{name}: {text[:500]}" if text else name
    fields = [name]
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        fields.append(f"status={status_code}")
    request_id = getattr(exc, "request_id", None)
    if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,160}", request_id):
        fields.append(f"request_id={request_id}")
    return " ".join(fields)


def _is_retryable_transport_error(exc: Exception) -> bool:
    if isinstance(exc, PlanningExecutionError):
        return False
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    status_code = getattr(exc, "status_code", None)
    if status_code in {408, 409, 429, 500, 502, 503, 504}:
        return True
    return type(exc).__name__ in {
        "APITimeoutError",
        "APIConnectionError",
        "RateLimitError",
        "InternalServerError",
    }


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _planning_repair_attempts() -> int:
    if os.getenv("PLANNING_MAX_REPAIR_ATTEMPTS") is not None:
        return _env_int("PLANNING_MAX_REPAIR_ATTEMPTS", 1)
    return _env_int("PLANNING_SELECTOR_MAX_FORMAT_RETRIES", 1)


def _planning_context_limit() -> int:
    if os.getenv("PLANNING_SYNTHESIS_CONTEXT_MAX_CHARS") is not None:
        return _env_int("PLANNING_SYNTHESIS_CONTEXT_MAX_CHARS", 16000)
    return _env_int("PLANNING_FINAL_CONTEXT_MAX_CHARS", 16000)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
