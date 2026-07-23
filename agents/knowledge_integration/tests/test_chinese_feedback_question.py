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


SCIENCE_125_LIFESPAN_QUESTION = "人类寿命能延长到什么程度？"

CHINESE_FEEDBACK = (
    "上一轮结果里关于人类最大寿命极限的文献不够，请优先补充《自然》《科学》和大型百岁老人队列研究；"
    "请增加一个子问题：健康寿命延长和最大寿命延长是否由相同机制控制；"
    "我提供一个待核验证据线索：雷帕霉素、热量限制和表观遗传重编程在模式动物中常被报告可以延缓衰老；"
    "文献卡片请更关注研究对象、样本来源和干预类型；"
    "证据卡片请区分人口统计学证据、动物实验机制证据和临床转化证据；"
    "知识空白部分请只给候选假设生成的方向和建议，不要直接写成具体假设。"
)

STAGE_COMPLETION_LABELS = {
    "feedback_routing_completed": "用户反馈路由完成",
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
            "[检索进度] 正在搜索数据库 "
            f"{payload.get('database', '')} | "
            f"查询 {payload.get('query_index', '')}/{payload.get('query_count', '')}: "
            f"{payload.get('query', '')}"
        )
    elif event_name == "retrieval_database_completed":
        print(
            "[检索进度] 完成搜索数据库 "
            f"{payload.get('database', '')} | "
            f"结果数 {payload.get('result_count', 0)}"
        )
    elif event_name == "retrieval_database_failed":
        print(
            "[检索进度] 数据库搜索失败 "
            f"{payload.get('database', '')} | "
            f"错误: {payload.get('error', '')}"
        )
    elif event_name == "feedback_routing_completed":
        directives = payload.get("feedback_directives", {})
        print(
            "[反馈进度] 用户反馈已拆分到子agent | "
            f"检索意见: {bool(directives.get('retrieval_directives'))} | "
            f"文献意见: {bool(directives.get('literature_directives'))} | "
            f"证据意见: {bool(directives.get('evidence_directives'))} | "
            f"知识空白意见: {bool(directives.get('gap_directives'))}"
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
        "task_id": "task_lifespan_feedback_001",
        "stage": "knowledge_integration",
        "iteration": 2,
        "_feedback": CHINESE_FEEDBACK,
        "input": {
            "question_card": {
                "question_id": "q_lifespan_extension_001",
                "original_question": SCIENCE_125_LIFESPAN_QUESTION,
                "core_question": "人类寿命和健康寿命可以通过哪些遗传、细胞、代谢和医学干预机制被延长，其上限受到哪些因素约束？",
                "research_object": "人类寿命延长与健康衰老机制",
                "domain": [
                    "衰老生物学",
                    "老年医学",
                    "人口统计学",
                    "转化医学",
                ],
                "key_concepts": [
                    "最大寿命",
                    "健康寿命",
                    "细胞衰老",
                    "端粒",
                    "表观遗传时钟",
                    "雷帕霉素",
                    "热量限制",
                    "百岁老人队列",
                ],
                "key_variables": [
                    {
                        "name": "衰老干预类型",
                        "type": "independent_variable",
                        "description": "包括饮食限制、药物干预、遗传调控、细胞重编程和生活方式干预等可能影响衰老进程的因素。",
                    },
                    {
                        "name": "最大寿命",
                        "type": "dependent_variable",
                        "description": "个体或人群在严格验证年龄记录下能够达到的最高存活年龄。",
                    },
                    {
                        "name": "健康寿命",
                        "type": "dependent_variable",
                        "description": "个体在没有严重慢性疾病和功能障碍状态下维持生活质量的年限。",
                    },
                    {
                        "name": "衰老生物标志物",
                        "type": "observable_proxy",
                        "description": "端粒长度、表观遗传年龄、炎症水平、细胞衰老标志物和代谢状态等可观测指标。",
                    },
                ],
                "sub_questions": [
                    "人类最大寿命是否存在稳定上限，人口统计学证据如何支持或反驳这一判断？",
                    "端粒缩短、DNA损伤、细胞衰老和表观遗传漂移如何共同限制寿命延长？",
                    "热量限制、雷帕霉素和代谢通路调控在模式动物中的寿命延长证据能否转化到人类？",
                    "百岁老人和超级百岁老人队列揭示了哪些遗传保护因素和环境因素？",
                    "健康寿命延长和最大寿命延长是否由相同机制控制？",
                ],
                "research_scope": {
                    "included": [
                        "衰老机制研究",
                        "寿命与健康寿命干预证据",
                        "百岁老人队列和人口统计学证据",
                        "可验证的人类与动物实验研究",
                    ],
                    "excluded": [
                        "商业抗衰产品宣传",
                        "缺乏可验证数据的长寿传说",
                        "无法追溯来源的个人养生经验",
                    ],
                },
                "search_keywords": [
                    "人类寿命延长",
                    "最大寿命 上限",
                    "健康寿命 衰老干预",
                    "百岁老人队列 衰老",
                    "端粒 表观遗传时钟 寿命",
                    "雷帕霉素 热量限制 衰老",
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


def test_feedback_request_uses_science_125_lifespan_question() -> None:
    request = build_request()
    question_card = request["input"]["question_card"]

    assert question_card["original_question"] == "人类寿命能延长到什么程度？"
    assert question_card["core_question"].startswith("人类寿命和健康寿命")
    assert "衰老生物学" in question_card["domain"]
    assert "健康寿命" in question_card["key_concepts"]
    assert "百岁老人队列 衰老" in question_card["search_keywords"]
    assert request["iteration"] == 2
    assert request["_feedback"] == CHINESE_FEEDBACK


def test_feedback_request_keeps_required_protocol_field_names() -> None:
    request = build_request()

    assert request.keys() == {
        "task_id",
        "stage",
        "iteration",
        "_feedback",
        "input",
        "output_schema",
    }
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


def test_feedback_text_contains_directives_for_each_subagent() -> None:
    feedback = build_request()["_feedback"]

    assert "优先补充《自然》《科学》和大型百岁老人队列研究" in feedback
    assert "请增加一个子问题" in feedback
    assert "文献卡片请更关注研究对象、样本来源和干预类型" in feedback
    assert "证据卡片请区分人口统计学证据、动物实验机制证据和临床转化证据" in feedback
    assert "知识空白部分请只给候选假设生成的方向和建议" in feedback


def test_timestamped_output_path_uses_existing_name_logic() -> None:
    output_path = build_output_path(now=datetime(2026, 7, 23, 10, 15, 30))

    assert output_path.name == "output_chinese_20260723_101530.json"
    assert re.fullmatch(r"output_chinese_\d{8}_\d{6}\.json", output_path.name)


def test_write_json_output_writes_timestamped_utf8_file(tmp_path: Path) -> None:
    output_path = build_output_path(
        now=datetime(2026, 7, 23, 10, 15, 30),
        output_dir=tmp_path,
    )
    response = {"metadata": {"status": "success"}, "payload": {"问题": "人类寿命延长"}}

    write_json_output(response, output_path)
    output_text = output_path.read_text(encoding="utf-8")

    assert output_path.name == "output_chinese_20260723_101530.json"
    assert json.loads(output_text) == response
    assert "人类寿命延长" in output_text


def test_print_json_progress_shows_feedback_routing_and_current_database(capsys) -> None:
    feedback_event = {
        "event": "feedback_routing_completed",
        "component": "FeedbackRouter",
        "payload": {
            "feedback_directives": {
                "retrieval_directives": {"must_include_sources": ["《自然》"]},
                "literature_directives": {"focus": ["样本来源"]},
                "evidence_directives": {"classify_by": ["人口统计学证据"]},
                "gap_directives": {"avoid_specific_hypotheses": True},
            }
        },
    }
    database_event = {
        "event": "retrieval_database_started",
        "component": "RetrievalService",
        "payload": {
            "database": "openalex",
            "query": "人类寿命延长 最大寿命 上限",
            "query_index": 1,
            "query_count": 5,
        },
    }

    print_json_progress(feedback_event)
    print_json_progress(database_event)
    output_lines = capsys.readouterr().out.splitlines()

    assert "用户反馈已拆分到子agent" in output_lines[0]
    assert "正在搜索数据库 openalex" in output_lines[2]
    assert json.loads(output_lines[1]) == feedback_event
    assert json.loads(output_lines[-1]) == database_event


if __name__ == "__main__":
    agent = KnowledgeIntegrationAgent()
    response = agent.run(build_request(), progress_callback=print_json_progress)
    output_path = build_output_path()
    write_json_output(response, output_path)
    print(json.dumps({"final_output_path": str(output_path)}, ensure_ascii=False))
