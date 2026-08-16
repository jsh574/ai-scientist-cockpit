import importlib
import os
from pathlib import Path

from planning_agent import env as env_module


def test_load_dotenv_supports_export_lines_without_overriding_existing_env(monkeypatch):
    artifact_dir = Path("samples/test-artifacts")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    dotenv = artifact_dir / "dotenv-test.env"
    dotenv.write_text(
        "\n".join(
            [
                "# comment",
                'export DASHSCOPE_BASE_URL="http://from-dotenv.example"',
                "export DASHSCOPE_API_KEY='from-dotenv-key'",
                "export PLANNING_SHOW_PROGRESS=true # inline comment",
                "BROKEN_LINE",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DASHSCOPE_API_KEY", "from-shell-key")
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)
    monkeypatch.delenv("PLANNING_SHOW_PROGRESS", raising=False)

    importlib.reload(env_module)
    loaded = env_module.load_dotenv(dotenv)

    assert loaded == dotenv
    assert os.environ["DASHSCOPE_BASE_URL"] == "http://from-dotenv.example"
    assert os.environ["DASHSCOPE_API_KEY"] == "from-shell-key"
    assert os.environ["PLANNING_SHOW_PROGRESS"] == "true"


def test_ensure_dotenv_loaded_can_be_disabled_for_isolated_subprocess_tests(monkeypatch):
    monkeypatch.setenv("PLANNING_AGENT_SKIP_DOTENV", "1")
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)

    importlib.reload(env_module)
    env_module.ensure_dotenv_loaded()

    assert "DASHSCOPE_BASE_URL" not in os.environ


def test_load_dotenv_merges_files_without_overriding_higher_priority_values(
    monkeypatch, tmp_path
):
    high_priority = tmp_path / "project.env"
    low_priority = tmp_path / "backend.env"
    high_priority.write_text(
        "DASHSCOPE_API_KEY=project-key\nPLANNING_MODEL=project-model\n",
        encoding="utf-8",
    )
    low_priority.write_text(
        "DASHSCOPE_API_KEY=backend-key\nPLANNING_MAX_RETRIES=2\n",
        encoding="utf-8",
    )
    for key in ("DASHSCOPE_API_KEY", "PLANNING_MODEL", "PLANNING_MAX_RETRIES"):
        monkeypatch.delenv(key, raising=False)

    importlib.reload(env_module)
    monkeypatch.setattr(env_module, "_find_env_files", lambda: [high_priority, low_priority])
    loaded = env_module.load_dotenv()

    assert loaded == high_priority
    assert os.environ["DASHSCOPE_API_KEY"] == "project-key"
    assert os.environ["PLANNING_MODEL"] == "project-model"
    assert os.environ["PLANNING_MAX_RETRIES"] == "2"


def test_default_dotenv_search_includes_backend_env():
    package_root = Path(env_module.__file__).resolve().parent.parent
    project_root = package_root.parents[1]

    assert (project_root / "backend" / ".env").resolve() in env_module._find_env_files()
