import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from knowledge_integration_agent import KnowledgeIntegrationAgent


SCIENCE_125_UNIVERSE_COMPOSITION_QUESTION = "宇宙是由什么构成的？"

STAGE_COMPLETION_LABELS = {
    "retrieval_completed": "文献与数据源检索完成",
    "literature_extraction_completed": "文献卡片抽取完成",
    "evidence_extraction_completed": "证据卡片抽取完成",
    "gap_synthesis_completed": "知识空白识别完成",
}


def write_json_output(response: dict[str, Any], output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(response, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")


def print_json_progress(event: dict[str, Any]) -> None:
    event_name = event.get("event")
    payload = event.get("payload", {})
    if event_name == "retrieval_database_started":
        print(
            "[检索进度] 正在搜索数据库: "
            f"{payload.get('database', '')} | "
            f"查询 {payload.get('query_index', '')}/{payload.get('query_count', '')}: "
            f"{payload.get('query', '')}"
        )
    elif event_name == "retrieval_database_completed":
        print(
            "[检索进度] 完成搜索数据库: "
            f"{payload.get('database', '')} | "
            f"结果数: {payload.get('result_count', 0)}"
        )
    elif event_name == "retrieval_database_failed":
        print(
            "[检索进度] 数据库搜索失败: "
            f"{payload.get('database', '')} | "
            f"错误: {payload.get('error', '')}"
        )
    elif event_name in STAGE_COMPLETION_LABELS:
        print(f"[阶段完成] {STAGE_COMPLETION_LABELS[event_name]}")
    print(json.dumps(event, ensure_ascii=False))


def build_output_path(
    now: datetime | None = None,
    output_dir: Path | None = None,
) -> Path:
    timestamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    directory = output_dir or Path(__file__).parent
    return directory / f"output_chinese_{timestamp}.json"


def build_request() -> dict[str, Any]:
    return {
        "task_id": "task_universe_composition_001",
        "stage": "knowledge_integration",
        "iteration": 1,
        "input": {
            "question_card": {
                "question_id": "q_universe_composition_001",
                "original_question": SCIENCE_125_UNIVERSE_COMPOSITION_QUESTION,
                "core_question": "宇宙中普通物质、暗物质和暗能量的组成比例、物理本质及其观测证据是什么？",
                "research_object": "宇宙物质与能量组成",
                "domain": [
                    "宇宙学",
                    "天体物理学",
                    "粒子物理学",
                    "观测天文学",
                ],
                "key_concepts": [
                    "暗物质",
                    "暗能量",
                    "重子物质",
                    "宇宙微波背景辐射",
                    "引力透镜",
                    "星系团",
                    "大尺度结构",
                ],
                "key_variables": [
                    {
                        "name": "普通物质密度参数",
                        "type": "observable_proxy",
                        "description": "由宇宙微波背景辐射、重子声学振荡和元素丰度等约束的重子物质占比。",
                    },
                    {
                        "name": "暗物质密度参数",
                        "type": "dependent_variable",
                        "description": "通过星系旋转曲线、星系团动力学、引力透镜和大尺度结构推断的不可见物质成分。",
                    },
                    {
                        "name": "暗能量状态方程参数",
                        "type": "mechanism_variable",
                        "description": "描述暗能量压强与能量密度关系的参数，可用于区分宇宙常数和动力学暗能量模型。",
                    },
                    {
                        "name": "宇宙膨胀率",
                        "type": "observable_proxy",
                        "description": "由超新星、宇宙微波背景辐射和重子声学振荡等观测约束的哈勃膨胀历史。",
                    },
                ],
                "sub_questions": [
                    "现有观测如何约束普通物质、暗物质和暗能量在宇宙中的比例？",
                    "星系旋转曲线、引力透镜和宇宙大尺度结构分别为暗物质提供了哪些证据？",
                    "宇宙加速膨胀的观测证据如何支持暗能量或宇宙常数模型？",
                    "暗物质粒子候选体和修正引力模型各自面临哪些证据限制？",
                    "哈勃常数张力和宇宙学参数张力会如何影响对宇宙组成的解释？",
                ],
                "research_scope": {
                    "included": [
                        "宇宙学参数约束",
                        "暗物质与暗能量观测证据",
                        "粒子物理候选解释",
                        "可验证的天文观测和统计证据",
                    ],
                    "excluded": [
                        "缺乏观测依据的宇宙起源叙事",
                        "纯哲学宇宙论讨论",
                        "科幻设定或非科学解释",
                    ],
                },
                "search_keywords": [
                    "宇宙组成 暗物质 暗能量",
                    "宇宙微波背景辐射 宇宙学参数",
                    "引力透镜 暗物质 证据",
                    "星系旋转曲线 暗物质",
                    "超新星 宇宙加速膨胀 暗能量",
                    "哈勃常数张力 宇宙学模型",
                ],
            },
            "search_policy": {
                "max_papers": 8,
                "max_queries": 5,
                "per_client_limit": 2,
                "min_recent_papers": 3,
                "must_verify_sources": True,
                "forbidden_actions": [
                    "invent_references",
                    "invent_dataset_url",
                    "claim_unverified_source_as_real",
                ],
            },
        },
        "output_schema": "knowledge_integration.schema.json",
    }


def test_chinese_request_uses_science_125_universe_composition_question() -> None:
    request = build_request()
    question_card = request["input"]["question_card"]

    assert question_card["original_question"] == "宇宙是由什么构成的？"
    assert question_card["core_question"].startswith("宇宙中普通物质")
    assert "宇宙学" in question_card["domain"]
    assert "暗物质" in question_card["key_concepts"]
    assert "宇宙组成 暗物质 暗能量" in question_card["search_keywords"]
    assert "生命起源" not in json.dumps(request, ensure_ascii=False)


def test_chinese_cosmology_request_keeps_required_protocol_field_names() -> None:
    request = build_request()

    assert request.keys() == {"task_id", "stage", "iteration", "input", "output_schema"}
    assert request["input"].keys() == {"question_card", "search_policy"}
    assert request["input"]["question_card"].keys() == {
        "question_id",
        "original_question",
        "core_question",
        "research_object",
        "domain",
        "key_concepts",
        "key_variables",
        "sub_questions",
        "research_scope",
        "search_keywords",
    }
    assert request["input"]["search_policy"]["forbidden_actions"] == [
        "invent_references",
        "invent_dataset_url",
        "claim_unverified_source_as_real",
    ]


def test_timestamped_output_path_uses_existing_name_logic() -> None:
    output_path = build_output_path(now=datetime(2026, 7, 21, 16, 55, 30))

    assert output_path.name == "output_chinese_20260721_165530.json"
    assert re.fullmatch(r"output_chinese_\d{8}_\d{6}\.json", output_path.name)


def test_write_json_output_writes_timestamped_utf8_file(tmp_path: Path) -> None:
    output_path = build_output_path(
        now=datetime(2026, 7, 21, 16, 55, 30),
        output_dir=tmp_path,
    )
    response = {"metadata": {"status": "success"}, "payload": {"问题": "宇宙组成"}}

    write_json_output(response, output_path)
    output_text = output_path.read_text(encoding="utf-8")

    assert output_path.name == "output_chinese_20260721_165530.json"
    assert json.loads(output_text) == response
    assert "宇宙组成" in output_text


def test_print_json_progress_shows_current_database_in_terminal(capsys) -> None:
    event = {
        "event": "retrieval_database_started",
        "component": "RetrievalService",
        "payload": {
            "database": "openalex",
            "query": "宇宙组成 暗物质 暗能量",
            "query_index": 1,
            "query_count": 5,
        },
    }

    print_json_progress(event)
    output_lines = capsys.readouterr().out.splitlines()

    assert "正在搜索数据库: openalex" in output_lines[0]
    assert json.loads(output_lines[-1]) == event


if __name__ == "__main__":
    agent = KnowledgeIntegrationAgent()
    response = agent.run(build_request(), progress_callback=print_json_progress)
    output_path = build_output_path()
    write_json_output(response, output_path)
    print(json.dumps({"final_output_path": str(output_path)}, ensure_ascii=False))
