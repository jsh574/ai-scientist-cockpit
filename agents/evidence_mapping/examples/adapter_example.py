"""总控 Agent Adapter 示例（可直接拷贝到 backend/app/agents/）。"""

from __future__ import annotations

from typing import Any

from evidence_mapping import EvidenceMappingAgent


class EvidenceMappingAdapter:
    """把总控裁剪后的 input_slice 转成统一 AgentResponse。"""

    stage = "evidence_mapping"

    def __init__(self) -> None:
        self._agent = EvidenceMappingAgent()

    def call(self, input_slice: dict[str, Any]) -> dict[str, Any]:
        # 兼容总控两种传法：扁平 或 { "input": {...} }
        payload = input_slice.get("input", input_slice)
        data = {
            "task_id": input_slice.get("task_id", payload.get("task_id", "task_001")),
            "stage": "evidence_mapping",
            "iteration": input_slice.get("iteration", payload.get("iteration", 1)),
            "threshold": payload.get("threshold", 7.0),
            "hypothesis_cards": payload.get("hypothesis_cards", []),
            "evidence_cards": payload.get("evidence_cards", []),
            "literature_cards": payload.get("literature_cards", []),
        }
        return self._agent.run_dict(data)
