from __future__ import annotations

import os

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


def test_planning_status_requires_complete_scoped_workflow_credentials(monkeypatch):
    monkeypatch.setenv("DIFY_API_URL", "https://dify.example")
    for stage in "ABC":
        monkeypatch.setenv(f"DIFY_WORKFLOW_{stage}_API_KEY", f"key-{stage.lower()}")

    planning = Settings.from_env().source_status()["research_planning"]

    assert planning["credential_configured"] is True
    assert planning["ready"] is True


def test_planning_status_rejects_missing_workflow_c_key(monkeypatch):
    monkeypatch.setenv("DIFY_API_URL", "https://dify.example")
    monkeypatch.setenv("DIFY_WORKFLOW_A_API_KEY", "key-a")
    monkeypatch.setenv("DIFY_WORKFLOW_B_API_KEY", "key-b")
    monkeypatch.delenv("DIFY_WORKFLOW_C_API_KEY", raising=False)

    planning = Settings.from_env().source_status()["research_planning"]

    assert planning["credential_configured"] is False
    assert planning["ready"] is False


def test_planning_status_allows_per_workflow_urls(monkeypatch):
    monkeypatch.delenv("DIFY_API_URL", raising=False)
    for stage in "ABC":
        monkeypatch.setenv(f"DIFY_WORKFLOW_{stage}_API_URL", f"https://{stage}.example")
        monkeypatch.setenv(f"DIFY_WORKFLOW_{stage}_API_KEY", f"key-{stage.lower()}")

    assert Settings.from_env().source_status()["research_planning"]["ready"] is True
