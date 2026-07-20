"""LLM 客户端（OpenAI 兼容 / 阿里云百炼），与知识整合模块对齐。"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Optional, Protocol


DEFAULT_BASE_URL = (
    "https://ws-7hqgj5wzj4r60zy7.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
)
DEFAULT_MODEL = "qwen3.7-max"


class LLMClient(Protocol):
    def generate_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        expected_schema: str,
    ) -> dict[str, Any]:
        ...


class QwenCompatibleClient:
    """无第三方 openai 包依赖的 JSON 生成客户端。"""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_seconds: int = 90,
    ) -> None:
        self.api_key = (
            api_key
            or os.getenv("DASHSCOPE_API_KEY")
            or os.getenv("QWEN_API_KEY")
            or os.getenv("LLM_API_KEY")
            or ""
        )
        self.model = model or os.getenv("QWEN_MODEL") or os.getenv("LLM_MODEL") or DEFAULT_MODEL
        self.base_url = (
            base_url
            or os.getenv("DASHSCOPE_BASE_URL")
            or os.getenv("LLM_BASE_URL")
            or DEFAULT_BASE_URL
        ).rstrip("/")
        self.timeout_seconds = timeout_seconds

    @property
    def available(self) -> bool:
        key = (self.api_key or "").strip()
        return bool(key) and key not in {"在此填入你的API密钥"} and not key.startswith("填入")

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        expected_schema: str,
    ) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError(
                "未配置 API Key：请设置 DASHSCOPE_API_KEY / QWEN_API_KEY / LLM_API_KEY"
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
            raise RuntimeError(f"LLM request failed: {exc}") from exc

        response_payload = json.loads(raw)
        content = response_payload["choices"][0]["message"]["content"]
        return parse_json_content(content)


def parse_json_content(content: str) -> dict[str, Any]:
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


def resolve_scoring_mode(explicit: Optional[str] = None) -> str:
    """auto | llm | rules。auto：有 Key 用 LLM，否则规则。"""
    mode = (explicit or os.getenv("EVIDENCE_MAPPING_MODE") or "auto").strip().lower()
    if mode not in {"auto", "llm", "rules"}:
        return "auto"
    return mode
