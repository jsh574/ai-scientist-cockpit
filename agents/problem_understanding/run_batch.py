"""批量处理 125 个前沿科学问题，生成结构化 question_card。

特性:
  - 从 config.json 读取多个 LLM provider，自动故障切换
  - 线程池并行处理，加速批量生成
  - 输入同时包含问题标题 question_en 和问题描述 question_description_en
  - 无有效 API key 时自动降级为 mock 模式

输入: questions_125.json
输出: question_cards_125.json
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from problem_understanding.agent import ProblemUnderstandingAgent
from problem_understanding.llm_client import LLMClient

ROOT = Path(__file__).parent
INPUT_FILE = ROOT / "questions_125.json"
OUTPUT_FILE = ROOT / "question_cards_125.json"
CONFIG_FILE = ROOT / "config.json"


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def process_one(agent: ProblemUnderstandingAgent, q: dict, index: int) -> dict:
    qid = q.get("id", f"q_{index:03d}")
    question_text = q.get("question_en", "")
    question_description = q.get("question_description_en", "")
    discipline = q.get("discipline_en", "")

    user_input = {
        "original_question": question_text,
        "question_description": question_description,
        "question_id": qid,
        "user_constraints": {
            "language": "en",
            "domain_preference": discipline,
        },
    }

    result = agent.run(
        user_input=user_input,
        question_id=qid,
        task_id=f"batch-125-{qid}",
    )

    if result["status"] == "ok":
        card = result["data"]["question_card"]
        card["question_description_en"] = question_description
        card["discipline_en"] = discipline
        card["discipline_zh"] = q.get("discipline_zh", "")
        return {"status": "ok", "index": index, "qid": qid, "question": question_text, "card": card}

    return {"status": "error", "index": index, "qid": qid, "question": question_text, "error": result["error"]}


def main():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        questions = json.load(f)

    cfg = load_config().get("llm", {})
    providers = cfg.get("providers", [])
    max_workers = cfg.get("max_workers", 4)
    retry_count = cfg.get("retry_count", 2)
    timeout = cfg.get("timeout", 60)

    llm = LLMClient(
        providers=providers if providers else None,
        retry_count=retry_count,
        timeout=timeout,
    )
    agent = ProblemUnderstandingAgent(llm=llm)

    mode = "mock" if llm.mock else f"real ({', '.join(llm.provider_names)})"
    workers = 1 if llm.mock else max_workers
    print(f"[启动] 共 {len(questions)} 个问题")
    print(f"[模式] {mode}")
    print(f"[并行] {workers} 线程")
    print(f"[输入] {INPUT_FILE}")
    print(f"[输出] {OUTPUT_FILE}")
    print("-" * 60)

    results = []
    failed = []

    if workers <= 1:
        for i, q in enumerate(questions, 1):
            r = process_one(agent, q, i)
            if r["status"] == "ok":
                results.append(r["card"])
                print(f"  [{i:3d}/125] OK   {r['qid']}: {r['question'][:50]}")
            else:
                failed.append({"id": r["qid"], "error": r["error"]})
                print(f"  [{i:3d}/125] FAIL {r['qid']}: {r['error']['message']}")
    else:
        futures = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for i, q in enumerate(questions, 1):
                futures[pool.submit(process_one, agent, q, i)] = i

            for fut in as_completed(futures):
                r = fut.result()
                i = r["index"]
                if r["status"] == "ok":
                    results.append(r["card"])
                    print(f"  [{i:3d}/125] OK   {r['qid']}: {r['question'][:50]}")
                else:
                    failed.append({"id": r["qid"], "error": r["error"]})
                    print(f"  [{i:3d}/125] FAIL {r['qid']}: {r['error']['message']}")

    results.sort(key=lambda c: c.get("question_id", ""))

    print("-" * 60)
    print(f"[完成] 成功: {len(results)}, 失败: {len(failed)}")

    output = {
        "total": len(questions),
        "success": len(results),
        "failed": len(failed),
        "question_cards": results,
    }
    if failed:
        output["errors"] = failed

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[输出] 已写入 {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
