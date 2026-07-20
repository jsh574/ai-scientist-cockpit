"""运行模块 4 Demo：读取 mock_input，输出 evidence_map。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evidence_mapping import EvidenceMappingAgent  # noqa: E402


def main() -> None:
    input_path = Path(__file__).with_name("mock_input.json")
    output_path = Path(__file__).with_name("mock_output.json")
    data = json.loads(input_path.read_text(encoding="utf-8"))

    agent = EvidenceMappingAgent()
    response = agent.run(data)
    out = response.model_dump(mode="json")
    output_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    backend = out["self_review"]["dimension_scores"].get("scoring_backend_llm", 0)
    mode = "llm" if backend >= 1.0 else "rules(fallback or no-key)"
    print(f"=== scoring mode: {mode} (config={agent.scoring_mode}) ===")
    print(
        "提示：配置 DASHSCOPE_API_KEY / QWEN_API_KEY / LLM_API_KEY 后默认走 LLM；"
        "也可设 EVIDENCE_MAPPING_MODE=rules 强制规则兜底。"
    )
    print("=== metadata ===")
    print(json.dumps(out["metadata"], ensure_ascii=False, indent=2))
    print("\n=== self_review ===")
    print(json.dumps(out["self_review"], ensure_ascii=False, indent=2))
    print("\n=== evidence_map 摘要 ===")
    for item in out["payload"]["evidence_map"]:
        v = item["detailed_review"]["verdict"]
        print(
            f"- {item['hypothesis_id']}: strength={item['evidence_strength_score']}, "
            f"passed={v['passed']}, needs_more={item['needs_more_evidence']}, "
            f"rollback={v['rollback_target']}"
        )
        print(f"  support={item['supporting_evidence_ids']}")
        print(f"  oppose={item['opposing_evidence_ids']}")
        print(f"  uncertain={item['uncertain_evidence_ids']}")
        print(f"  reason={v['reason']}")
    print(f"\n完整输出已写入: {output_path}")


if __name__ == "__main__":
    main()
