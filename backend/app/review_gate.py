from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from .agent_protocol import AgentSpec
from .contracts import AgentResponse, ReviewRecord, ReviewScore


class ReviewGate:
    def __init__(self, threshold: float = 0.75) -> None:
        self.threshold = threshold

    @staticmethod
    def _ids(items: Any, key: str) -> set[str]:
        if not isinstance(items, list):
            return set()
        return {str(item[key]) for item in items if isinstance(item, dict) and item.get(key)}

    def _traceability(
        self, stage: str, payload: dict[str, Any], context: dict[str, Any]
    ) -> tuple[float, list[str]]:
        issues: list[str] = []
        valid_evidence = self._ids(context.get("evidence_cards"), "evidence_id")
        valid_literature = self._ids(context.get("literature_cards"), "literature_id")
        valid_gaps = self._ids(context.get("knowledge_gaps"), "gap_id")
        valid_hypotheses = self._ids(context.get("hypothesis_cards"), "hypothesis_id")

        if stage == "knowledge_integration":
            literature = payload.get("literature_cards")
            if not isinstance(literature, list) or not literature:
                return 0.0, ["No literature cards were produced."]
            traceable = sum(
                1
                for item in literature
                if isinstance(item, dict) and (item.get("doi") or item.get("url"))
            )
            score = traceable / len(literature)
            if score < 1:
                issues.append("Every literature card must include a DOI or URL.")
            return score, issues

        if stage == "hypothesis_generation":
            cards = payload.get("hypothesis_cards") or []
            evidence_references = [
                evidence_id
                for card in cards
                if isinstance(card, dict)
                for evidence_id in card.get("based_on_evidence_ids") or []
            ]
            gap_references = [
                gap_id
                for card in cards
                if isinstance(card, dict)
                for gap_id in card.get("related_gap_ids") or []
            ]
            for card in cards:
                if not isinstance(card, dict):
                    continue
                hypothesis_id = str(card.get("hypothesis_id") or "unknown")
                if not card.get("based_on_evidence_ids"):
                    issues.append(
                        f"Hypothesis {hypothesis_id} must cite at least one evidence_id."
                    )
                if not card.get("related_gap_ids"):
                    issues.append(
                        f"Hypothesis {hypothesis_id} must cite at least one gap_id."
                    )
            unknown_evidence = sorted(set(map(str, evidence_references)) - valid_evidence)
            unknown_gaps = sorted(set(map(str, gap_references)) - valid_gaps)
            if unknown_evidence:
                issues.append(f"Unknown evidence IDs in hypotheses: {unknown_evidence}")
            if unknown_gaps:
                issues.append(f"Unknown knowledge gap IDs in hypotheses: {unknown_gaps}")
            return (1.0 if not issues else 0.0), issues

        if stage == "evidence_mapping":
            maps = payload.get("evidence_map") or []
            unknown_evidence: set[str] = set()
            unknown_hypotheses: set[str] = set()
            for item in maps:
                if not isinstance(item, dict):
                    continue
                hypothesis_id = str(item.get("hypothesis_id") or "")
                if hypothesis_id not in valid_hypotheses:
                    unknown_hypotheses.add(hypothesis_id)
                for key in (
                    "supporting_evidence_ids",
                    "opposing_evidence_ids",
                    "uncertain_evidence_ids",
                ):
                    unknown_evidence.update(set(map(str, item.get(key) or [])) - valid_evidence)
            if unknown_hypotheses:
                issues.append(
                    f"Unknown hypothesis IDs in evidence map: {sorted(unknown_hypotheses)}"
                )
            if unknown_evidence:
                issues.append(f"Unknown evidence IDs in evidence map: {sorted(unknown_evidence)}")
            return (1.0 if not issues else 0.0), issues

        if stage == "research_planning":
            research_plan = payload.get("research_plan") or {}
            plans = research_plan.get("plans") if isinstance(research_plan, dict) else []
            unknown: set[str] = set()
            for plan_item in plans or []:
                plan = plan_item.get("plan") if isinstance(plan_item, dict) else {}
                rationale = plan.get("rationale") if isinstance(plan, dict) else {}
                for link in (rationale or {}).get("logic_chain") or []:
                    unknown.update(set(map(str, link.get("evidence_ids") or [])) - valid_evidence)
                    unknown.update(set(map(str, link.get("source_ids") or [])) - valid_literature)
            if unknown:
                issues.append(f"Unknown source IDs in research plan: {sorted(unknown)}")
            return (1.0 if not unknown else 0.0), issues

        return 1.0, issues

    def evaluate(
        self,
        raw_response: dict[str, Any],
        context: dict[str, Any],
        spec: AgentSpec,
    ) -> tuple[AgentResponse | None, ReviewRecord]:
        issues: list[str] = []
        try:
            response = AgentResponse.model_validate(raw_response)
            schema_score = 1.0
        except ValidationError as exc:
            response = None
            schema_score = 0.0
            issues.extend(error["msg"] for error in exc.errors())

        required_score = 0.0
        downstream_score = 0.0
        traceability_score = 0.0
        iteration_score = 0.0
        decision = "retry"

        if response is not None:
            if response.metadata.task_id != str(context.get("task_id") or ""):
                issues.append("Response task_id does not match the task context.")
            if response.metadata.stage != spec.stage:
                issues.append("Response stage does not match the requested stage.")
            if response.metadata.iteration != int(context.get("iteration") or 1):
                issues.append("Response iteration does not match the task context.")

            present = [key for key in spec.writes if key in response.payload]
            required_score = len(present) / len(spec.writes)
            unexpected = sorted(set(response.payload) - set(spec.writes))
            if unexpected:
                issues.append(f"Payload contains undeclared writes: {unexpected}")
                required_score = 0.0

            nonempty = [response.payload.get(key) not in (None, [], {}) for key in spec.writes]
            downstream_score = sum(nonempty) / len(nonempty)
            if downstream_score < 1:
                issues.append("One or more required payload fields are empty.")

            traceability_score, trace_issues = self._traceability(
                spec.stage, response.payload, context
            )
            issues.extend(trace_issues)
            iteration_score = min(1.0, response.self_review.overall_score)

            if response.metadata.status == "failed":
                decision = "fail"
            elif not response.self_review.passed:
                issues.extend(str(issue) for issue in response.self_review.issues)
                issues.append("Agent self-review did not pass.")
            elif response.self_review.overall_score < response.self_review.threshold:
                issues.append("Agent self-review score is below its declared threshold.")

        score = ReviewScore(
            schema_validity=schema_score,
            required_fields=required_score,
            downstream_readiness=downstream_score,
            evidence_traceability=traceability_score,
            iteration_value=iteration_score,
        )
        overall = (
            score.schema_validity * 0.25
            + score.required_fields * 0.2
            + score.downstream_readiness * 0.2
            + score.evidence_traceability * 0.25
            + score.iteration_value * 0.1
        )

        if response is not None and response.metadata.status != "failed":
            if not issues and overall >= self.threshold:
                mode = str(context.get("mode") or "auto")
                decision = (
                    "human_review"
                    if mode == "manual" or (mode == "hybrid" and spec.hybrid_review)
                    else "accept"
                )
            elif decision != "fail":
                decision = "retry"

        review = ReviewRecord(
            review_id=f"review_{uuid4().hex[:12]}",
            task_id=str(context.get("task_id") or ""),
            stage=spec.stage,
            decision=decision,
            comment="Review Gate validation completed.",
            score=score,
            overall_score=round(overall, 4),
            issues=issues,
        )
        return response, review
