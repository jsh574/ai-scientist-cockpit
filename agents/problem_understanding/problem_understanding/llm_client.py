"""LLM 客户端：支持多 provider 故障切换 + 并行调度。

- 多 provider：按顺序尝试，一个失败自动切换下一个。
- mock 模式：所有 provider 都没有有效 key 时自动降级。
- 并行：由上层 run_batch.py 通过线程池实现并发调用。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import List, Optional


class LLMProvider:
    """单个 LLM 服务端点。"""

    def __init__(self, name: str, api_key: str, base_url: str, model: str,
                 retry_count: int = 2, timeout: int = 60):
        self.name = name
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.retry_count = retry_count
        self.timeout = timeout
        self._client = None
        self.fail_count = 0

    @property
    def available(self) -> bool:
        return bool(self.api_key) and self.api_key != "在此填入你的API密钥" and not self.api_key.startswith("填入")

    def _lazy_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._client

    def chat_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.2) -> dict:
        client = self._lazy_client()
        last_err = None
        for attempt in range(self.retry_count + 1):
            try:
                resp = client.chat.completions.create(
                    model=self.model,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                content = resp.choices[0].message.content or "{}"
                self.fail_count = 0
                return _safe_json(content)
            except Exception as e:
                last_err = e
                if attempt < self.retry_count:
                    time.sleep(1.5 * (attempt + 1))
        self.fail_count += 1
        raise last_err


class LLMClient:
    """多 provider 客户端，自动 fallback。"""

    def __init__(
        self,
        providers: Optional[List[dict]] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        mock: Optional[bool] = None,
        retry_count: int = 2,
        timeout: int = 60,
    ):
        self._providers: List[LLMProvider] = []
        self.retry_count = retry_count
        self.timeout = timeout

        if providers:
            for p in providers:
                prov = LLMProvider(
                    name=p.get("name", "unnamed"),
                    api_key=p.get("api_key", ""),
                    base_url=p.get(
                        "base_url",
                        "https://ws-7hqgj5wzj4r60zy7.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
                    ),
                    model=p.get("model", "qwen3.7-max"),
                    retry_count=retry_count,
                    timeout=timeout,
                )
                if prov.available:
                    self._providers.append(prov)
        else:
            key = api_key or os.getenv("LLM_API_KEY", "")
            url = base_url or os.getenv(
                "LLM_BASE_URL",
                "https://ws-7hqgj5wzj4r60zy7.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            )
            mdl = model or os.getenv("LLM_MODEL", "qwen3.7-max")
            prov = LLMProvider(
                name="default",
                api_key=key,
                base_url=url,
                model=mdl,
                retry_count=retry_count,
                timeout=timeout,
            )
            if prov.available:
                self._providers.append(prov)

        if mock is not None:
            self.mock = mock
        else:
            self.mock = len(self._providers) == 0

        self.model = self._providers[0].model if self._providers else "mock"

    @property
    def provider_names(self) -> List[str]:
        return [p.name for p in self._providers]

    def chat_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.2) -> dict:
        if self.mock:
            return _mock_generate(user_prompt)

        last_err = None
        for provider in self._providers:
            try:
                result = provider.chat_json(system_prompt, user_prompt, temperature)
                return result
            except Exception as e:
                last_err = e
                print(f"    [warn] provider '{provider.name}' 失败: {e}, 尝试下一个...")
                continue

        print(f"    [error] 所有 provider 均失败，降级为 mock")
        return _mock_generate(user_prompt)


def _safe_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def _extract_question(user_prompt: str) -> str:
    m = re.search(r"<<<(.+?)>>>", user_prompt, re.DOTALL)
    return m.group(1).strip() if m else user_prompt.strip()


def _guess_type(q: str) -> str:
    ql = q.lower()
    if ql.startswith(("why", "how", "为什么", "如何")) or "mechanism" in ql or "机制" in q:
        return "mechanism"
    if ql.startswith(("will", "can", "could", "是否", "能否")):
        return "predictive"
    if ql.startswith(("what", "什么", "是什么")):
        return "descriptive"
    if "compare" in ql or "比较" in q:
        return "comparative"
    return "mechanism"


def _mock_generate(user_prompt: str) -> dict:
    q = _extract_question(user_prompt)
    words = [w for w in re.split(r"[\s,，。？?]+", q) if len(w) > 1][:4]
    kw = words or ["topic"]
    return {
        "core_question": f"针对「{q}」的核心机制/规律是什么，及其可验证依据？",
        "question_type": _guess_type(q),
        "domain": ["跨学科"],
        "research_object": kw[0],
        "context": {"region": None, "time_scale": None,
                    "spatial_scale": None, "conditions": []},
        "key_concepts": kw,
        "key_variables": [
            {"name": kw[0], "role": "independent", "category": "factor"},
            {"name": "观测结果", "role": "outcome", "category": "outcome"},
        ],
        "sub_questions": [
            f"{kw[0]} 与目标现象是否存在因果关系？",
            f"影响 {kw[0]} 的主要条件有哪些？",
            "现有证据能在多大程度上支持该机制？",
        ],
        "research_scope": {
            "included": ["机制解释", "候选假设生成", "可验证研究方案"],
            "excluded": ["泛泛的研究建议", "无法检验的猜想"],
        },
        "search_keywords": kw + [q[:40]],
        "verifiability": {
            "is_verifiable": True,
            "type": "observational+experimental",
            "checkpoints": [
                "是否存在可测量的关键变量",
                "是否可通过实验或观测数据判定假设真伪",
            ],
        },
        "assumptions": [
            {"point": "默认聚焦机制解释而非纯哲学讨论",
             "default_choice": "机制解释", "need_human": False}
        ],
        "confidence": 0.6,
    }
