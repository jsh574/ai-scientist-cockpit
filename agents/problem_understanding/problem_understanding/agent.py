"""模块1：问题理解 Agent 主逻辑。

对外提供 ProblemUnderstandingAgent.run()：
  输入  = task_context.user_input (dict)
  输出  = 统一信封 {status, error, meta, data:{question_card}}
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import ValidationError

from .schema import QuestionCard, UserInput
from .llm_client import LLMClient
from .prompts import SYSTEM_PROMPT, build_user_prompt

STAGE = "problem_understanding"
CONFIDENCE_THRESHOLD = 0.35


class ProblemUnderstandingAgent:
    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or LLMClient()

    def run(
        self,
        user_input: dict,
        question_id: Optional[str] = None,
        version: int = 1,
        feedback: Optional[dict] = None,
        task_id: str = "task-local",
    ) -> dict:
        try:
            ui = UserInput(**user_input)
        except ValidationError as e:
            return self._error(task_id, version, "UNPARSEABLE", str(e), recoverable=False)

        if not ui.original_question.strip():
            return self._error(task_id, version, "EMPTY_QUESTION", "original_question 为空", recoverable=False)

        qid = question_id or ui.question_id or "q_local"
        raw = self.llm.chat_json(SYSTEM_PROMPT, build_user_prompt(ui, feedback=feedback))
        card = self._assemble_card(raw, ui, qid, version)

        if card is None:
            return self._error(task_id, version, "UNPARSEABLE", "LLM 输出无法组装为 question_card", recoverable=False)

        envelope = self._ok(task_id, version, card)
        if card.confidence < CONFIDENCE_THRESHOLD:
            envelope["meta"]["warning"] = {
                "code": "LOW_CONFIDENCE",
                "message": f"置信度 {card.confidence} 低于阈值，建议人工介入",
            }
        return envelope

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
        return raw

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
            "stage": STAGE,
            "version": version,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": "mock" if self.llm.mock else self.llm.model,
        }

    def _ok(self, task_id: str, version: int, card: QuestionCard) -> dict:
        return {
            "status": "ok",
            "error": None,
            "meta": self._meta(task_id, version),
            "data": {"question_card": card.model_dump()},
        }

    def _error(self, task_id: str, version: int, code: str, msg: str, recoverable: bool) -> dict:
        return {
            "status": "error",
            "error": {"code": code, "message": msg, "recoverable": recoverable},
            "meta": self._meta(task_id, version),
            "data": None,
        }
