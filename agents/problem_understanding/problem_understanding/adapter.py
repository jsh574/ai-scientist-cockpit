"""与团队其他 Agent 一致的总控适配器。"""
from __future__ import annotations

from typing import Any

from .agent import ProblemUnderstandingAgent
from .schema import INPUT_SCHEMA_VERSION


class ProblemUnderstandingAdapter:
    stage = "question_understanding"
    input_schema = INPUT_SCHEMA_VERSION
    output_schema = "problem_understanding_output_v1"

    def __init__(self, agent: ProblemUnderstandingAgent | None = None) -> None:
        self.agent = agent or ProblemUnderstandingAgent()

    def build_request(self, task_context: dict[str, Any]) -> dict[str, Any]:
        user_input = dict(task_context.get("user_input") or {})
        feedback = str(
            task_context.get("_feedback")
            or task_context.get("feedback")
            or task_context.get("_fedback")
            or ""
        ).strip()
        return {
            "schema_version": self.input_schema,
            "task_id": str(task_context.get("task_id") or ""),
            "stage": self.stage,
            "iteration": int(task_context.get("iteration") or 1),
            "_feedback": feedback,
            "input": user_input,
        }

    def call(self, task_context: dict[str, Any]) -> dict[str, Any]:
        return self.agent.run_protocol(self.build_request(task_context))
