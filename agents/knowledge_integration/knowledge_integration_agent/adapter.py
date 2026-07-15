from __future__ import annotations

from typing import Any

from .agent import KnowledgeIntegrationAgent


class KnowledgeIntegrationAdapter:
    stage = "knowledge_integration"
    output_schema = "knowledge_integration.schema.json"

    def __init__(
        self,
        agent: KnowledgeIntegrationAgent | None = None,
        default_search_policy: dict[str, Any] | None = None,
    ) -> None:
        self.agent = agent or KnowledgeIntegrationAgent()
        self.default_search_policy = default_search_policy or {
            "max_papers": 20,
            "min_recent_papers": 5,
            "must_verify_sources": True,
            "forbidden_actions": ["invent_references", "invent_dataset_url"],
        }

    def build_request(self, task_context: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": task_context.get("task_id", ""),
            "stage": self.stage,
            "iteration": int(task_context.get("iteration", 1)),
            "input": {
                "question_card": task_context.get("question_card"),
                "search_policy": dict(self.default_search_policy),
            },
            "output_schema": self.output_schema,
        }

    def call(self, task_context: dict[str, Any]) -> dict[str, Any]:
        return self.agent.run(self.build_request(task_context))
