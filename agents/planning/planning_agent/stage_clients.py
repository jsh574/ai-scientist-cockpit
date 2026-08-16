from __future__ import annotations

from typing import Any

from planning_agent.local_nodes import (
    guard_final_plan,
    guard_protocol_draft,
    guard_protocol_review,
)
from planning_agent.prompts import (
    PROTOCOL_DRAFT_SYSTEM_PROMPT,
    REPAIR_SYSTEM_PROMPT,
    REVIEW_SYSTEM_PROMPTS,
    SYNTHESIS_SYSTEM_PROMPT,
    protocol_draft_user_prompt,
    protocol_review_user_prompt,
    repair_user_prompt,
    synthesis_user_prompt,
)
from planning_agent.runtime import PlanningLLMClient, PlanningLLMConfig, StageRunResult


class LocalPlanningProtocolClient:
    """Structured model stages for the local protocol-compiler workflow."""

    def __init__(self, llm: PlanningLLMClient, config: PlanningLLMConfig) -> None:
        self.llm = llm
        self.config = config

    @property
    def configured(self) -> bool:
        return self.llm.configured

    def run(
        self,
        stage: str,
        inputs: dict[str, Any],
        event_context: dict[str, Any] | None = None,
    ) -> StageRunResult:
        context = dict(event_context or {})
        if stage == "draft":
            return self._run_draft(inputs, context)
        if stage.startswith("review_"):
            return self._run_review(stage.removeprefix("review_"), inputs, context)
        if stage == "synthesis":
            return self._run_synthesis(inputs, context)
        if stage == "repair":
            return self._run_repair(inputs, context)
        raise ValueError(f"Unknown Planning protocol stage: {stage}")

    def _run_draft(
        self, inputs: dict[str, Any], event_context: dict[str, Any]
    ) -> StageRunResult:
        brief = dict(inputs["planning_brief"])
        hypothesis_id = str(inputs["hypothesis_id"])
        call = self.llm.complete_json(
            stage="draft",
            system_prompt=PROTOCOL_DRAFT_SYSTEM_PROMPT,
            user_prompt=protocol_draft_user_prompt(brief),
            temperature=0.20,
            event_context=event_context,
            allow_thinking=True,
        )
        draft, report = guard_protocol_draft(call.value, brief, hypothesis_id)
        self._finish_or_fail("draft", event_context, call, report)
        return _result("draft", inputs, call, {"protocol_draft": draft, "contract_report": report})

    def _run_review(
        self, role: str, inputs: dict[str, Any], event_context: dict[str, Any]
    ) -> StageRunResult:
        brief = dict(inputs["planning_brief"])
        draft = dict(inputs["protocol_draft"])
        hypothesis_id = str(inputs["hypothesis_id"])
        stage = f"review_{role}"
        call = self.llm.complete_json(
            stage=stage,
            system_prompt=REVIEW_SYSTEM_PROMPTS[role],
            user_prompt=protocol_review_user_prompt(role, brief, draft),
            temperature=0.10,
            event_context=event_context,
            allow_thinking=True,
        )
        review, report = guard_protocol_review(
            call.value, brief, hypothesis_id, role
        )
        self._finish_or_fail(stage, event_context, call, report)
        return _result(stage, inputs, call, {"protocol_review": review, "contract_report": report})

    def _run_synthesis(
        self, inputs: dict[str, Any], event_context: dict[str, Any]
    ) -> StageRunResult:
        task_id = str(inputs["task_id"])
        iteration = int(inputs["iteration"])
        hypothesis_id = str(inputs["hypothesis_id"])
        brief = dict(inputs["planning_brief"])
        call = self.llm.complete_json(
            stage="synthesis",
            system_prompt=SYNTHESIS_SYSTEM_PROMPT,
            user_prompt=synthesis_user_prompt(
                task_id,
                iteration,
                hypothesis_id,
                brief,
                dict(inputs["protocol_draft"]),
                dict(inputs["merged_reviews"]),
            ),
            temperature=0.12,
            event_context=event_context,
            allow_thinking=False,
        )
        result, report = guard_final_plan(
            call.value, brief, task_id, iteration, hypothesis_id
        )
        self._finish_or_fail("synthesis", event_context, call, report)
        return _result("synthesis", inputs, call, {"plan_result": result, "contract_report": report})

    def _run_repair(
        self, inputs: dict[str, Any], event_context: dict[str, Any]
    ) -> StageRunResult:
        task_id = str(inputs["task_id"])
        iteration = int(inputs["iteration"])
        hypothesis_id = str(inputs["hypothesis_id"])
        brief = dict(inputs["planning_brief"])
        call = self.llm.complete_json(
            stage="repair",
            system_prompt=REPAIR_SYSTEM_PROMPT,
            user_prompt=repair_user_prompt(
                task_id,
                iteration,
                hypothesis_id,
                brief,
                dict(inputs["invalid_result"]),
                [str(item) for item in inputs.get("contract_issues", [])],
            ),
            temperature=0.05,
            event_context=event_context,
            allow_thinking=False,
        )
        result, report = guard_final_plan(
            call.value, brief, task_id, iteration, hypothesis_id
        )
        self._finish_or_fail("repair", event_context, call, report)
        return _result("repair", inputs, call, {"plan_result": result, "contract_report": report})

    def _finish_or_fail(
        self, stage: str, context: dict[str, Any], call: Any, report: dict[str, Any]
    ) -> None:
        if report.get("passed"):
            self.llm.emit_finished(stage, context, call)
        else:
            self.llm.emit_validation_failed(
                stage, context, call, [str(item) for item in report.get("issues", [])]
            )

    def public_summary(self) -> dict[str, Any]:
        return self.config.public_summary("protocol_compiler")


def _result(
    stage: str, inputs: dict[str, Any], call: Any, outputs: dict[str, Any]
) -> StageRunResult:
    return StageRunResult(
        stage=stage,
        run_id=call.run_id,
        request_id=str(inputs.get("task_id") or ""),
        status="succeeded",
        elapsed_time=call.elapsed_time,
        total_tokens=call.total_tokens,
        outputs=outputs,
    )
