import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml


def test_cli_writes_failed_response_when_dify_is_not_configured():
    output = Path("samples/test-artifacts") / f"cli-test-output-{os.getpid()}.json"
    polluted_env = os.environ.copy()
    polluted_env["DIFY_API_URL"] = "http://127.0.0.1:9"
    polluted_env["DIFY_WORKFLOW_C_API_KEY"] = "test-key"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "planning_agent.cli",
            "--sample",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=_without_dify_configuration(polluted_env),
    )

    assert result.returncode == 1
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["metadata"]["status"] == "failed"
    assert "Planning workflow chain is not configured" in data["self_review"]["issues"][0]


def _without_dify_configuration(env: dict[str, str]) -> dict[str, str]:
    clean_env = env.copy()
    for key in (
        "DIFY_API_URL",
        "DIFY_WORKFLOW_A_API_URL",
        "DIFY_WORKFLOW_A_API_KEY",
        "DIFY_WORKFLOW_B_API_URL",
        "DIFY_WORKFLOW_B_API_KEY",
        "DIFY_WORKFLOW_C_API_URL",
        "DIFY_WORKFLOW_C_API_KEY",
        "DIFY_CHAIN_USER",
        "DIFY_CHAIN_RESPONSE_MODE",
        "DIFY_CHAIN_TIMEOUT_SECONDS",
        "DIFY_RESPONSE_MODE",
        "DIFY_TIMEOUT_SECONDS",
        "DIFY_SHOW_PROGRESS",
    ):
        clean_env.pop(key, None)
    clean_env["PLANNING_AGENT_SKIP_DOTENV"] = "1"
    return clean_env


def test_cli_default_response_path_uses_output_dir_and_minute_timestamp():
    from planning_agent.cli import timestamped_response_path

    path = timestamped_response_path(datetime(2026, 7, 12, 22, 5))

    assert path == Path("samples/output/planning_response07_12-22_05.json")


def test_primary_dify_yml_file_exists():
    dsl_path = Path("dify/Research Planning Agent.yml")

    text = dsl_path.read_text(encoding="utf-8")

    assert "kind: app" in text
    assert "version: 0.6.0" in text
    assert "mode: workflow" in text
    assert "type: start" in text
    assert "type: llm" in text
    assert "type: end" in text
    assert "variable: plan_result" in text


