from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from problem_understanding.adapter import ProblemUnderstandingAdapter
from problem_understanding.agent import ProblemUnderstandingAgent
from problem_understanding.config import ProblemUnderstandingConfig
from problem_understanding.state_store import RoundStateStore

from test_iteration_state import BASE_INPUT, FIRST_RAW


class SequenceLLM:
    mock = True
    model = "sequence-stub"

    def __init__(self, results):
        self.results = list(results)
        self.calls: list[tuple[str, str]] = []

    def chat_json(self, system_prompt: str, user_prompt: str) -> dict:
        self.calls.append((system_prompt, user_prompt))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return dict(result)


class BrokenStateStore:
    def latest_iteration(self, *args, **kwargs):
        raise OSError("state unavailable")

    def load_history(self, *args, **kwargs):
        raise OSError("state unavailable")

    def save(self, *args, **kwargs):
        raise OSError("state unavailable")

    def clear(self, *args, **kwargs):
        raise OSError("state unavailable")


class ProtocolAndQualityTests(unittest.TestCase):
    def explicit_config(self, **changes) -> ProblemUnderstandingConfig:
        values = {
            "state_mode": "explicit",
            "max_output_retries": 1,
        }
        values.update(changes)
        return ProblemUnderstandingConfig(**values)

    def test_protocol_adapter_matches_team_envelope(self):
        llm = SequenceLLM([FIRST_RAW])
        agent = ProblemUnderstandingAgent(llm, config=self.explicit_config())
        adapter = ProblemUnderstandingAdapter(agent)

        task_context = {
            "task_id": "task-protocol",
            "iteration": 1,
            "_feedback": "",
            "user_input": BASE_INPUT,
        }
        request = adapter.build_request(task_context)
        response = adapter.call(task_context)

        self.assertIn("_feedback", request)
        self.assertEqual(response["metadata"]["stage"], "question_understanding")
        self.assertEqual(response["metadata"]["status"], "success")
        self.assertEqual(
            response["payload"]["schema_version"],
            "problem_understanding_output_v1",
        )
        self.assertTrue(response["payload"]["question_card"])
        self.assertTrue(response["self_review"]["passed"])
        self.assertIn("verifiability", response["self_review"]["dimension_scores"])

    def test_invalid_protocol_request_returns_failed_envelope(self):
        llm = SequenceLLM([FIRST_RAW])
        agent = ProblemUnderstandingAgent(llm, config=self.explicit_config())
        response = agent.run_protocol({"task_id": "missing-input"})
        self.assertEqual(response["metadata"]["status"], "failed")
        self.assertFalse(response["self_review"]["passed"])
        self.assertEqual(llm.calls, [])

    def test_invalid_iteration_is_structured_error(self):
        llm = SequenceLLM([FIRST_RAW])
        agent = ProblemUnderstandingAgent(llm, config=self.explicit_config())
        response = agent.run(BASE_INPUT, version="not-an-integer")
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["error"]["code"], "INVALID_ITERATION")
        self.assertFalse(response["self_review"]["passed"])
        self.assertEqual(llm.calls, [])

    def test_empty_output_is_retried_with_validation_issues(self):
        llm = SequenceLLM([{}, FIRST_RAW])
        agent = ProblemUnderstandingAgent(llm, config=self.explicit_config())
        response = agent.run(BASE_INPUT, version=1)
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["meta"]["output_retry_count"], 1)
        self.assertEqual(len(llm.calls), 2)
        self.assertIn("上一尝试未通过输出校验", llm.calls[1][1])
        self.assertIn("LLM 输出为空 JSON object", llm.calls[1][1])

    def test_persistently_empty_output_fails_instead_of_being_normalized(self):
        llm = SequenceLLM([{}, {}])
        agent = ProblemUnderstandingAgent(llm, config=self.explicit_config())
        response = agent.run(BASE_INPUT, version=1)
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["error"]["code"], "OUTPUT_VALIDATION_FAILED")
        self.assertIsNone(response["data"])

    def test_llm_exception_is_retried_and_can_recover(self):
        llm = SequenceLLM([RuntimeError("temporary outage"), FIRST_RAW])
        agent = ProblemUnderstandingAgent(llm, config=self.explicit_config())
        response = agent.run(BASE_INPUT, version=1)
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["meta"]["output_retry_count"], 1)

    def test_prompt_limit_fails_without_silent_truncation(self):
        llm = SequenceLLM([FIRST_RAW])
        config = self.explicit_config(max_prompt_chars=200)
        agent = ProblemUnderstandingAgent(llm, config=config)
        response = agent.run(BASE_INPUT, version=1)
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["error"]["code"], "PROMPT_TOO_LARGE")
        self.assertTrue(response["error"]["recoverable"])
        self.assertEqual(llm.calls, [])

    def test_state_failure_warns_but_keeps_valid_card(self):
        llm = SequenceLLM([FIRST_RAW])
        config = ProblemUnderstandingConfig(state_mode="sqlite", max_output_retries=0)
        agent = ProblemUnderstandingAgent(llm, BrokenStateStore(), config=config)
        response = agent.run(BASE_INPUT, version=1, task_id="broken-state")
        warning_codes = {item["code"] for item in response["meta"]["warnings"]}
        self.assertEqual(response["status"], "ok")
        self.assertFalse(response["meta"]["state_persisted"])
        self.assertIn("STATE_RESET_FAILED", warning_codes)
        self.assertIn("STATE_SAVE_FAILED", warning_codes)

    def test_corrupt_snapshot_is_skipped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "rounds.sqlite3"
            store = RoundStateStore(db_path)
            first_agent = ProblemUnderstandingAgent(
                SequenceLLM([FIRST_RAW]),
                store,
                ProblemUnderstandingConfig(state_mode="sqlite", max_output_retries=0),
            )
            first_agent.run(BASE_INPUT, version=1, task_id="task-corrupt")

            fingerprint = store.question_fingerprint(BASE_INPUT["original_question"])
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    "INSERT INTO round_snapshots "
                    "(task_id, question_id, question_fingerprint, iteration, snapshot_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    ("task-corrupt", "q-state", fingerprint, 2, "{not valid json"),
                )
                conn.commit()

            history = store.load_history(
                "task-corrupt",
                "q-state",
                BASE_INPUT["original_question"],
                before_iteration=3,
            )
            self.assertEqual([item.iteration for item in history], [1])

    def test_explicit_mode_does_not_touch_state_provider(self):
        llm = SequenceLLM([FIRST_RAW])
        agent = ProblemUnderstandingAgent(
            llm,
            BrokenStateStore(),
            config=self.explicit_config(),
        )
        response = agent.run(BASE_INPUT, version=1)
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["meta"]["state_mode"], "explicit")
        self.assertFalse(response["meta"]["state_persisted"])
        self.assertNotIn("warnings", response["meta"])


if __name__ == "__main__":
    unittest.main()
