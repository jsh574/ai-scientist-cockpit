"""模块1：问题理解 Agent 主逻辑。

对外提供 ProblemUnderstandingAgent.run()：
  输入  = task_context.user_input (dict)
  输出  = 统一信封 {status, error, meta, data:{question_card}}
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import ValidationError

from .config import ProblemUnderstandingConfig
from .schema import (
    AgentMetadata,
    PriorRound,
    ProblemUnderstandingPayload,
    ProblemUnderstandingRequest,
    ProblemUnderstandingResponse,
    PromptSnapshot,
    QuestionCard,
    SelfReview,
    UserInput,
    DOWNSTREAM_REQUIRED_FIELDS,
)
from .llm_client import LLMClient
from .prompts import (
    build_retry_prompt,
    build_system_prompt,
    build_user_prompt,
    feedback_to_text,
    is_revision_round,
)
from .review import review_question_card
from .state_store import RoundStateProvider, RoundStateStore

STAGE = "question_understanding"


class ProblemUnderstandingAgent:
    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        state_store: Optional[RoundStateProvider] = None,
        config: Optional[ProblemUnderstandingConfig] = None,
    ):
        self.llm = llm or LLMClient()
        self.config = config or ProblemUnderstandingConfig.from_env()
        self.state_store = state_store
        if self.state_store is None and self.config.state_mode == "sqlite":
            self.state_store = RoundStateStore()

    def run(
        self,
        user_input: dict,
        question_id: Optional[str] = None,
        version: Optional[int] = None,
        feedback: Optional[dict | str] = None,
        task_id: str = "task-local",
    ) -> dict:
        try:
            requested_version = int(version) if version is not None else None
            if requested_version is not None and requested_version < 1:
                raise ValueError("iteration must be >= 1")
        except (TypeError, ValueError) as exc:
            return self._error(
                task_id,
                1,
                "INVALID_ITERATION",
                f"iteration/version 必须是大于等于 1 的整数：{exc}",
                recoverable=False,
            )

        fallback_version = requested_version or 1
        try:
            ui = UserInput(**user_input)
        except (ValidationError, TypeError) as e:
            return self._error(task_id, fallback_version, "UNPARSEABLE", str(e), recoverable=False)

        if not ui.original_question.strip():
            return self._error(task_id, fallback_version, "EMPTY_QUESTION", "original_question 为空", recoverable=False)

        qid = question_id or ui.question_id or "q_local"
        state_warnings: list[dict] = []
        history_source = "none"
        state_enabled = self.config.state_mode == "sqlite" and self.state_store is not None
        latest_iteration = None
        if requested_version is None and not ui.prior_rounds and state_enabled:
            try:
                latest_iteration = self.state_store.latest_iteration(
                    task_id, qid, ui.original_question
                )
            except Exception as exc:
                state_warnings.append({
                    "code": "STATE_LOAD_FAILED",
                    "message": f"无法读取上一轮状态，将在无内部历史的情况下继续：{exc}",
                })

        if ui.reset_history:
            resolved_version = requested_version or 1
        elif requested_version is not None:
            resolved_version = requested_version
        elif ui.prior_rounds:
            resolved_version = max(item.iteration for item in ui.prior_rounds) + 1
        else:
            resolved_version = (latest_iteration or 0) + 1

        history: list[PriorRound] = []
        if ui.reset_history or resolved_version <= 1:
            if state_enabled:
                try:
                    self.state_store.clear(task_id, qid, ui.original_question)
                except Exception as exc:
                    state_warnings.append({
                        "code": "STATE_RESET_FAILED",
                        "message": f"无法清理旧轮次状态：{exc}",
                    })
        elif ui.prior_rounds:
            history = ui.prior_rounds[-self.config.history_limit:]
            history_source = "input"
        elif state_enabled:
            try:
                history = self.state_store.load_history(
                    task_id,
                    qid,
                    ui.original_question,
                    before_iteration=resolved_version,
                    limit=self.config.history_limit,
                )
                if history:
                    history_source = "state_store"
            except Exception as exc:
                state_warnings.append({
                    "code": "STATE_LOAD_FAILED",
                    "message": f"无法加载上一轮快照，将在无历史快照的情况下继续：{exc}",
                })

        current_feedback = (ui.user_feedback or "").strip()
        if not current_feedback and feedback:
            current_feedback = feedback_to_text(feedback).strip()
        effective_ui = ui.model_copy(update={
            "user_feedback": current_feedback,
            "prior_rounds": history,
        })

        revision_mode = is_revision_round(effective_ui)
        system_prompt = build_system_prompt(revision_mode)
        base_user_prompt = build_user_prompt(effective_ui)
        if len(system_prompt) + len(base_user_prompt) > self.config.max_prompt_chars:
            return self._error(
                task_id,
                resolved_version,
                "PROMPT_TOO_LARGE",
                (
                    f"Prompt 长度 {len(system_prompt) + len(base_user_prompt)} 超过上限 "
                    f"{self.config.max_prompt_chars}，未静默截断历史"
                ),
                recoverable=True,
            )

        user_prompt = base_user_prompt
        validation_history: list[str] = []
        card: Optional[QuestionCard] = None
        attempt_count = 0
        last_error_code = "OUTPUT_VALIDATION_FAILED"
        for attempt in range(self.config.max_output_retries + 1):
            attempt_count = attempt + 1
            if len(system_prompt) + len(user_prompt) > self.config.max_prompt_chars:
                return self._error(
                    task_id,
                    resolved_version,
                    "PROMPT_TOO_LARGE",
                    "输出修复 Prompt 超过配置上限，未继续调用模型",
                    recoverable=True,
                )
            try:
                raw = self.llm.chat_json(system_prompt, user_prompt)
            except Exception as exc:
                last_error_code = "LLM_CALL_FAILED"
                issues = [f"LLM 调用失败：{exc}"]
            else:
                last_error_code = "OUTPUT_VALIDATION_FAILED"
                issues = self._validate_raw_output(raw, effective_ui)
                if not issues:
                    card = self._assemble_card(raw, effective_ui, qid, resolved_version)
                    if card is None:
                        issues = ["LLM 输出无法组装为合法 question_card"]
                    else:
                        break

            validation_history.extend(issues)
            if attempt < self.config.max_output_retries:
                user_prompt = build_retry_prompt(base_user_prompt, issues, attempt + 1)

        if card is None:
            return self._error(
                task_id,
                resolved_version,
                last_error_code,
                "; ".join(validation_history[-8:]) or "问题卡生成失败",
                recoverable=True,
                issues=validation_history,
            )

        feedback_applied = bool(current_feedback)
        self_review = review_question_card(
            card,
            effective_ui,
            current_feedback,
            threshold=self.config.self_review_threshold,
        )
        envelope = self._ok(
            task_id,
            resolved_version,
            card,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            feedback_applied=feedback_applied,
            self_review=self_review,
            attempt_count=attempt_count,
            prompt_size_chars=len(system_prompt) + len(user_prompt),
            history_rounds_included=len(history),
        )
        if card.confidence < self.config.confidence_warning_threshold:
            envelope["meta"].setdefault("warnings", []).append({
                "code": "LOW_CONFIDENCE",
                "message": f"置信度 {card.confidence} 低于阈值，建议人工介入",
            })
        if self._feedback_not_applied(effective_ui, card):
            envelope["meta"].setdefault("warnings", []).append({
                "code": "FEEDBACK_NOT_APPLIED",
                "message": "本轮用户要求非空，但问题卡片与上一轮相同且没有改动记录",
            })

        if state_warnings:
            envelope["meta"].setdefault("warnings", []).extend(state_warnings)
        envelope["meta"]["history_source"] = history_source
        envelope["meta"]["state_mode"] = self.config.state_mode
        envelope["meta"]["state_persisted"] = state_enabled

        snapshot = PriorRound(
            iteration=resolved_version,
            user_feedback=current_feedback,
            prompt_snapshot=PromptSnapshot(system=system_prompt, user=user_prompt),
            run_result={
                "status": envelope["status"],
                "error": envelope["error"],
                "meta": dict(envelope["meta"]),
                "self_review": envelope["self_review"],
            },
            question_card=card.model_dump(),
        )
        if state_enabled:
            try:
                self.state_store.save(task_id, qid, ui.original_question, snapshot)
            except Exception as exc:
                envelope["meta"]["state_persisted"] = False
                envelope["meta"].setdefault("warnings", []).append({
                    "code": "STATE_SAVE_FAILED",
                    "message": f"本轮执行成功，但轮次快照保存失败：{exc}",
                })
                snapshot.run_result["meta"] = dict(envelope["meta"])
        envelope["data"]["round_snapshot"] = snapshot.model_dump()
        return envelope

    def run_protocol(
        self, request: dict[str, Any] | ProblemUnderstandingRequest
    ) -> dict[str, Any]:
        """与其他模块一致的 metadata/payload/self_review 协议入口。"""
        try:
            req = (
                request
                if isinstance(request, ProblemUnderstandingRequest)
                else ProblemUnderstandingRequest.model_validate(request)
            )
        except ValidationError as exc:
            return ProblemUnderstandingResponse(
                metadata=AgentMetadata(
                    task_id=str(request.get("task_id") or "") if isinstance(request, dict) else "",
                    iteration=1,
                    status="failed",
                ),
                payload=ProblemUnderstandingPayload(),
                self_review=SelfReview(
                    passed=False,
                    overall_score=0.0,
                    threshold=self.config.self_review_threshold,
                    issues=[str(exc)],
                    suggestions=["修复 problem_understanding_input_v1 请求字段后重试。"],
                ),
            ).model_dump(mode="json")

        legacy = self.run(
            req.input.model_dump(),
            version=req.iteration,
            feedback=req.feedback,
            task_id=req.task_id,
        )
        data = legacy.get("data") or {}
        review = legacy.get("self_review") or self._failed_self_review(
            str((legacy.get("error") or {}).get("message") or "问题理解执行失败")
        )
        protocol_status = "failed"
        if legacy.get("status") == "ok":
            protocol_status = "success" if review.get("passed") else "partial_success"
        return ProblemUnderstandingResponse(
            metadata=AgentMetadata(
                task_id=req.task_id,
                iteration=req.iteration,
                status=protocol_status,
            ),
            payload=ProblemUnderstandingPayload(
                question_card=data.get("question_card"),
                prompt_snapshot=data.get("prompt_snapshot"),
                round_snapshot=data.get("round_snapshot"),
            ),
            self_review=SelfReview.model_validate(review),
        ).model_dump(mode="json")

    def _validate_raw_output(self, raw: Any, ui: UserInput) -> list[str]:
        """严格检查模型原始输出；格式兜底不能替代关键内容。"""
        if not isinstance(raw, dict):
            return ["LLM 输出必须是 JSON object"]
        if not raw:
            return ["LLM 输出为空 JSON object"]

        prior = ui.prior_rounds[-1].question_card if ui.prior_rounds else None
        issues: list[str] = []
        for field in DOWNSTREAM_REQUIRED_FIELDS:
            raw_value = raw.get(field)
            prior_value = (prior or {}).get(field)
            if self._is_empty(raw_value) and self._is_empty(prior_value):
                issues.append(f"缺少下游必需字段：{field}")

        if not self._is_empty(raw.get("core_question")) and not isinstance(
            raw.get("core_question"), str
        ):
            issues.append("core_question 必须是字符串")

        has_feedback = bool((ui.user_feedback or "").strip())
        if has_feedback and prior:
            feedback_markers = (
                raw.get("revision_notes"),
                raw.get("unaddressed_feedback"),
                raw.get("feedback_directives"),
            )
            if all(self._is_empty(value) for value in feedback_markers):
                issues.append(
                    "迭代轮必须返回 revision_notes、unaddressed_feedback 或 feedback_directives"
                )
        return issues

    @staticmethod
    def _is_empty(value: Any) -> bool:
        return value is None or value == "" or value == [] or value == {}

    def _assemble_card(self, raw: dict, ui: UserInput, qid: str, version: int) -> Optional[QuestionCard]:
        raw = self._normalize_raw(raw, ui, qid, version)
        try:
            return QuestionCard(**raw)
        except ValidationError:
            allowed = set(QuestionCard.model_fields.keys())
            cleaned = {k: v for k, v in raw.items() if k in allowed}
            cleaned = self._normalize_raw(cleaned, ui, qid, version)
            try:
                return QuestionCard(**cleaned)
            except ValidationError as e:
                print(f"    [debug] question_card 校验失败 {qid}: {e}")
                return None

    def _normalize_raw(self, raw: dict, ui: UserInput, qid: str, version: int) -> dict:
        raw = dict(raw or {})
        raw = self._inherit_prior_values(raw, ui)
        raw["question_id"] = qid
        raw["version"] = version
        raw["original_question"] = ui.original_question
        raw.setdefault("core_question", ui.original_question)
        raw["question_type"] = self._normalize_question_type(raw.get("question_type"))
        raw["domain"] = self._to_str_list(raw.get("domain")) or [ui.user_constraints.domain_preference or "interdisciplinary"]
        raw["research_object"] = self._to_research_object(raw.get("research_object"), ui.original_question)
        raw["key_concepts"] = self._to_str_list(raw.get("key_concepts")) or self._fallback_terms(ui.original_question)
        raw["key_variables"] = self._to_key_variables(raw.get("key_variables"), raw["key_concepts"])
        raw["sub_questions"] = self._to_str_list(raw.get("sub_questions"), keys=("content", "question", "sub_question")) or [ui.original_question]
        raw["research_scope"] = self._to_scope(raw.get("research_scope"))
        raw["search_keywords"] = self._to_str_list(raw.get("search_keywords")) or raw["key_concepts"]
        raw["context"] = self._to_context(raw.get("context"))
        raw["verifiability"] = self._to_verifiability(raw.get("verifiability"))
        raw["assumptions"] = self._to_assumptions(raw.get("assumptions"))
        raw["confidence"] = self._to_confidence(raw.get("confidence"))
        raw["revision_notes"] = self._to_revision_notes(raw.get("revision_notes"))
        raw["unaddressed_feedback"] = self._to_str_list(raw.get("unaddressed_feedback"))
        raw["feedback_directives"] = self._to_feedback_directives(
            raw.get("feedback_directives")
        )
        return raw

    def _inherit_prior_values(self, raw: dict, ui: UserInput) -> dict:
        """迭代轮缺失/空字段时沿用上一轮，避免局部修订造成其他字段质量倒退。"""
        if not ui.prior_rounds or not ui.prior_rounds[-1].question_card:
            return raw

        prior = ui.prior_rounds[-1].question_card or {}
        inheritable = (
            "core_question", "question_type", "domain", "research_object", "context",
            "key_concepts", "key_variables", "sub_questions", "research_scope",
            "search_keywords", "verifiability", "assumptions", "confidence",
        )
        for field in inheritable:
            value = raw.get(field)
            if (value is None or value == "" or value == [] or value == {}) and field in prior:
                raw[field] = prior[field]
        return raw

    def _to_revision_notes(self, value) -> list[dict]:
        out = []
        if not isinstance(value, list):
            value = [value] if value else []
        for item in value:
            if isinstance(item, dict):
                field = str(item.get("field") or item.get("name") or "").strip()
                change = str(
                    item.get("change")
                    or item.get("description")
                    or item.get("comment")
                    or ""
                ).strip()
                if field and change:
                    out.append({
                        "field": field,
                        "change": change,
                        "driven_by": str(item.get("driven_by") or "user_feedback"),
                    })
            elif str(item).strip():
                out.append({
                    "field": "unspecified",
                    "change": str(item).strip(),
                    "driven_by": "user_feedback",
                })
        return out

    def _to_feedback_directives(self, value) -> dict[str, list[str]]:
        source = value if isinstance(value, dict) else {}
        return {
            key: self._to_str_list(source.get(key))
            for key in (
                "question_reframe",
                "scope_changes",
                "concept_updates",
                "constraint_updates",
                "out_of_scope",
            )
        }

    def _feedback_not_applied(self, ui: UserInput, card: QuestionCard) -> bool:
        if not (ui.user_feedback or "").strip() or not ui.prior_rounds:
            return False
        prior = ui.prior_rounds[-1].question_card
        if not prior or card.revision_notes:
            return False

        current = card.model_dump()
        ignored = {"question_id", "version", "revision_notes", "unaddressed_feedback"}
        comparable = (set(prior) & set(current)) - ignored
        return bool(comparable) and all(prior[key] == current[key] for key in comparable)

    def _normalize_question_type(self, value) -> str:
        valid = {"mechanism", "causal", "descriptive", "predictive", "comparative", "existence", "optimization", "definition"}
        mapping = {
            "mechanism_analysis": "mechanism",
            "mechanistic": "mechanism",
            "causal_relationship": "causal",
            "causality": "causal",
            "prediction": "predictive",
            "forecast": "predictive",
            "comparison": "comparative",
            "feasibility": "existence",
            "possibility": "existence",
            "explanatory": "descriptive",
        }
        v = str(value or "descriptive").strip().lower()
        v = mapping.get(v, v)
        return v if v in valid else "descriptive"

    def _to_research_object(self, value, fallback: str) -> str:
        if isinstance(value, dict):
            return str(value.get("name") or value.get("object") or value.get("research_object") or fallback)
        items = self._to_str_list(value)
        return items[0] if items else fallback

    def _to_key_variables(self, value, concepts: list[str]) -> list[dict]:
        valid_roles = {"target", "independent", "dependent", "outcome", "mediator", "condition", "control"}
        role_map = {
            "causal_factor": "independent",
            "factor": "independent",
            "driver": "independent",
            "cause": "independent",
            "parameter": "condition",
            "constraint": "condition",
            "mechanism": "mediator",
            "effect": "outcome",
            "result": "outcome",
            "endpoint": "outcome",
        }
        out = []
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    name = str(item.get("name") or item.get("variable") or item.get("concept") or "").strip()
                    if not name:
                        continue
                    role = str(item.get("role") or item.get("type") or "independent").strip().lower()
                    role = role_map.get(role, role)
                    if role not in valid_roles:
                        role = "independent"
                    out.append({"name": name, "role": role, "category": str(item.get("category") or item.get("type") or "factor")})
                elif item:
                    out.append({"name": str(item), "role": "independent", "category": "factor"})
        return out or [{"name": concepts[0] if concepts else "target phenomenon", "role": "target", "category": "research_object"}]

    def _to_scope(self, value) -> dict:
        if not isinstance(value, dict):
            return {"included": ["problem decomposition", "literature retrieval"], "excluded": ["unsupported speculation"]}
        return {"included": self._to_str_list(value.get("included")), "excluded": self._to_str_list(value.get("excluded"))}

    def _to_context(self, value) -> dict:
        if not isinstance(value, dict):
            return {"region": None, "time_scale": None, "spatial_scale": None, "conditions": []}
        return {
            "region": value.get("region"),
            "time_scale": value.get("time_scale"),
            "spatial_scale": value.get("spatial_scale"),
            "conditions": self._to_str_list(value.get("conditions")),
        }

    def _to_verifiability(self, value) -> dict:
        if not isinstance(value, dict):
            return {"is_verifiable": True, "type": "theoretical|observational", "checkpoints": []}
        return {
            "is_verifiable": bool(value.get("is_verifiable", True)),
            "type": str(value.get("type") or "theoretical|observational"),
            "checkpoints": self._to_str_list(value.get("checkpoints")),
        }

    def _to_assumptions(self, value) -> list[dict]:
        out = []
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    point = str(item.get("point") or item.get("issue") or item.get("assumption") or "").strip()
                    if point:
                        out.append({
                            "point": point,
                            "default_choice": str(item.get("default_choice") or item.get("suggestion") or ""),
                            "need_human": bool(item.get("need_human", False)),
                        })
                elif item:
                    out.append({"point": str(item), "default_choice": "", "need_human": False})
        return out

    def _to_confidence(self, value) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.7

    def _fallback_terms(self, text: str) -> list[str]:
        return [w.strip("?,.()\"'") for w in text.split() if len(w.strip("?,.()\"'")) > 2][:6] or [text]

    def _to_str_list(self, value, keys=("name", "normalized_name", "content", "keyword", "term")) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, dict):
            res = []
            for k in keys:
                if k in value:
                    res.extend(self._to_str_list(value[k], keys))
            if not res:
                for v in value.values():
                    res.extend(self._to_str_list(v, keys))
            return self._unique(res)
        if isinstance(value, list):
            res = []
            for item in value:
                res.extend(self._to_str_list(item, keys))
            return self._unique(res)
        return [str(value)] if str(value).strip() else []

    def _unique(self, values: list[str]) -> list[str]:
        seen, out = set(), []
        for v in values:
            s = str(v).strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out

    def _meta(self, task_id: str, version: int) -> dict:
        return {
            "task_id": task_id,
            "agent_id": self.config.agent_id,
            "stage": STAGE,
            "version": version,
            "iteration": version,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": "mock"
            if bool(getattr(self.llm, "mock", False))
            else str(getattr(self.llm, "model", "unknown")),
        }

    def _ok(
        self,
        task_id: str,
        version: int,
        card: QuestionCard,
        system_prompt: str,
        user_prompt: str,
        feedback_applied: bool,
        self_review: dict[str, Any],
        attempt_count: int,
        prompt_size_chars: int,
        history_rounds_included: int,
    ) -> dict:
        meta = self._meta(task_id, version)
        meta.update({
            "feedback_applied": feedback_applied,
            "attempt_count": attempt_count,
            "output_retry_count": max(0, attempt_count - 1),
            "prompt_size_chars": prompt_size_chars,
            "history_rounds_included": history_rounds_included,
        })
        return {
            "status": "ok",
            "error": None,
            "meta": meta,
            "self_review": self_review,
            "data": {
                "question_card": card.model_dump(),
                "prompt_snapshot": {
                    "system": system_prompt,
                    "user": user_prompt,
                },
            },
        }

    def _failed_self_review(
        self, message: str, issues: Optional[list[str]] = None
    ) -> dict[str, Any]:
        return {
            "passed": False,
            "overall_score": 0.0,
            "threshold": self.config.self_review_threshold,
            "dimension_scores": {},
            "issues": issues or [message],
            "suggestions": ["根据 issues 修复输入、Prompt 或模型输出后重试。"],
        }

    def _error(
        self,
        task_id: str,
        version: int,
        code: str,
        msg: str,
        recoverable: bool,
        issues: Optional[list[str]] = None,
    ) -> dict:
        return {
            "status": "error",
            "error": {"code": code, "message": msg, "recoverable": recoverable},
            "meta": self._meta(task_id, version),
            "self_review": self._failed_self_review(msg, issues),
            "data": None,
        }
