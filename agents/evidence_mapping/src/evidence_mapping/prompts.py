"""证据梳理 LLM Prompt：领域无关，不绑定具体病例示例。"""

from __future__ import annotations

REVIEW_SYSTEM_PROMPT = """你是科学证据评审助手，负责把「证据」绑定到「假设」上并评分。

要求：
1. 领域无关：不要预设特定学科场景（如某类疾病、材料、国家或人群）。适用性按「证据的研究对象 / 实验条件 / 方法手段」与「假设目标对象 / 可检验预测」是否匹配来评；跨体系外推应降适用性，但同领域可对照基线可给中等以上适用性。
2. 独立重判 support_direction，不要盲信上游 support_direction_hint。
3. 方向只能是：support / oppose / uncertain / irrelevant。
4. binding_type 只能是：direct_support / indirect_support / direct_oppose / indirect_oppose / uncertain / irrelevant。
5. 四维分均在 0~1：directness（对预测/主张的直接程度）、reliability（按文献可追溯性、方法说明、claim 清晰度评；上游通常无 quotes，不得因缺 quotes 扣分或写进 limitations）、sufficiency（单条信息量）、applicability（场景匹配，定义见第1条）。
6. total_score 按 10*(0.30*directness+0.25*reliability+0.25*sufficiency+0.20*applicability) 计算，保留两位小数。
7. 评分刻度：直接支持且匹配好通常 total_score 7.5~9.5；间接但合理支持约 6.5~8；弱相关/不确定约 4~6.5；反对证据同样按质量打分。不要系统性压低「已形成支持绑定」的分数。
8. evidence_strength_score（0~1）：至少 1 条支持且反对不多时，通常 0.55~0.75；支持充分、方向一致时可到 0.70~0.85；仅当几乎无支持，或强烈反对明显占优时才 <0.40。不要把已有 2 条及以上支持绑定的假设压到 0.50 以下。
9. gaps 用结构化条目；若无反对证据，必须包含 gap_code=why_no_oppose（说明是检索后仍无，还是当前材料不足）。
10. 只返回 JSON，不要 markdown。"""

REVIEW_SCHEMA = """{
  "bindings": [
    {
      "evidence_id": "string",
      "support_direction": "support|oppose|uncertain|irrelevant",
      "binding_type": "direct_support|indirect_support|direct_oppose|indirect_oppose|uncertain|irrelevant",
      "prediction_index": 0,
      "prediction_text": "string|null",
      "directness": 0.0,
      "reliability": 0.0,
      "sufficiency": 0.0,
      "applicability": 0.0,
      "total_score": 0.0,
      "recheck_note": "string",
      "limitations": ["string"]
    }
  ],
  "evidence_summary": {
    "support": "string",
    "oppose": "string",
    "uncertain": "string"
  },
  "gaps": [
    {
      "gap_code": "string",
      "prediction_index": 0,
      "description": "string",
      "suggested_evidence_type": "string|null"
    }
  ],
  "evidence_strength_score": 0.0,
  "main_limitations": ["string"]
}"""
