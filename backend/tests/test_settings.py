from __future__ import annotations

import os

from backend.app.main import health
from backend.app.settings import Settings, _load_environment_files


def test_load_environment_files_uses_documented_precedence(tmp_path, monkeypatch):
    planning_dir = tmp_path / "agents" / "planning"
    backend_dir = tmp_path / "backend"
    planning_dir.mkdir(parents=True)
    backend_dir.mkdir()

    (tmp_path / ".env").write_text(
        "TEST_ROOT_WINS=root\nTEST_PROCESS_WINS=root\n",
        encoding="utf-8",
    )
    (planning_dir / ".env").write_text(
        "export TEST_ROOT_WINS=planning\n"
        "export TEST_PLANNING_WINS=planning\n"
        "export TEST_PLANNING_ONLY=planning-only\n",
        encoding="utf-8",
    )
    (backend_dir / ".env").write_text(
        "TEST_ROOT_WINS=backend\nTEST_PLANNING_WINS=backend\nTEST_BACKEND_ONLY=backend-only\n",
        encoding="utf-8",
    )

    for name in (
        "TEST_ROOT_WINS",
        "TEST_PLANNING_WINS",
        "TEST_PLANNING_ONLY",
        "TEST_BACKEND_ONLY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TEST_PROCESS_WINS", "process")

    _load_environment_files(tmp_path)

    assert os.getenv("TEST_PROCESS_WINS") == "process"
    assert os.getenv("TEST_ROOT_WINS") == "root"
    assert os.getenv("TEST_PLANNING_WINS") == "planning"
    assert os.getenv("TEST_PLANNING_ONLY") == "planning-only"
    assert os.getenv("TEST_BACKEND_ONLY") == "backend-only"


def test_planning_status_requires_a_bailian_compatible_credential(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

    planning = Settings.from_env().source_status()["research_planning"]

    assert planning["credential_configured"] is True
    assert planning["ready"] is True
    assert planning["mode"] == "local_protocol_compiler"


def test_planning_status_rejects_missing_model_credential(monkeypatch):
    for name in ("DASHSCOPE_API_KEY", "QWEN_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    planning = Settings.from_env().source_status()["research_planning"]

    assert planning["credential_configured"] is False
    assert planning["ready"] is False


def test_planning_status_accepts_shared_llm_credential(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    assert Settings.from_env().source_status()["research_planning"]["ready"] is True


def test_health_exposes_local_planning_runtime_without_dify_fields(monkeypatch):
    monkeypatch.setenv("PLANNING_MODEL", "qwen-planning-test")
    monkeypatch.setenv("QWEN_ENABLE_THINKING", "true")

    payload = health()

    assert payload["planning_runtime"] == {
        "mode": "local_protocol_compiler",
        "model": "qwen-planning-test",
        "stages": [
            "draft", "review_methodology", "review_statistics",
            "review_feasibility", "synthesis", "repair_optional",
        ],
        "thinking_enabled_for": [
            "draft", "review_methodology", "review_statistics", "review_feasibility",
        ],
        "synthesis_thinking": False,
    }
    assert set(payload["model_policy"]) == {"supported_fields"}
