from __future__ import annotations

from typing import Any

from .agent_protocol import STAGE_ORDER, get_agent_spec


class ControllerAssistant:
    review_weights = {
        "question_clarity": 0.10,
        "evidence_quality": 0.22,
        "hypothesis_quality": 0.16,
        "evidence_alignment": 0.18,
        "plan_executability": 0.24,
        "reproducibility": 0.10,
    }
    intents = {
        "explain",
        "modify",
        "rerun_agent",
        "compare_versions",
        "retrieve_more",
        "cancel",
        "status_query",
    }

    def route(
        self,
        context: dict[str, Any],
        message: str,
        llm_client: Any | None = None,
    ) -> dict[str, Any]:
        fallback = self._fallback_route(message)
        if llm_client is None:
            return fallback
        try:
            result = llm_client.generate_json(
                system_prompt=(
                    "Classify the operator message for a scientific workflow controller. "
                    f"intent must be one of {sorted(self.intents)}. Return intent, target_stage, "
                    "reason, optimized_instruction, and answer."
                ),
                user_payload={
                    "message": message,
                    "current_stage": context.get("current_stage"),
                    "available_stages": list(STAGE_ORDER),
                    "artifact_counts": {
                        "literature": len(context.get("literature_cards") or []),
                        "evidence": len(context.get("evidence_cards") or []),
                        "hypotheses": len(context.get("hypothesis_cards") or []),
                    },
                    "question_card": context.get("question_card"),
                    "hypothesis_cards": context.get("hypothesis_cards"),
                    "evidence_map": context.get("evidence_map"),
                    "research_plan": context.get("research_plan"),
                    "final_review": context.get("final_review"),
                },
                expected_schema="controller_route_v1",
            )
        except Exception:
            return fallback
        intent = str(result.get("intent") or fallback["intent"])
        target = result.get("target_stage")
        return {
            "schema_version": "controller_route_v1",
            "intent": intent if intent in self.intents else fallback["intent"],
            "target_stage": target if target in STAGE_ORDER else fallback["target_stage"],
            "reason": str(result.get("reason") or fallback["reason"]),
            "optimized_instruction": str(result.get("optimized_instruction") or message),
            "answer": str(result.get("answer") or fallback["answer"]),
        }

    def evaluate_plan(
        self,
        context: dict[str, Any],
        user_score: int,
        comment: str,
        problem_type: str | None = None,
        llm_client: Any | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        inferred = problem_type or self._problem_type(comment)
        agents = {
            "retrieve_more": ["knowledge_integration", "evidence_mapping", "research_planning"],
            "hypothesis_direction": ["hypothesis_generation", "evidence_mapping", "research_planning"],
            "evidence_quality": ["knowledge_integration", "evidence_mapping", "research_planning"],
            "experiment_design": ["research_planning"],
        }.get(inferred, ["research_planning"])
        llm_reason = ""
        llm_instructions: dict[str, str] = {}
        if llm_client is not None:
            try:
                result = llm_client.generate_json(
                    system_prompt=(
                        "Evaluate research-plan feedback and choose the minimum required stages to rerun. "
                        f"Allowed stages: {list(STAGE_ORDER)}. Return problem_type, agents_to_rerun, "
                        "instructions_by_agent, and reason."
                    ),
                    user_payload={
                        "user_score": user_score,
                        "comment": comment,
                        "current_plan": context.get("research_plan"),
                        "final_review": context.get("final_review"),
                    },
                    expected_schema="iteration_plan_v1",
                )
                selected = [
                    stage
                    for stage in STAGE_ORDER
                    if stage in result.get("agents_to_rerun", [])
                    and stage != "final_review"
                ]
                if selected:
                    earliest = min(STAGE_ORDER.index(stage) for stage in selected)
                    if earliest == 0:
                        agents = list(STAGE_ORDER[:-1])
                    else:
                        required = set(selected)
                        if required & {"knowledge_integration", "hypothesis_generation"}:
                            required.add("evidence_mapping")
                        required.add("research_planning")
                        agents = [stage for stage in STAGE_ORDER if stage in required]
                inferred = str(result.get("problem_type") or inferred)
                llm_reason = str(result.get("reason") or "")
                if isinstance(result.get("instructions_by_agent"), dict):
                    llm_instructions = {
                        stage: str(instruction)
                        for stage, instruction in result["instructions_by_agent"].items()
                        if stage in agents
                    }
            except Exception:
                pass
        decision = {
            "schema_version": "iteration_plan_v1",
            "problem_type": inferred,
            "agents_to_rerun": agents,
            "artifacts_to_keep": self._kept_fields(agents),
            "artifacts_to_invalidate": sorted(
                {field for stage in agents for field in get_agent_spec(stage).writes}
                | {"final_review"}
            ),
            "instructions_by_agent": {
                stage: llm_instructions.get(stage, comment) for stage in agents
            },
            "must_regenerate_plan": True,
            "reason": llm_reason or f"User score {user_score}/5; routed as {inferred}.",
        }
        evaluation = {
            "schema_version": "plan_evaluation_v1",
            "user_score": user_score,
            "comment": comment,
            "problem_type": inferred,
        }
        return evaluation, decision

    def evaluate_workflow(
        self,
        context: dict[str, Any],
        llm_client: Any | None = None,
    ) -> dict[str, Any]:
        rubric_scores = self._workflow_rubric(context)
        agent_review: dict[str, Any] = {}
        if llm_client is not None:
            try:
                agent_review = llm_client.generate_json(
                    system_prompt=(
                        "Act as an adversarial scientific review agent. Evaluate the completed "
                        "multi-agent workflow for scientific quality, not merely field presence. "
                        "Score question_clarity, evidence_quality, hypothesis_quality, "
                        "evidence_alignment, plan_executability, and reproducibility from 0 to 1. "
                        "First-round work should normally score 0.55-0.75; reserve scores above "
                        "0.90 for publication-ready, fully traceable and reproducible work. Return "
                        "dimension_scores, strengths, weaknesses, suggestions, and agents_to_rerun. "
                        "Suggestions must be concrete and written in Chinese."
                    ),
                    user_payload=self._workflow_review_payload(context),
                    expected_schema="controller_final_review_v2",
                )
            except Exception:
                agent_review = {}

        agent_scores = agent_review.get("dimension_scores")
        if not isinstance(agent_scores, dict):
            agent_scores = {}
        dimension_scores: dict[str, float] = {}
        for dimension, rubric_score in rubric_scores.items():
            raw_agent_score = agent_scores.get(dimension)
            if isinstance(raw_agent_score, (int, float)):
                normalized_agent_score = max(0.0, min(1.0, float(raw_agent_score)))
                blended = rubric_score * 0.55 + normalized_agent_score * 0.45
                dimension_scores[dimension] = round(
                    min(rubric_score + 0.10, blended), 3
                )
            else:
                dimension_scores[dimension] = round(rubric_score, 3)

        overall_score = sum(
            dimension_scores[dimension] * weight
            for dimension, weight in self.review_weights.items()
        )
        if not context.get("feedback_events"):
            overall_score = min(overall_score, 0.85)
        if min(dimension_scores.values(), default=0.0) < 0.55:
            overall_score = min(overall_score, 0.74)
        if any(
            context.get(field) in (None, [], {})
            for field in (
                "question_card",
                "literature_cards",
                "evidence_cards",
                "hypothesis_cards",
                "evidence_map",
                "research_plan",
            )
        ):
            overall_score = min(overall_score, 0.49)
        overall_score = round(max(0.0, min(0.92, overall_score)), 3)

        strengths = self._string_list(agent_review.get("strengths"))
        weaknesses = self._string_list(agent_review.get("weaknesses"))
        suggestions = self._string_list(agent_review.get("suggestions"))
        agents_to_rerun = [
            stage
            for stage in STAGE_ORDER
            if stage != "final_review"
            and stage in self._string_list(agent_review.get("agents_to_rerun"))
        ]
        if not strengths:
            strengths = self._fallback_strengths(dimension_scores)
        if not weaknesses:
            weaknesses = self._fallback_weaknesses(dimension_scores)
        if not suggestions:
            suggestions = self._fallback_suggestions(dimension_scores)
        if not agents_to_rerun:
            agents_to_rerun = self._fallback_rerun_agents(dimension_scores)

        passed = overall_score >= 0.78
        return {
            "passed": passed,
            "overall_score": overall_score,
            "dimension_scores": dimension_scores,
            "strengths": strengths[:5],
            "weaknesses": weaknesses[:6],
            "suggestions": suggestions[:6],
            "agents_to_rerun": agents_to_rerun,
            "revision_required": not passed,
            "review_source": "controller_agent" if agent_review else "rubric_fallback",
            "feedback_iterations": len(context.get("feedback_events") or []),
        }

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _average(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    def _workflow_rubric(self, context: dict[str, Any]) -> dict[str, float]:
        question = context.get("question_card") or {}
        if not isinstance(question, dict):
            question = {}
        literature = [item for item in context.get("literature_cards") or [] if isinstance(item, dict)]
        evidence = [item for item in context.get("evidence_cards") or [] if isinstance(item, dict)]
        hypotheses = [item for item in context.get("hypothesis_cards") or [] if isinstance(item, dict)]
        evidence_map = [item for item in context.get("evidence_map") or [] if isinstance(item, dict)]
        research_plan = context.get("research_plan") or {}
        plans = research_plan.get("plans") if isinstance(research_plan, dict) else []
        plans = [item for item in plans or [] if isinstance(item, dict)]

        question_clarity = sum(
            score
            for score, present in (
                (0.25, bool(question.get("core_question"))),
                (0.10, bool(question.get("question_type"))),
                (0.10, bool(question.get("domain"))),
                (0.20, bool(question.get("key_variables"))),
                (0.15, bool(question.get("sub_questions"))),
                (0.10, bool((question.get("research_scope") or {}).get("included"))),
                (0.10, bool(question.get("search_keywords"))),
            )
            if present
        )

        literature_ids = {str(item.get("literature_id")) for item in literature if item.get("literature_id")}
        evidence_ids = {str(item.get("evidence_id")) for item in evidence if item.get("evidence_id")}
        hypothesis_ids = {str(item.get("hypothesis_id")) for item in hypotheses if item.get("hypothesis_id")}
        traceable_ratio = self._average([1.0 if item.get("doi") or item.get("url") else 0.0 for item in literature])
        linked_ratio = self._average([
            1.0 if str(item.get("source_literature_id") or "") in literature_ids else 0.0
            for item in evidence
        ])
        evidence_detail = self._average([
            self._average([
                1.0 if item.get("claim") else 0.0,
                1.0 if item.get("summary") else 0.0,
                1.0 if item.get("evidence_type") else 0.0,
                1.0 if isinstance(item.get("strength_score"), (int, float)) else 0.0,
            ])
            for item in evidence
        ])
        breadth = min(1.0, (len(literature) / 5 + len(evidence) / 10) / 2)
        evidence_quality = (
            breadth * 0.20
            + traceable_ratio * 0.25
            + linked_ratio * 0.25
            + evidence_detail * 0.30
        )

        hypothesis_quality = self._average([
            sum(
                score
                for score, present in (
                    (0.20, bool(item.get("statement"))),
                    (0.15, bool(item.get("rationale"))),
                    (0.20, bool(set(map(str, item.get("based_on_evidence_ids") or [])) & evidence_ids)),
                    (0.15, bool(item.get("expected_observation"))),
                    (0.15, bool(item.get("validation_idea"))),
                    (0.15, bool(item.get("initial_scores"))),
                )
                if present
            )
            for item in hypotheses
        ])

        evidence_alignment = self._average([
            sum(
                score
                for score, present in (
                    (0.15, str(item.get("hypothesis_id") or "") in hypothesis_ids),
                    (0.20, bool(
                        set(map(str, item.get("supporting_evidence_ids") or []))
                        | set(map(str, item.get("opposing_evidence_ids") or []))
                        | set(map(str, item.get("uncertain_evidence_ids") or []))
                    )),
                    (0.20, bool(item.get("evidence_summary"))),
                    (0.15, isinstance(item.get("evidence_strength_score"), (int, float))),
                    (0.15, bool(item.get("main_limitations"))),
                    (0.15, bool(item.get("detailed_review"))),
                )
                if present
            )
            for item in evidence_map
        ])

        plan_scores: list[float] = []
        reproducibility_scores: list[float] = []
        for item in plans:
            plan = item.get("plan") if isinstance(item.get("plan"), dict) else {}
            rationale = plan.get("rationale") if isinstance(plan.get("rationale"), dict) else {}
            methods = plan.get("methods") if isinstance(plan.get("methods"), dict) else {}
            experiments = plan.get("experiments") if isinstance(plan.get("experiments"), dict) else {}
            technical = plan.get("technical_details") if isinstance(plan.get("technical_details"), dict) else {}
            datasets = plan.get("datasets") if isinstance(plan.get("datasets"), dict) else {}
            plan_scores.append(sum(
                score
                for score, present in (
                    (0.10, str(item.get("hypothesis_id") or "") in hypothesis_ids),
                    (0.10, bool(plan.get("problem_statement"))),
                    (0.15, bool(rationale.get("text")) and bool(rationale.get("logic_chain"))),
                    (0.20, bool(methods.get("overall_design")) and bool(methods.get("steps"))),
                    (0.15, bool(experiments.get("main_experiment")) and bool(experiments.get("metrics"))),
                    (0.10, bool(experiments.get("falsification_criteria"))),
                    (0.10, bool(experiments.get("baselines"))),
                    (0.05, bool(datasets.get("source")) or bool(datasets.get("target"))),
                    (0.05, bool(plan.get("paper_title")) and bool(plan.get("paper_abstract"))),
                )
                if present
            ))
            reproducibility_scores.append(self._average([
                1.0 if technical.get("statistical_tests") and technical.get("software_stack") else 0.0,
                1.0 if datasets.get("source") or datasets.get("target") else 0.0,
                1.0 if methods.get("steps") else 0.0,
                1.0 if experiments.get("metrics") else 0.0,
                1.0 if experiments.get("falsification_criteria") else 0.0,
            ]))

        return {
            "question_clarity": max(0.0, min(1.0, question_clarity)),
            "evidence_quality": max(0.0, min(1.0, evidence_quality)),
            "hypothesis_quality": max(0.0, min(1.0, hypothesis_quality)),
            "evidence_alignment": max(0.0, min(1.0, evidence_alignment)),
            "plan_executability": max(0.0, min(1.0, self._average(plan_scores))),
            "reproducibility": max(0.0, min(1.0, self._average(reproducibility_scores))),
        }

    @staticmethod
    def _workflow_review_payload(context: dict[str, Any]) -> dict[str, Any]:
        return {
            "iteration": context.get("iteration"),
            "original_question": (context.get("user_input") or {}).get("original_question"),
            "question_card": context.get("question_card"),
            "literature_cards": list(context.get("literature_cards") or [])[:20],
            "evidence_cards": list(context.get("evidence_cards") or [])[:30],
            "hypothesis_cards": list(context.get("hypothesis_cards") or [])[:10],
            "evidence_map": list(context.get("evidence_map") or [])[:10],
            "research_plan": context.get("research_plan"),
            "prior_feedback": list(context.get("feedback_events") or [])[-5:],
        }

    @staticmethod
    def _fallback_strengths(scores: dict[str, float]) -> list[str]:
        labels = {
            "question_clarity": "研究问题结构较清晰",
            "evidence_quality": "证据来源与主张具备基本可追溯性",
            "hypothesis_quality": "假设具有一定可检验性",
            "evidence_alignment": "假设与证据之间建立了映射",
            "plan_executability": "研究计划包含可执行要素",
            "reproducibility": "计划提供了部分复现信息",
        }
        return [labels[key] for key, value in scores.items() if value >= 0.75] or ["工作流已生成完整的结构化产物"]

    @staticmethod
    def _fallback_weaknesses(scores: dict[str, float]) -> list[str]:
        labels = {
            "question_clarity": "研究边界、变量或子问题仍不够明确",
            "evidence_quality": "文献数量、证据强度或来源追溯仍不足",
            "hypothesis_quality": "假设的机制依据、预期观察或证伪路径不足",
            "evidence_alignment": "支持、反对与不确定证据的映射不够完整",
            "plan_executability": "实验设计、基线、指标或实施步骤不够具体",
            "reproducibility": "数据、统计方法、软件栈或复现条件不完整",
        }
        return [labels[key] for key, value in scores.items() if value < 0.75]

    @staticmethod
    def _fallback_suggestions(scores: dict[str, float]) -> list[str]:
        suggestions = {
            "question_clarity": "让问题理解 Agent 收紧研究对象、变量定义和排除范围。",
            "evidence_quality": "让知识整合 Agent 补充高质量可追溯文献，并标注证据强度。",
            "hypothesis_quality": "让假设 Agent 补足机制链、可观测预测和明确证伪条件。",
            "evidence_alignment": "让证据梳理 Agent 同时整理支持、反对和不确定证据。",
            "plan_executability": "让研究计划 Agent 明确数据、基线、指标、步骤和风险控制。",
            "reproducibility": "让研究计划 Agent 补充统计检验、软件版本和复现实验条件。",
        }
        return [suggestions[key] for key, value in scores.items() if value < 0.75]

    @staticmethod
    def _fallback_rerun_agents(scores: dict[str, float]) -> list[str]:
        mapping = {
            "question_clarity": "question_understanding",
            "evidence_quality": "knowledge_integration",
            "hypothesis_quality": "hypothesis_generation",
            "evidence_alignment": "evidence_mapping",
            "plan_executability": "research_planning",
            "reproducibility": "research_planning",
        }
        selected = {mapping[key] for key, value in scores.items() if value < 0.75}
        if selected & {"question_understanding", "knowledge_integration", "hypothesis_generation"}:
            selected.update({"evidence_mapping", "research_planning"})
        elif "evidence_mapping" in selected:
            selected.add("research_planning")
        return [stage for stage in STAGE_ORDER if stage in selected and stage != "final_review"] or ["research_planning"]

    @staticmethod
    def _problem_type(message: str) -> str:
        lowered = message.lower()
        if any(word in lowered for word in ("文献", "证据", "literature", "evidence")):
            return "retrieve_more"
        if any(word in lowered for word in ("假设", "hypothesis", "方向")):
            return "hypothesis_direction"
        return "experiment_design"

    @staticmethod
    def _fallback_route(message: str) -> dict[str, Any]:
        lowered = message.lower()
        mention_targets = {
            "@knowledge": "knowledge_integration",
            "@hypothesis": "hypothesis_generation",
            "@evidence": "evidence_mapping",
            "@planning": "research_planning",
        }
        mentioned = next((stage for mention, stage in mention_targets.items() if mention in lowered), None)
        if mentioned:
            intent, target = "rerun_agent", mentioned
        elif any(word in lowered for word in ("取消", "停止", "cancel", "stop")):
            intent, target = "cancel", None
        elif any(word in lowered for word in ("状态", "进度", "status", "progress")):
            intent, target = "status_query", None
        elif any(word in lowered for word in ("比较", "diff", "compare")):
            intent, target = "compare_versions", None
        elif any(word in lowered for word in ("文献", "检索", "retrieve", "literature")):
            intent, target = "retrieve_more", "knowledge_integration"
        elif any(word in lowered for word in ("修改", "重做", "rerun", "modify")):
            intent, target = "modify", "research_planning"
        else:
            intent, target = "explain", None
        return {
            "schema_version": "controller_route_v1",
            "intent": intent,
            "target_stage": target,
            "reason": "Rule-based fallback routing.",
            "optimized_instruction": message,
            "answer": "The request was classified and recorded by the controller.",
        }

    @staticmethod
    def _kept_fields(agents: list[str]) -> list[str]:
        invalidated = {field for stage in agents for field in get_agent_spec(stage).writes}
        core = {
            "question_card", "literature_cards", "evidence_cards", "knowledge_gaps",
            "hypothesis_cards", "evidence_map", "research_plan",
        }
        return sorted(core - invalidated)
