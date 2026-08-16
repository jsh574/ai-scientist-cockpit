"""问题理解 Agent 的集中配置。"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ProblemUnderstandingConfig:
    agent_id: str = "question_understanding_agent"
    stage: str = "question_understanding"
    max_output_retries: int = 2
    self_review_threshold: float = 0.75
    confidence_warning_threshold: float = 0.35
    history_limit: int = 5
    max_prompt_chars: int = 120_000
    state_mode: str = "sqlite"

    @classmethod
    def from_env(cls) -> "ProblemUnderstandingConfig":
        state_mode = os.getenv("PROBLEM_UNDERSTANDING_STATE_MODE", "sqlite").strip().lower()
        if state_mode not in {"sqlite", "explicit", "off"}:
            state_mode = "sqlite"
        return cls(
            max_output_retries=max(
                0, int(os.getenv("PROBLEM_UNDERSTANDING_OUTPUT_RETRIES", "2"))
            ),
            self_review_threshold=min(
                1.0,
                max(0.0, float(os.getenv("PROBLEM_UNDERSTANDING_REVIEW_THRESHOLD", "0.75"))),
            ),
            confidence_warning_threshold=min(
                1.0,
                max(0.0, float(os.getenv("PROBLEM_UNDERSTANDING_CONFIDENCE_THRESHOLD", "0.35"))),
            ),
            history_limit=max(
                1, int(os.getenv("PROBLEM_UNDERSTANDING_HISTORY_LIMIT", "5"))
            ),
            max_prompt_chars=max(
                1_000, int(os.getenv("PROBLEM_UNDERSTANDING_MAX_PROMPT_CHARS", "120000"))
            ),
            state_mode=state_mode,
        )
