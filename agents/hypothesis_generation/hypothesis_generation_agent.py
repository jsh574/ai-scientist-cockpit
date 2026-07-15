"""Hypothesis generation agent.

This module is intentionally API-provider neutral. Fill `call_llm()` later with
Qwen/DashScope logic. Everything before the real model call is ready: prompt
building, retries, JSON parsing, payload normalization, validation, and review.
"""

from __future__ import annotations

import os
import json
import re
from dataclasses import dataclass
from statistics import pstdev
from typing import Any, Iterable


class AgentInputError(ValueError):
    """Raised when the upstream context is missing required data."""


class AgentOutputError(ValueError):
    """Raised when the LLM output cannot be used by downstream modules."""


@dataclass(frozen=True)
class HypothesisAgentConfig:
    agent_id: str = "hypothesis_generation_agent"
    stage: str = "hypothesis_generation"
    threshold: float = 0.75
    default_max_hypotheses: int = 5
    min_hypotheses: int = 3
    max_retries: int = 2
    min_variable_coverage: float = 0.6
    min_evidence_keyword_overlap: float = 0.08
    min_gap_ratio: float = 0.8


class HypothesisGenerationAgent:
    """Generate candidate scientific hypotheses from evidence and knowledge gaps."""

    REQUIRED_QUESTION_FIELDS = (
        "core_question",
        "research_object",
        "key_concepts",
        "key_variables",
    )
    REQUIRED_EVIDENCE_FIELDS = (
        "evidence_id",
        "claim",
        "related_concepts",
        "summary",
    )
    REQUIRED_GAP_FIELDS = (
        "gap_id",
        "description",
        "related_concepts",
    )
    REQUIRED_HYPOTHESIS_FIELDS = (
        "hypothesis_id",
        "statement",
        "hypothesis_type",
        "rationale",
        "based_on_evidence_ids",
        "related_gap_ids",
        "target_variables",
        "expected_observation",
        "predictions",
        "validation_idea",
        "risk_or_limitation",
        "initial_scores",
    )
    REQUIRED_SCORE_FIELDS = (
        "novelty",
        "testability",
        "relevance",
        "evidence_alignment",
        "risk",
    )
    VAGUE_PATTERNS = (
        "可能有关",
        "可能相关",
        "值得研究",
        "存在一定关系",
        "有一定影响",
        "may be related",
        "might be related",
    )

    def __init__(self, config: HypothesisAgentConfig | None = None) -> None:
        self.config = config or HypothesisAgentConfig()

    def run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Main entry point called by the supervisor."""
        normalized_input = self.validate_input(input_data)
        retry_notes: list[str] = []
        last_error: AgentOutputError | None = None

        for attempt in range(self.config.max_retries + 1):
            prompt = (
                self.build_prompt(normalized_input)
                if attempt == 0
                else self.build_retry_prompt(normalized_input, retry_notes, attempt)
            )
            try:
                llm_text = self.call_llm(prompt)
                payload = self.parse_llm_output(llm_text)
                payload = self.normalize_payload(payload)
                audit = self.validate_payload(payload, normalized_input)
                self.calibrate_scores(payload)
                self_review = self.build_self_review(payload, normalized_input, audit, retry_notes)
                return self._response(normalized_input, payload, self_review)
            except AgentOutputError as exc:
                last_error = exc
                retry_notes.append(str(exc))

        return self._failed_response(normalized_input, retry_notes, last_error)

    def validate_input(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize the module input."""
        if not isinstance(input_data, dict):
            raise AgentInputError("input_data must be a dict.")

        task_id = input_data.get("task_id")
        if not task_id:
            raise AgentInputError("Missing required field: task_id.")

        iteration = input_data.get("iteration", 1)
        question_card = input_data.get("question_card")
        evidence_cards = input_data.get("evidence_cards")
        knowledge_gaps = input_data.get("knowledge_gaps")
        user_constraints = input_data.get("user_constraints", {})

        if not isinstance(question_card, dict):
            raise AgentInputError("Missing or invalid field: question_card.")
        self._require_fields(question_card, self.REQUIRED_QUESTION_FIELDS, "question_card")

        if not isinstance(evidence_cards, list) or not evidence_cards:
            raise AgentInputError("evidence_cards must be a non-empty list.")
        for index, evidence in enumerate(evidence_cards):
            if not isinstance(evidence, dict):
                raise AgentInputError(f"evidence_cards[{index}] must be a dict.")
            self._require_fields(
                evidence,
                self.REQUIRED_EVIDENCE_FIELDS,
                f"evidence_cards[{index}]",
            )

        if not isinstance(knowledge_gaps, list) or not knowledge_gaps:
            raise AgentInputError("knowledge_gaps must be a non-empty list.")
        for index, gap in enumerate(knowledge_gaps):
            if not isinstance(gap, dict):
                raise AgentInputError(f"knowledge_gaps[{index}] must be a dict.")
            self._require_fields(gap, self.REQUIRED_GAP_FIELDS, f"knowledge_gaps[{index}]")

        max_hypotheses = user_constraints.get(
            "max_hypotheses",
            self.config.default_max_hypotheses,
        )
        try:
            max_hypotheses = int(max_hypotheses)
        except (TypeError, ValueError) as exc:
            raise AgentInputError("user_constraints.max_hypotheses must be an integer.") from exc

        if max_hypotheses < self.config.min_hypotheses:
            max_hypotheses = self.config.min_hypotheses

        return {
            "task_id": task_id,
            "iteration": int(iteration),
            "question_card": question_card,
            "evidence_cards": evidence_cards,
            "knowledge_gaps": knowledge_gaps,
            "user_constraints": {
                **user_constraints,
                "max_hypotheses": max_hypotheses,
                "language": user_constraints.get("language", "zh"),
            },
        }

    def build_prompt(self, input_data: dict[str, Any]) -> str:
        """Build the LLM prompt for Qwen or another chat model."""
        max_hypotheses = input_data["user_constraints"]["max_hypotheses"]
        language = input_data["user_constraints"]["language"]
        schema_hint = self._schema_hint()
        context = {
            "question_card": input_data["question_card"],
            "evidence_cards": input_data["evidence_cards"],
            "knowledge_gaps": input_data["knowledge_gaps"],
            "user_constraints": input_data["user_constraints"],
        }

        return (
            "You are a scientific hypothesis generation Agent.\n"
            "Your output will be consumed by an evidence-mapping Agent, so every hypothesis must be traceable, testable, and specific.\n\n"
            f"Output language: {language}.\n"
            f"Generate {self.config.min_hypotheses} to {max_hypotheses} candidate hypotheses.\n\n"
            "Mandatory internal workflow before writing each hypothesis:\n"
            "1. Variable anchoring: identify the core variables from question_card.key_variables and choose target_variables.\n"
            "2. Evidence matching: select concrete evidence_ids whose claim/summary supports the variable relation.\n"
            "3. Gap coverage: select concrete gap_ids explaining why the hypothesis adds research value.\n"
            "4. Hypothesis construction: state a mechanism/causal/mediation/moderation/comparison relation and write checkable predictions.\n\n"
            "Hard constraints:\n"
            "- Return JSON only. No Markdown, no comments, no text outside JSON.\n"
            "- Each hypothesis must reference at least one known based_on_evidence_ids and at least one known related_gap_ids.\n"
            "- Each statement must mention at least two target variables or their close synonyms.\n"
            "- Each evidence_id used must be reflected in statement or rationale through overlapping concepts.\n"
            "- Do not merely repeat evidence claims. Convert evidence + gap into a new testable hypothesis.\n"
            "- Avoid vague empty wording such as 'possibly related', 'worth studying', or 'has some effect'.\n"
            "- Scientific uncertainty is allowed, but express it as a testable relation: 'A may affect C through B' plus predictions.\n"
            "- predictions must contain 1 to 3 concrete, evidence-checkable predictions.\n"
            "- initial_scores and hypothesis_scores must contain numbers between 0 and 1, and both score objects must be identical.\n"
            "- risk means higher is worse.\n\n"
            "Few-shot good example:\n"
            f"{json.dumps(self._few_shot_example(), ensure_ascii=False, indent=2)}\n\n"
            "Required output JSON schema:\n"
            f"{json.dumps(schema_hint, ensure_ascii=False, indent=2)}\n\n"
            "Input context:\n"
            f"{json.dumps(context, ensure_ascii=False, indent=2)}"
        )

    def build_retry_prompt(
        self,
        input_data: dict[str, Any],
        retry_notes: list[str],
        attempt: int,
    ) -> str:
        """Build a repair prompt when JSON parsing or validation failed."""
        base = self.build_prompt(input_data)
        return (
            f"{base}\n\n"
            f"Previous attempt {attempt} failed validation. Fix the output and return JSON only.\n"
            "Validation errors:\n"
            f"{json.dumps(retry_notes[-3:], ensure_ascii=False, indent=2)}"
        )

    def call_llm(self, prompt: str) -> str:
        """通过阿里云百炼 OpenAI 兼容接口调用千问模型。"""
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "缺少 openai 依赖，请执行：python -m pip install -U openai"
            ) from exc
    
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "未配置环境变量 DASHSCOPE_API_KEY。"
            )
    
        base_url = os.getenv(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    
        model = os.getenv("QWEN_MODEL", "qwen-plus")
    
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=120.0,
            max_retries=2,
        )
    
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a scientific hypothesis generation model. "
                            "Return exactly one valid JSON object. "
                            "Do not use Markdown code fences or add text outside JSON."
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
            )
        except Exception as exc:
            raise AgentOutputError(
                f"Qwen API call failed: {type(exc).__name__}: {exc}"
            ) from exc
    
        if not completion.choices:
            raise AgentOutputError("Qwen API returned no choices.")
    
        content = completion.choices[0].message.content
    
        if not isinstance(content, str) or not content.strip():
            raise AgentOutputError("Qwen API returned empty content.")
    
        return content.strip()
    
    def parse_llm_output(self, llm_text: str) -> dict[str, Any]:
        """Parse JSON returned by the LLM with conservative repair fallbacks."""
        if not llm_text or not isinstance(llm_text, str):
            raise AgentOutputError("LLM output must be a non-empty string.")

        candidates = [self._extract_json_text(llm_text)]
        repaired = self._repair_json_text(candidates[0])
        if repaired not in candidates:
            candidates.append(repaired)

        errors: list[str] = []
        for raw_json in candidates:
            try:
                payload = json.loads(raw_json)
            except json.JSONDecodeError as exc:
                errors.append(str(exc))
                continue
            if not isinstance(payload, dict):
                raise AgentOutputError("LLM output JSON must be an object.")
            return payload

        raise AgentOutputError(f"LLM output is not valid JSON after repair: {'; '.join(errors)}")

    def normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Normalize LLM payload for downstream module compatibility."""
        cards = payload.get("hypothesis_cards")
        if not isinstance(cards, list):
            return payload

        for index, card in enumerate(cards, start=1):
            if not isinstance(card, dict):
                continue

            card.setdefault("hypothesis_id", f"hyp_{index:03d}")
            initial_scores = card.get("initial_scores")
            hypothesis_scores = card.get("hypothesis_scores")
            if not isinstance(initial_scores, dict) and isinstance(hypothesis_scores, dict):
                card["initial_scores"] = dict(hypothesis_scores)
            elif isinstance(initial_scores, dict) and not isinstance(hypothesis_scores, dict):
                card["hypothesis_scores"] = dict(initial_scores)
            elif isinstance(initial_scores, dict) and isinstance(hypothesis_scores, dict):
                merged = {**hypothesis_scores, **initial_scores}
                card["initial_scores"] = dict(merged)
                card["hypothesis_scores"] = dict(merged)

            predictions = card.get("predictions")
            if not isinstance(predictions, list) or not predictions:
                expected = str(card.get("expected_observation") or "").strip()
                if expected:
                    card["predictions"] = self._split_prediction_text(expected)

        return payload

    def validate_payload(
        self,
        payload: dict[str, Any],
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate generated hypothesis cards and return audit information."""
        hypothesis_cards = payload.get("hypothesis_cards")
        if not isinstance(hypothesis_cards, list):
            raise AgentOutputError("payload.hypothesis_cards must be a list.")

        if len(hypothesis_cards) < self.config.min_hypotheses:
            raise AgentOutputError(
                f"At least {self.config.min_hypotheses} hypotheses are required."
            )

        max_hypotheses = input_data["user_constraints"]["max_hypotheses"]
        if len(hypothesis_cards) > max_hypotheses:
            raise AgentOutputError(f"Too many hypotheses. Max allowed: {max_hypotheses}.")

        evidence_by_id = {item["evidence_id"]: item for item in input_data["evidence_cards"]}
        gap_by_id = {item["gap_id"]: item for item in input_data["knowledge_gaps"]}
        seen_ids: set[str] = set()
        audit_items: list[dict[str, Any]] = []

        for index, card in enumerate(hypothesis_cards):
            if not isinstance(card, dict):
                raise AgentOutputError(f"hypothesis_cards[{index}] must be a dict.")
            self._require_fields(
                card,
                self.REQUIRED_HYPOTHESIS_FIELDS,
                f"hypothesis_cards[{index}]",
                error_cls=AgentOutputError,
            )

            hypothesis_id = card["hypothesis_id"]
            if hypothesis_id in seen_ids:
                raise AgentOutputError(f"Duplicate hypothesis_id: {hypothesis_id}.")
            seen_ids.add(hypothesis_id)

            based_on_evidence_ids = card["based_on_evidence_ids"]
            related_gap_ids = card["related_gap_ids"]
            if not isinstance(based_on_evidence_ids, list):
                raise AgentOutputError(f"{hypothesis_id}.based_on_evidence_ids must be a list.")
            if not isinstance(related_gap_ids, list):
                raise AgentOutputError(f"{hypothesis_id}.related_gap_ids must be a list.")
            if not based_on_evidence_ids:
                raise AgentOutputError(f"{hypothesis_id} must reference at least one evidence id.")
            if not related_gap_ids:
                raise AgentOutputError(f"{hypothesis_id} must reference at least one knowledge gap id.")

            unknown_evidence = set(based_on_evidence_ids) - set(evidence_by_id)
            unknown_gaps = set(related_gap_ids) - set(gap_by_id)
            if unknown_evidence:
                raise AgentOutputError(
                    f"{hypothesis_id} references unknown evidence ids: {sorted(unknown_evidence)}."
                )
            if unknown_gaps:
                raise AgentOutputError(
                    f"{hypothesis_id} references unknown gap ids: {sorted(unknown_gaps)}."
                )

            target_variables = card["target_variables"]
            if not isinstance(target_variables, list) or not target_variables:
                raise AgentOutputError(f"{hypothesis_id}.target_variables must be a non-empty list.")

            predictions = card["predictions"]
            if not isinstance(predictions, list) or not predictions:
                raise AgentOutputError(f"{hypothesis_id}.predictions must be a non-empty list.")
            if not all(isinstance(item, str) and item.strip() for item in predictions):
                raise AgentOutputError(f"{hypothesis_id}.predictions must contain non-empty strings.")

            scores = card["initial_scores"]
            if not isinstance(scores, dict):
                raise AgentOutputError(f"{hypothesis_id}.initial_scores must be a dict.")
            self._require_fields(
                scores,
                self.REQUIRED_SCORE_FIELDS,
                f"{hypothesis_id}.scores",
                error_cls=AgentOutputError,
            )
            for score_name in self.REQUIRED_SCORE_FIELDS:
                self._validate_score(scores[score_name], f"{hypothesis_id}.{score_name}")

            hypothesis_scores = card.get("hypothesis_scores")
            if not isinstance(hypothesis_scores, dict):
                raise AgentOutputError(f"{hypothesis_id}.hypothesis_scores must be a dict.")

            text_blob = " ".join(
                [
                    str(card["statement"]),
                    str(card["rationale"]),
                    str(card["expected_observation"]),
                    " ".join(card["predictions"]),
                ]
            )
            variable_coverage = self._variable_coverage(card, input_data)
            evidence_overlap = self._evidence_overlap(card, evidence_by_id)
            vague_hits = [pattern for pattern in self.VAGUE_PATTERNS if pattern.lower() in text_blob.lower()]

            if variable_coverage < self.config.min_variable_coverage:
                raise AgentOutputError(
                    f"{hypothesis_id} variable coverage too low: {variable_coverage:.2f}."
                )
            if evidence_overlap < self.config.min_evidence_keyword_overlap:
                raise AgentOutputError(
                    f"{hypothesis_id} evidence keyword overlap too low: {evidence_overlap:.2f}."
                )
            if vague_hits:
                raise AgentOutputError(f"{hypothesis_id} contains vague phrases: {vague_hits}.")

            audit_items.append(
                {
                    "hypothesis_id": hypothesis_id,
                    "variable_coverage": round(variable_coverage, 3),
                    "evidence_keyword_overlap": round(evidence_overlap, 3),
                    "gap_count": len(related_gap_ids),
                }
            )

        gap_ratio = sum(1 for card in hypothesis_cards if card.get("related_gap_ids")) / len(hypothesis_cards)
        if gap_ratio < self.config.min_gap_ratio:
            raise AgentOutputError(f"Too few hypotheses are linked to knowledge gaps: {gap_ratio:.2f}.")

        return {
            "hard_checks": audit_items,
            "gap_ratio": round(gap_ratio, 3),
        }

    def calibrate_scores(self, payload: dict[str, Any]) -> None:
        """Lightweight score audit to avoid overconfident flat scores."""
        cards = payload.get("hypothesis_cards", [])
        if not isinstance(cards, list) or len(cards) < 2:
            return

        overall_values = []
        for card in cards:
            scores = card.get("initial_scores", {})
            if isinstance(scores, dict):
                overall_values.append(
                    0.25 * float(scores.get("novelty", 0.0))
                    + 0.30 * float(scores.get("testability", 0.0))
                    + 0.25 * float(scores.get("relevance", 0.0))
                    + 0.20 * float(scores.get("evidence_alignment", 0.0))
                    - 0.15 * float(scores.get("risk", 0.0))
                )

        if len(overall_values) < 2:
            return
        if sum(overall_values) / len(overall_values) > 0.8 and pstdev(overall_values) < 0.05:
            for card in cards:
                scores = card.get("initial_scores", {})
                if not isinstance(scores, dict):
                    continue
                for key in ("novelty", "testability", "relevance", "evidence_alignment"):
                    scores[key] = round(max(0.0, float(scores.get(key, 0.0)) - 0.15), 3)
                card["hypothesis_scores"] = dict(scores)

    def build_self_review(
        self,
        payload: dict[str, Any],
        input_data: dict[str, Any],
        audit: dict[str, Any] | None = None,
        retry_notes: list[str] | None = None,
    ) -> dict[str, Any]:
        """Build module-level self review from generated cards."""
        cards = payload["hypothesis_cards"]
        scores = [card["initial_scores"] for card in cards]
        average_testability = self._mean(score["testability"] for score in scores)
        average_alignment = self._mean(score["evidence_alignment"] for score in scores)
        average_relevance = self._mean(score["relevance"] for score in scores)
        average_risk = self._mean(score["risk"] for score in scores)
        average_variable_coverage = self._mean(
            item["variable_coverage"] for item in (audit or {}).get("hard_checks", [])
        )
        average_evidence_overlap = self._mean(
            item["evidence_keyword_overlap"] for item in (audit or {}).get("hard_checks", [])
        )

        hypothesis_count_score = min(1.0, len(cards) / input_data["user_constraints"]["max_hypotheses"])
        diversity_score = self._estimate_diversity(cards)

        overall_score = round(
            0.15 * hypothesis_count_score
            + 0.15 * diversity_score
            + 0.20 * average_testability
            + 0.20 * average_alignment
            + 0.10 * average_relevance
            + 0.10 * average_variable_coverage
            + 0.10 * min(1.0, average_evidence_overlap * 3)
            - 0.10 * average_risk,
            3,
        )
        overall_score = max(0.0, min(1.0, overall_score))

        issues: list[str] = []
        suggestions: list[str] = []
        if average_alignment < 0.7:
            issues.append("Candidate hypotheses are weakly aligned with evidence.")
            suggestions.append("Bind each hypothesis to stronger evidence IDs or ask module 2 to supplement evidence.")
        if average_testability < 0.7:
            issues.append("Some hypotheses are not testable enough.")
            suggestions.append("Add clearer observations, variables, and validation data sources.")
        if diversity_score < 0.6:
            issues.append("Hypothesis types or variable combinations are not diverse enough.")
            suggestions.append("Generate mechanism, mediation, moderation, and comparison hypotheses.")
        if retry_notes:
            suggestions.append("The model needed repair attempts; keep JSON mode enabled when connecting Qwen.")

        return {
            "passed": overall_score >= self.config.threshold,
            "overall_score": overall_score,
            "threshold": self.config.threshold,
            "dimension_scores": {
                "hypothesis_count": round(hypothesis_count_score, 3),
                "diversity": round(diversity_score, 3),
                "average_testability": round(average_testability, 3),
                "evidence_alignment": round(average_alignment, 3),
                "average_relevance": round(average_relevance, 3),
                "average_risk": round(average_risk, 3),
                "variable_coverage": round(average_variable_coverage, 3),
                "evidence_keyword_overlap": round(average_evidence_overlap, 3),
                "gap_ratio": (audit or {}).get("gap_ratio", 0.0),
            },
            "hard_check_audit": (audit or {}).get("hard_checks", []),
            "issues": issues,
            "suggestions": suggestions,
        }

    def _response(
        self,
        input_data: dict[str, Any],
        payload: dict[str, Any],
        self_review: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "metadata": {
                "task_id": input_data["task_id"],
                "agent_id": self.config.agent_id,
                "stage": self.config.stage,
                "iteration": input_data["iteration"],
                "status": "success" if self_review["passed"] else "partial",
            },
            "payload": payload,
            "self_review": self_review,
        }

    def _failed_response(
        self,
        input_data: dict[str, Any],
        retry_notes: list[str],
        last_error: AgentOutputError | None,
    ) -> dict[str, Any]:
        return {
            "metadata": {
                "task_id": input_data["task_id"],
                "agent_id": self.config.agent_id,
                "stage": self.config.stage,
                "iteration": input_data["iteration"],
                "status": "failed",
            },
            "payload": {"hypothesis_cards": []},
            "self_review": {
                "passed": False,
                "overall_score": 0.0,
                "threshold": self.config.threshold,
                "dimension_scores": {},
                "issues": retry_notes or [str(last_error) if last_error else "Unknown generation failure."],
                "suggestions": [
                    "Retry with stricter JSON mode.",
                    "Check whether module 2 supplied sufficient evidence_cards and knowledge_gaps.",
                ],
            },
        }

    def _require_fields(
        self,
        data: dict[str, Any],
        fields: tuple[str, ...],
        label: str,
        error_cls: type[ValueError] = AgentInputError,
    ) -> None:
        missing = [field for field in fields if field not in data]
        if missing:
            raise error_cls(f"{label} missing required fields: {missing}.")

    def _extract_json_text(self, text: str) -> str:
        fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
        if fenced:
            return fenced.group(1).strip()
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last != -1 and last > first:
            return text[first : last + 1].strip()
        return text.strip()

    def _repair_json_text(self, text: str) -> str:
        repaired = text.strip()
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        if '"' not in repaired and "'" in repaired:
            repaired = repaired.replace("'", '"')
        return repaired

    def _split_prediction_text(self, text: str) -> list[str]:
        parts = re.split(r"[；;。\n]+", text)
        predictions = [part.strip(" ，,") for part in parts if part.strip(" ，,")]
        return predictions or [text]

    def _validate_score(self, value: Any, label: str) -> None:
        if not isinstance(value, (int, float)):
            raise AgentOutputError(f"{label} must be a number.")
        if value < 0 or value > 1:
            raise AgentOutputError(f"{label} must be between 0 and 1.")

    def _variable_coverage(self, card: dict[str, Any], input_data: dict[str, Any]) -> float:
        required_variables = [
            str(variable)
            for variable in card.get("target_variables", [])
            if isinstance(variable, str) and variable.strip()
        ]
        if not required_variables:
            required_variables = self._question_variable_names(input_data["question_card"])
        if not required_variables:
            return 1.0
        text = self._normalized_text(
            " ".join(
                [
                    str(card.get("statement", "")),
                    str(card.get("rationale", "")),
                    " ".join(str(v) for v in card.get("target_variables", [])),
                ]
            )
        )
        hits = sum(1 for variable in required_variables if self._concept_hit(variable, text))
        return hits / len(required_variables)

    def _evidence_overlap(self, card: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]) -> float:
        text = self._token_set(
            " ".join(
                [
                    str(card.get("statement", "")),
                    str(card.get("rationale", "")),
                    str(card.get("expected_observation", "")),
                    " ".join(str(v) for v in card.get("target_variables", [])),
                ]
            )
        )
        if not text:
            return 0.0
        overlaps: list[float] = []
        for evidence_id in card.get("based_on_evidence_ids", []):
            evidence = evidence_by_id.get(evidence_id, {})
            evidence_tokens = self._token_set(
                " ".join(
                    [
                        str(evidence.get("claim", "")),
                        str(evidence.get("summary", "")),
                        " ".join(str(v) for v in evidence.get("related_concepts", [])),
                    ]
                )
            )
            if evidence_tokens:
                overlaps.append(len(text & evidence_tokens) / len(evidence_tokens))
        return self._mean(overlaps)

    def _question_variable_names(self, question_card: dict[str, Any]) -> list[str]:
        raw_variables = question_card.get("key_variables", [])
        names: list[str] = []
        if isinstance(raw_variables, list):
            for item in raw_variables:
                if isinstance(item, dict):
                    name = item.get("name")
                    if name:
                        names.append(str(name))
                elif item:
                    names.append(str(item))
        if not names:
            raw_concepts = question_card.get("key_concepts", [])
            names = self._string_items(raw_concepts)
        return list(dict.fromkeys(name for name in names if name.strip()))

    def _string_items(self, value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [str(value[key]) for key in ("name", "normalized_name", "content") if value.get(key)]
        if isinstance(value, list):
            result: list[str] = []
            for item in value:
                result.extend(self._string_items(item))
            return result
        return []

    def _concept_hit(self, concept: str, normalized_text: str) -> bool:
        concept_norm = self._normalized_text(concept)
        if not concept_norm:
            return False
        if concept_norm in normalized_text:
            return True
        concept_tokens = self._token_set(concept)
        return bool(concept_tokens and concept_tokens <= self._token_set(normalized_text))

    def _normalized_text(self, text: str) -> str:
        return re.sub(r"\s+", "", text.lower())

    def _token_set(self, text: str) -> set[str]:
        text = text.lower()
        tokens: set[str] = set()
        for item in re.findall(r"[a-zA-Z0-9_]+", text):
            if len(item) > 1:
                tokens.add(item)
        for zh in re.findall(r"[\u4e00-\u9fff]+", text):
            if len(zh) <= 2:
                tokens.add(zh)
            else:
                tokens.add(zh)
                tokens.update(zh[i : i + 2] for i in range(len(zh) - 1))
                tokens.update(zh[i : i + 3] for i in range(len(zh) - 2))
        return tokens

    def _estimate_diversity(self, cards: list[dict[str, Any]]) -> float:
        hypothesis_types = {card.get("hypothesis_type") for card in cards if card.get("hypothesis_type")}
        variables = {
            variable
            for card in cards
            for variable in card.get("target_variables", [])
            if isinstance(variable, str)
        }
        type_score = min(1.0, len(hypothesis_types) / 3)
        variable_score = min(1.0, len(variables) / max(1, len(cards) * 2))
        return round((type_score + variable_score) / 2, 3)

    def _mean(self, values: Iterable[Any]) -> float:
        value_list = list(values)
        if not value_list:
            return 0.0
        return sum(float(value) for value in value_list) / len(value_list)

    def _schema_hint(self) -> dict[str, Any]:
        return {
            "hypothesis_cards": [
                {
                    "hypothesis_id": "hyp_001",
                    "statement": "string",
                    "hypothesis_type": "mechanism | causal | mediation | moderation | comparison",
                    "rationale": "string",
                    "based_on_evidence_ids": ["ev_001"],
                    "related_gap_ids": ["gap_001"],
                    "target_variables": ["string"],
                    "expected_observation": "string",
                    "predictions": ["string"],
                    "validation_idea": "string",
                    "risk_or_limitation": "string",
                    "initial_scores": {
                        "novelty": 0.0,
                        "testability": 0.0,
                        "relevance": 0.0,
                        "evidence_alignment": 0.0,
                        "risk": 0.0,
                    },
                    "hypothesis_scores": {
                        "novelty": 0.0,
                        "testability": 0.0,
                        "relevance": 0.0,
                        "evidence_alignment": 0.0,
                        "risk": 0.0,
                    },
                }
            ]
        }

    def _few_shot_example(self) -> dict[str, Any]:
        return {
            "hypothesis_cards": [
                {
                    "hypothesis_id": "hyp_001",
                    "statement": "Neuroinflammation may accelerate cognitive decline in Alzheimer's disease by promoting tau pathology spread.",
                    "hypothesis_type": "mechanism",
                    "rationale": "ev_001 links tau pathology to cognitive decline, ev_002 links neuroinflammation to tau pathology, and gap_001 states that the causal role of neuroinflammation in tau spread remains unclear.",
                    "based_on_evidence_ids": ["ev_001", "ev_002"],
                    "related_gap_ids": ["gap_001"],
                    "target_variables": ["neuroinflammation", "tau pathology", "cognitive decline"],
                    "expected_observation": "Inflammatory biomarkers should precede or accompany tau spreading and predict faster cognitive decline.",
                    "predictions": [
                        "Higher inflammatory biomarkers precede or accompany faster tau spreading.",
                        "Tau spreading mediates the relation between inflammation and cognitive decline.",
                    ],
                    "validation_idea": "Test longitudinal biomarker, tau PET, and cognitive score data with mediation or temporal prediction models.",
                    "risk_or_limitation": "Current evidence may be correlational, so causal direction requires intervention or longitudinal validation.",
                    "initial_scores": {
                        "novelty": 0.72,
                        "testability": 0.86,
                        "relevance": 0.91,
                        "evidence_alignment": 0.82,
                        "risk": 0.38,
                    },
                    "hypothesis_scores": {
                        "novelty": 0.72,
                        "testability": 0.86,
                        "relevance": 0.91,
                        "evidence_alignment": 0.82,
                        "risk": 0.38,
                    },
                }
            ]
        }


if __name__ == "__main__":
    print(
        "This file defines HypothesisGenerationAgent with Qwen/DashScope API support. "
        "Import the class and call run() with validated input data."
    )
