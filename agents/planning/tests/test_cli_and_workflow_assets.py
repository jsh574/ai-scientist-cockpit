import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def _without_model_configuration(env: dict[str, str]) -> dict[str, str]:
    clean_env = env.copy()
    for key in (
        "DASHSCOPE_API_KEY",
        "QWEN_API_KEY",
        "LLM_API_KEY",
        "DASHSCOPE_BASE_URL",
        "LLM_BASE_URL",
        "PLANNING_MODEL",
        "QWEN_MODEL",
        "LLM_MODEL",
        "PLANNING_SHOW_PROGRESS",
    ):
        clean_env.pop(key, None)
    clean_env["PLANNING_AGENT_SKIP_DOTENV"] = "1"
    return clean_env


def test_cli_writes_failed_response_when_bailian_is_not_configured():
    output = Path("samples/test-artifacts") / f"cli-test-output-{os.getpid()}.json"
    result = subprocess.run(
        [sys.executable, "-m", "planning_agent.cli", "--sample", "--output", str(output)],
        check=False,
        capture_output=True,
        text=True,
        env=_without_model_configuration(os.environ.copy()),
    )

    assert result.returncode == 1
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["metadata"]["status"] == "failed"
    assert "Planning protocol compiler is not configured" in data["self_review"]["issues"][0]


def test_cli_default_response_path_uses_output_dir_and_minute_timestamp():
    from planning_agent.cli import timestamped_response_path

    path = timestamped_response_path(datetime(2026, 7, 12, 22, 5))
    assert path == Path("samples/output/planning_response07_12-22_05.json")


def test_dify_yml_assets_are_preserved_only_as_migration_history():
    assets = sorted(Path("dify").glob("*.yml"))
    assert len(assets) == 3
    assert all("mode: workflow" in path.read_text(encoding="utf-8") for path in assets)
    readme = Path("dify/README.md").read_text(encoding="utf-8")
    assert "历史" in readme
    assert "运行依赖" in readme


def test_cli_prints_redacted_local_runtime():
    env = _without_model_configuration(os.environ.copy())
    env.update(
        {
            "DASHSCOPE_API_KEY": "secret-test-key",
            "DASHSCOPE_BASE_URL": "https://dashscope.example/compatible-mode/v1",
            "PLANNING_MODEL": "qwen-test",
        }
    )

    result = subprocess.run(
        [sys.executable, "-m", "planning_agent.cli", "--print-runtime"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    runtime = json.loads(result.stdout)
    assert [item["name"] for item in runtime] == [
        "draft", "review_methodology", "review_statistics",
        "review_feasibility", "synthesis", "repair",
    ]
    assert all(item["mode"] == "local_protocol_compiler" for item in runtime)
    assert all(item["model"] == "qwen-test" for item in runtime)
    assert all(item["api_key_present"] is True for item in runtime)
    assert all(item["max_parallel_calls"] == 1 for item in runtime)
    assert all(item["max_repair_attempts"] == 1 for item in runtime)
    assert "secret-test-key" not in result.stdout


def test_print_targets_remains_a_deprecated_alias_for_one_release():
    env = _without_model_configuration(os.environ.copy())
    env["DASHSCOPE_API_KEY"] = "secret-test-key"
    result = subprocess.run(
        [sys.executable, "-m", "planning_agent.cli", "--print-targets"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)[0]["name"] == "draft"
    assert "secret-test-key" not in result.stdout


def test_short_sample_file_exists_and_is_smaller_than_full_sample():
    short_path = Path("samples/input/module5_input_short.json")
    full_path = Path("samples/input/module5_input_sample.json")

    short_data = json.loads(short_path.read_text(encoding="utf-8"))
    assert short_data["task_id"] == "task_short_001"
    assert short_data["user_constraints"]["max_hypotheses"] == 2
    assert short_path.stat().st_size < full_path.stat().st_size


def test_cli_sample_uses_short_sample_when_model_is_not_configured():
    output = Path("samples/test-artifacts") / f"cli-short-{os.getpid()}.json"
    result = subprocess.run(
        [sys.executable, "-m", "planning_agent.cli", "--sample", "--output", str(output)],
        check=False,
        capture_output=True,
        text=True,
        env=_without_model_configuration(os.environ.copy()),
    )
    assert result.returncode == 1
    assert json.loads(output.read_text(encoding="utf-8"))["metadata"]["task_id"] == "task_short_001"


def test_cli_full_sample_keeps_original_full_sample_when_model_is_not_configured():
    output = Path("samples/test-artifacts") / f"cli-full-{os.getpid()}.json"
    result = subprocess.run(
        [sys.executable, "-m", "planning_agent.cli", "--full-sample", "--output", str(output)],
        check=False,
        capture_output=True,
        text=True,
        env=_without_model_configuration(os.environ.copy()),
    )
    assert result.returncode == 1
    assert json.loads(output.read_text(encoding="utf-8"))["metadata"]["task_id"] == "task_demo_001"


def test_local_protocol_compiler_is_the_only_supported_runtime_path():
    assert not Path("planning_agent/dify_client.py").exists()
    assert not Path("planning_agent/workflow_api.py").exists()
    runtime_contract = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "planning_agent/runtime.py",
            "planning_agent/stage_clients.py",
            "planning_agent/workflow_chain.py",
            "planning_agent/service.py",
        )
    )
    assert "DIFY_" not in runtime_contract
    assert "exec(" not in Path("planning_agent/local_nodes.py").read_text(encoding="utf-8")
    assert "eval(" not in Path("planning_agent/local_nodes.py").read_text(encoding="utf-8")
