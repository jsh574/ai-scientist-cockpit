from __future__ import annotations

import os

from backend.app.settings import _load_environment_files


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
        "TEST_ROOT_WINS=backend\n"
        "TEST_PLANNING_WINS=backend\n"
        "TEST_BACKEND_ONLY=backend-only\n",
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
