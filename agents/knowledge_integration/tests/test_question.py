import json
from pathlib import Path
from typing import Any

from knowledge_integration_agent import KnowledgeIntegrationAgent


def write_json_output(response: dict[str, Any], output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(response, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")


def print_json_progress(event: dict[str, Any]) -> None:
    print(json.dumps(event, ensure_ascii=False))


def build_request():
    return {
        "task_id": "task_consciousness_001",
        "stage": "knowledge_integration",
        "iteration": 1,
        "input": {
            "question_card": {
                "question_id": "q_consciousness_001",
                "original_question": "意识的生物学基础是什么？",
                "core_question": "意识产生和维持依赖哪些神经生物学机制、脑网络活动和信息处理过程？",
                "research_object": "意识的生物学基础",
                "domain": [
                    "神经科学",
                    "认知科学",
                    "脑科学",
                    "计算神经科学",
                ],
                "key_concepts": [
                    "consciousness",
                    "neural correlates of consciousness",
                    "global workspace theory",
                    "integrated information theory",
                    "thalamocortical networks",
                    "brain connectivity",
                    "attention and awareness",
                ],
                "key_variables": [
                    {
                        "name": "脑区功能连接",
                        "type": "observable_proxy",
                        "description": "不同脑区之间的同步、耦合和信息传递模式。",
                    },
                    {
                        "name": "丘脑-皮层环路活动",
                        "type": "mechanism_variable",
                        "description": "丘脑与皮层之间的交互活动，可能参与意识状态维持。",
                    },
                    {
                        "name": "意识水平",
                        "type": "dependent_variable",
                        "description": "清醒、睡眠、麻醉、昏迷等不同意识状态或意识内容可报告性。",
                    },
                    {
                        "name": "信息整合程度",
                        "type": "observable_proxy",
                        "description": "脑网络对分布式信息进行整合和广播的能力。",
                    },
                ],
                "sub_questions": [
                    "哪些脑区或脑网络活动与意识状态和意识内容最稳定相关？",
                    "丘脑-皮层网络在意识产生和维持中起什么作用？",
                    "全局工作空间理论和整合信息理论分别能解释哪些意识现象？",
                    "麻醉、睡眠、昏迷等状态下的神经活动变化如何揭示意识机制？",
                    "意识的神经相关物和真正的因果机制如何区分？",
                ],
                "research_scope": {
                    "included": [
                        "神经机制解释",
                        "意识神经相关物",
                        "脑网络和信息整合",
                        "可验证假设生成",
                    ],
                    "excluded": [
                        "纯哲学定义争论",
                        "宗教或玄学解释",
                        "个体临床诊断建议",
                    ],
                },
                "search_keywords": [
                    "biological basis of consciousness",
                    "neural correlates of consciousness",
                    "global neuronal workspace consciousness",
                    "integrated information theory consciousness",
                    "thalamocortical network consciousness",
                    "consciousness anesthesia sleep coma brain connectivity",
                    "attention awareness neural mechanisms",
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


if __name__ == "__main__":
    agent = KnowledgeIntegrationAgent()
    response = agent.run(build_request(), progress_callback=print_json_progress)
    write_json_output(response, Path(__file__).with_name("output.json"))
