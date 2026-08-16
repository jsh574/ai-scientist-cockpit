from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from problem_understanding.agent import ProblemUnderstandingAgent
from problem_understanding.prompts import OUTPUT_SCHEMA_HINT, SYSTEM_PROMPT, build_user_prompt
from problem_understanding.schema import UserInput
from problem_understanding.state_store import RoundStateStore


class StubLLM:
    mock = True
    model = "stub"

    def __init__(self, result: dict):
        self.result = result
        self.calls: list[tuple[str, str]] = []

    def chat_json(self, system_prompt: str, user_prompt: str) -> dict:
        self.calls.append((system_prompt, user_prompt))
        return dict(self.result)


BASE_INPUT = {
    "original_question": "How does factor A affect outcome B?",
    "question_description": "Existing explanation for the scientific problem.",
    "question_id": "q-state",
    "user_constraints": {"language": "en", "domain_preference": "medicine"},
}

FIRST_RAW = {
    "core_question": "How does factor A mechanistically affect outcome B?",
    "question_type": "mechanism",
    "domain": ["medicine"],
    "research_object": "factor A",
    "context": {
        "region": None,
        "time_scale": None,
        "spatial_scale": None,
        "conditions": [],
    },
    "key_concepts": ["factor A", "outcome B"],
    "key_variables": [
        {"name": "factor A", "role": "independent", "category": "factor"}
    ],
    "sub_questions": ["Is the association causal?"],
    "research_scope": {"included": ["mechanism"], "excluded": []},
    "search_keywords": ["factor A", "outcome B"],
    "verifiability": {
        "is_verifiable": True,
        "type": "observational",
        "checkpoints": [],
    },
    "assumptions": [],
    "confidence": 0.8,
}

SECOND_RAW = {
    "core_question": "How does factor A affect outcome B in older adults?",
    "revision_notes": [
        {"field": "core_question", "change": "Focused on older adults"}
    ],
    "confidence": 0.85,
}


class IterationStateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "rounds.sqlite3"

    def tearDown(self):
        self.temp_dir.cleanup()

    def agent(self, result: dict) -> tuple[ProblemUnderstandingAgent, StubLLM]:
        llm = StubLLM(result)
        return ProblemUnderstandingAgent(llm, RoundStateStore(self.db_path)), llm

    def run_first(self, task_id: str = "task-a") -> dict:
        agent, _ = self.agent(FIRST_RAW)
        return agent.run(BASE_INPUT, version=1, task_id=task_id)

    def test_first_round_prompt_is_unchanged(self):
        ui = UserInput(**BASE_INPUT)
        expected = "\n\n".join([
            "原始科学问题（置于三角括号内）：<<<How does factor A affect outcome B?>>>",
            "问题背景描述（来自大赛手册，可用于消歧和拆解）："
            "<<<Existing explanation for the scientific problem.>>>",
            "输出语言：en",
            "领域偏好：medicine",
            OUTPUT_SCHEMA_HINT,
        ])
        self.assertEqual(build_user_prompt(ui), expected)

        result = self.run_first()
        self.assertEqual(result["data"]["prompt_snapshot"]["user"], expected)
        self.assertEqual(result["data"]["round_snapshot"]["iteration"], 1)
        self.assertEqual(result["data"]["round_snapshot"]["user_feedback"], "")
        self.assertTrue(result["meta"]["state_persisted"])

    def test_next_round_recovers_all_required_context(self):
        first = self.run_first()
        agent, llm = self.agent(SECOND_RAW)
        second = agent.run(
            BASE_INPUT,
            version=2,
            feedback={"comment": "Focus on adults aged 65 and older."},
            task_id="task-a",
        )
        prompt = llm.calls[0][1]

        self.assertEqual(second["meta"]["history_source"], "state_store")
        self.assertIn("上一轮 System Prompt", prompt)
        self.assertIn("上一轮 User Prompt", prompt)
        self.assertIn("上一轮运行结果", prompt)
        self.assertIn("上一轮问题解释", prompt)
        self.assertIn(SYSTEM_PROMPT, prompt)
        self.assertIn(first["data"]["prompt_snapshot"]["user"], prompt)
        self.assertIn('"status": "ok"', prompt)
        self.assertIn(FIRST_RAW["core_question"], prompt)
        self.assertIn(BASE_INPUT["original_question"], prompt)
        self.assertIn(BASE_INPUT["question_description"], prompt)
        self.assertIn("Focus on adults aged 65 and older.", prompt)
        self.assertEqual(
            second["data"]["question_card"]["sub_questions"],
            first["data"]["question_card"]["sub_questions"],
        )
        snapshot = second["data"]["round_snapshot"]
        self.assertEqual(snapshot["prompt_snapshot"]["user"], prompt)
        self.assertNotIn("prompt_snapshot", snapshot["run_result"])
        self.assertNotIn("question_card", snapshot["run_result"])

    def test_same_iteration_retry_uses_previous_iteration(self):
        self.run_first()
        second_agent, _ = self.agent(SECOND_RAW)
        second_agent.run(
            BASE_INPUT,
            version=2,
            feedback={"comment": "First attempt."},
            task_id="task-a",
        )

        retry_agent, retry_llm = self.agent(SECOND_RAW)
        retry_agent.run(
            BASE_INPUT,
            version=2,
            feedback={"comment": "Retry round 2."},
            task_id="task-a",
        )
        prompt = retry_llm.calls[0][1]
        self.assertIn(FIRST_RAW["core_question"], prompt)
        self.assertNotIn(SECOND_RAW["core_question"], prompt)

    def test_tasks_are_isolated_and_explicit_history_wins(self):
        first = self.run_first("task-a")

        isolated_agent, isolated_llm = self.agent(FIRST_RAW)
        isolated = isolated_agent.run(BASE_INPUT, version=2, task_id="task-b")
        self.assertEqual(isolated["meta"]["history_source"], "none")
        self.assertNotIn("上一轮 User Prompt", isolated_llm.calls[0][1])

        explicit = first["data"]["round_snapshot"]
        explicit["prompt_snapshot"]["user"] = "EXPLICIT PREVIOUS PROMPT"
        explicit_input = dict(BASE_INPUT)
        explicit_input["prior_rounds"] = [explicit]
        explicit_agent, explicit_llm = self.agent(SECOND_RAW)
        result = explicit_agent.run(explicit_input, version=3, task_id="task-a")
        self.assertEqual(result["meta"]["history_source"], "input")
        self.assertIn("EXPLICIT PREVIOUS PROMPT", explicit_llm.calls[0][1])

    def test_reset_history_starts_a_clean_round(self):
        self.run_first()
        reset_input = dict(BASE_INPUT)
        reset_input["reset_history"] = True
        reset_agent, reset_llm = self.agent(FIRST_RAW)
        result = reset_agent.run(reset_input, version=1, task_id="task-a")
        self.assertEqual(result["meta"]["history_source"], "none")
        self.assertNotIn("上一轮 User Prompt", reset_llm.calls[0][1])

    def test_concurrent_first_rounds_keep_batch_path_working(self):
        agent, _ = self.agent(FIRST_RAW)

        def run_one(index: int) -> dict:
            user_input = dict(BASE_INPUT)
            user_input["question_id"] = f"q-{index}"
            user_input["original_question"] = f"Question {index}?"
            return agent.run(
                user_input,
                version=1,
                task_id=f"batch-task-{index}",
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run_one, range(16)))

        self.assertTrue(all(item["status"] == "ok" for item in results))
        self.assertTrue(all(item["meta"]["state_persisted"] for item in results))


if __name__ == "__main__":
    unittest.main()
