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


SCIENCE_125_ORIGIN_OF_LIFE_QUESTION = "地球生命是如何以及在哪里起源的？"

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
        "task_id": "task_origin_of_life_001",
        "stage": "knowledge_integration",
        "iteration": 1,
        "_feedback": "",
        "input": {
            "question_card": {
                "question_id": "q_origin_of_life_001",
                "original_question": SCIENCE_125_ORIGIN_OF_LIFE_QUESTION,
                "core_question": "早期地球环境中，哪些地球化学条件、能量来源和前生物化学过程可能共同促成了生命体系的起源？",
                "research_object": "地球生命起源",
                "domain": [
                    "生命起源研究",
                    "天体生物学",
                    "地球化学",
                    "前生物化学",
                ],
                "key_concepts": [
                    "前生物化学",
                    "核糖核酸世界",
                    "代谢优先模型",
                    "原始细胞",
                    "深海热液喷口",
                    "早期地球环境",
                ],
                "key_variables": [
                    {
                        "name": "有机分子合成条件",
                        "type": "independent_variable",
                        "description": "温度、酸碱度、氧化还原状态和原料分子供给等可能影响前生物有机分子的形成。",
                    },
                    {
                        "name": "自我复制体系形成",
                        "type": "dependent_variable",
                        "description": "能够保存信息并进行模板复制的分子体系是否可以在早期地球条件下出现。",
                    },
                    {
                        "name": "矿物表面催化作用",
                        "type": "mechanism_variable",
                        "description": "黏土矿物、硫化物矿物或其他矿物表面对有机反应、聚合和选择性富集的促进作用。",
                    },
                    {
                        "name": "原始环境能量来源",
                        "type": "observable_proxy",
                        "description": "热液梯度、紫外辐射、电化学梯度和火山活动等可作为驱动前生物反应的能量线索。",
                    },
                ],
                "sub_questions": [
                    "生命起源最可能发生在深海热液喷口、浅水池还是其他早期地球环境？",
                    "无机小分子如何逐步形成具有信息存储或自我复制能力的有机体系？",
                    "核糖核酸世界、代谢优先模型和脂质世界模型分别得到哪些证据支持？",
                    "矿物表面、温度梯度和氧化还原条件如何影响前生物反应路径？",
                    "最早生命活动的地质和同位素证据可以追溯到何时？",
                ],
                "research_scope": {
                    "included": [
                        "前生物化学过程",
                        "生命起源环境",
                        "早期自我复制体系",
                        "可验证的实验与地球化学证据",
                    ],
                    "excluded": [
                        "宗教解释",
                        "缺乏证据的生命起源断言",
                        "地外智慧生命社会推测",
                    ],
                },
                "search_keywords": [
                    "地球生命起源",
                    "前生物化学",
                    "核糖核酸世界假说",
                    "深海热液喷口生命起源",
                    "原始细胞形成",
                    "早期地球地球化学证据",
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


def test_chinese_request_uses_science_125_origin_of_life_question() -> None:
    request = build_request()
    question_card = request["input"]["question_card"]

    assert question_card["original_question"] == "地球生命是如何以及在哪里起源的？"
    assert question_card["core_question"].startswith("早期地球环境中")
    assert "生命起源研究" in question_card["domain"]
    assert "深海热液喷口" in question_card["key_concepts"]
    assert "地球生命起源" in question_card["search_keywords"]
    assert "consciousness" not in json.dumps(request, ensure_ascii=False).lower()


def test_chinese_request_keeps_required_protocol_field_names() -> None:
    request = build_request()

    assert request.keys() == {
        "task_id",
        "stage",
        "iteration",
        "_feedback",
        "input",
        "output_schema",
    }
    assert request["_feedback"] == ""
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


def test_timestamped_output_path_uses_expected_name() -> None:
    output_path = build_output_path(now=datetime(2026, 7, 21, 15, 30, 45))

    assert output_path.name == "output_chinese_20260721_153045.json"
    assert re.fullmatch(r"output_chinese_\d{8}_\d{6}\.json", output_path.name)


def test_write_json_output_writes_timestamped_utf8_file(tmp_path: Path) -> None:
    output_path = build_output_path(
        now=datetime(2026, 7, 21, 15, 30, 45),
        output_dir=tmp_path,
    )
    response = {"metadata": {"status": "success"}, "payload": {"问题": "生命起源"}}

    write_json_output(response, output_path)
    output_text = output_path.read_text(encoding="utf-8")

    assert output_path.name == "output_chinese_20260721_153045.json"
    assert json.loads(output_text) == response
    assert "生命起源" in output_text


def test_print_json_progress_shows_current_database_in_terminal(capsys) -> None:
    event = {
        "event": "retrieval_database_started",
        "component": "RetrievalService",
        "payload": {
            "database": "openalex",
            "query": "地球生命起源",
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
