from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Protocol


# Shared code must read credentials from environment variables only.
# Configure DASHSCOPE_API_KEY or QWEN_API_KEY, and optionally QWEN_MODEL.
QWEN_API_KEY = ""
QWEN_MODEL = "qwen3.7-max"
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class LLMClient(Protocol):
    def generate_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        expected_schema: str,
    ) -> dict[str, Any]:
        ...


class QwenDashScopeClient:
    """OpenAI-compatible DashScope client for Qwen JSON generation."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int = 180,
    ) -> None:
        self.api_key = (
            api_key
            or os.getenv("DASHSCOPE_API_KEY")
            or os.getenv("QWEN_API_KEY")
            or QWEN_API_KEY
        )
        self.model = model or os.getenv("QWEN_MODEL") or QWEN_MODEL
        self.base_url = (
            base_url
            or os.getenv("DASHSCOPE_BASE_URL")
            or QWEN_BASE_URL
        ).rstrip("/")
        self.timeout_seconds = timeout_seconds

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        expected_schema: str,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError(
                "DASHSCOPE_API_KEY or QWEN_API_KEY is required for QwenDashScopeClient"
            )

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"{system_prompt}\n"
                        f"Return strict JSON only for schema: {expected_schema}. "
                        "Do not include markdown fences or explanatory text."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Qwen request failed: {exc}") from exc

        response_payload = json.loads(raw)
        content = response_payload["choices"][0]["message"]["content"]
        return _parse_json_content(content)


def _parse_json_content(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be a JSON object")
    return parsed
