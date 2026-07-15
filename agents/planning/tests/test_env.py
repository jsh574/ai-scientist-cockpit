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
                "export DIFY_API_URL=\"http://from-dotenv.example\"",
                "export DIFY_API_KEY='from-dotenv-key'",
                "export DIFY_RESPONSE_MODE=streaming # inline comment",
                "BROKEN_LINE",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DIFY_API_KEY", "from-shell-key")
    monkeypatch.delenv("DIFY_API_URL", raising=False)
    monkeypatch.delenv("DIFY_RESPONSE_MODE", raising=False)

    importlib.reload(env_module)
    loaded = env_module.load_dotenv(dotenv)

    assert loaded == dotenv
    assert os.environ["DIFY_API_URL"] == "http://from-dotenv.example"
    assert os.environ["DIFY_API_KEY"] == "from-shell-key"
    assert os.environ["DIFY_RESPONSE_MODE"] == "streaming"


def test_ensure_dotenv_loaded_can_be_disabled_for_isolated_subprocess_tests(monkeypatch):
    monkeypatch.setenv("PLANNING_AGENT_SKIP_DOTENV", "1")
    monkeypatch.delenv("DIFY_API_URL", raising=False)

    importlib.reload(env_module)
    env_module.ensure_dotenv_loaded()

    assert "DIFY_API_URL" not in os.environ