def test_cli_prints_all_dify_targets_without_exposing_api_keys():
    env = os.environ.copy()
    env["DIFY_API_URL"] = "http://115.190.208.240:31880"
    for stage in "ABC":
        env[f"DIFY_WORKFLOW_{stage}_API_KEY"] = f"secret-test-key-{stage}"
    env["DIFY_CHAIN_USER"] = "research-planning-agent"

    result = subprocess.run(
        [sys.executable, "-m", "planning_agent.cli", "--print-targets"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    targets = json.loads(result.stdout)
    assert [target["name"] for target in targets] == [
        "workflow_a",
        "workflow_b",
        "workflow_c",
    ]
    assert all(target["configured"] for target in targets)
    assert all(target["api_key_present"] for target in targets)
    assert all(target["endpoint"].endswith("/v1/workflows/run") for target in targets)
    assert "secret-test-key" not in result.stdout


def test_primary_dify_yml_uses_single_hypothesis_contract():
    yml_path = Path("dify/Research Planning Agent.yml")

    text = yml_path.read_text(encoding="utf-8")

    assert "variable: hypothesis_evidence_package" in text
    assert "variable: hypothesis_evidence_packages" not in text
    assert "variable: plan_result" in text
    assert "experiment_planner_plan_result_v1" in text
    assert "不要输出 plans 数组" in text
    assert "structured_output_enabled: true" in text


def test_primary_dify_yml_has_fast_single_llm_planning_pipeline():
    yml_path = Path("dify/Research Planning Agent.yml")

    text = yml_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    graph = workflow["workflow"]["graph"]
    nodes = {node["id"]: node for node in graph["nodes"]}

    assert set(nodes) == {
        "start",
        "normalize_evidence",
        "full_plan",
        "final_contract",
        "end",
    }
    assert [node["data"]["type"] for node in graph["nodes"]].count("llm") == 1
    assert [(edge["source"], edge["target"]) for edge in graph["edges"]] == [
        ("start", "normalize_evidence"),
        ("normalize_evidence", "full_plan"),
        ("full_plan", "final_contract"),
        ("final_contract", "end"),
    ]
    assert nodes["full_plan"]["data"]["title"] == "Generate Final Plan Fast"
    completion = nodes["full_plan"]["data"]["model"]["completion_params"]
    assert completion["enable_thinking"] is False
    assert completion["response_format"] == "json_object"
    assert completion["max_tokens"] <= 8192
    prompts = "\n".join(item["text"] for item in nodes["full_plan"]["data"]["prompt_template"])
    assert "selected_design" in prompts
    assert "C-only" not in prompts
    assert "selected_design is required" in text
    assert nodes["final_contract"]["data"]["variables"][0]["value_selector"] == [
        "full_plan",
        "structured_output",
    ]
    assert [item["variable"] for item in nodes["start"]["data"]["variables"]] == [
        "task_id",
        "iteration",
        "hypothesis_id",
        "question_card",
        "hypothesis_evidence_package",
        "planning_constraints",
        "user_constraints",
    ]
    assert [item["variable"] for item in nodes["end"]["data"]["outputs"]] == [
        "plan_result",
        "contract_report",
    ]
    assert not {"evidence_brief", "plan_skeleton", "critic_repair"} & set(nodes)
    assert ".text#}}" not in text


def test_primary_dify_yml_avoids_manual_yaml_anchors_for_import_safety():
    yml_path = Path("dify/Research Planning Agent.yml")

    text = yml_path.read_text(encoding="utf-8")

    assert "&plan_result" not in text
    assert "*plan_result" not in text


def test_agents_file_documents_dify_dsl_rules_for_future_agents():
    agents_path = Path("AGENTS.md")

    text = agents_path.read_text(encoding="utf-8")

    assert "Dify Workflow DSL 规则" in text
    assert "data.type" in text
    assert "sourceType" in text
    assert "targetType" in text
    assert "value_selector" in text
    assert "variable_selector" in text
    assert "hypothesis_evidence_package" in text
    assert "plan_result" in text
    assert "YAML anchor/alias" in text


def test_short_sample_file_exists_and_is_smaller_than_full_sample():
    short_path = Path("samples/input/module5_input_short.json")
    full_path = Path("samples/input/module5_input_sample.json")

    short_data = json.loads(short_path.read_text(encoding="utf-8"))
    assert short_data["task_id"] == "task_short_001"
    assert short_data["user_constraints"]["max_hypotheses"] == 2
    assert short_path.stat().st_size < full_path.stat().st_size


def test_cli_sample_uses_short_sample_when_dify_is_not_configured():
    output = Path("samples/test-artifacts") / f"cli-test-output-{os.getpid()}.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "planning_agent.cli",
            "--sample",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=_without_dify_configuration(os.environ.copy()),
    )

    assert result.returncode == 1
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["metadata"]["task_id"] == "task_short_001"


def test_cli_full_sample_keeps_original_full_sample_when_dify_is_not_configured():
    output = Path("samples/test-artifacts") / f"cli-test-output-{os.getpid()}.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "planning_agent.cli",
            "--full-sample",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=_without_dify_configuration(os.environ.copy()),
    )

    assert result.returncode == 1
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["metadata"]["task_id"] == "task_demo_001"


def test_abc_chain_is_the_only_supported_runtime_path():
    assert not Path("planning_agent/dify_client.py").exists()
    assert not Path("dify/planning_agent_workflow.json").exists()

    runtime_contract = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            ".env.example",
            "planning_agent/service.py",
            "planning_agent/workflow_api.py",
        )
    )
    assert "DIFY_API_KEY" not in runtime_contract
