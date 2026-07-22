# 候选假设生成 Agent

本模块负责在“问题理解”和“知识整合”之后，基于 `question_card`、`evidence_cards` 和 `knowledge_gaps` 生成候选科学假设。

它不负责最终证据强度判断。最终支持、反对、证据不足等判断由下游“证据梳理 Agent”完成。本模块只负责给出可检查的假设来源说明，避免把完全无证据挂钩的假设交给下游。

## 核心职责

1. 根据科学问题中的核心变量生成候选假设。
2. 将每条假设绑定到已有证据和知识空白。
3. 输出可观测预测和初步验证思路。
4. 对假设进行模块内自评和硬约束检查。
5. 可选调用独立评审模型，对生成结果进行二次审查。
6. 接收总控传入的 `revision_feedback`，在下一轮中修正假设。

## 输入字段

```json
{
  "task_id": "task_001",
  "iteration": 1,
  "question_card": {
    "core_question": "string",
    "research_object": "string",
    "key_concepts": ["string"],
    "key_variables": ["string"]
  },
  "evidence_cards": [
    {
      "evidence_id": "ev_001",
      "claim": "string",
      "summary": "string",
      "related_concepts": ["string"]
    }
  ],
  "knowledge_gaps": [
    {
      "gap_id": "gap_001",
      "description": "string",
      "gap_type": "mechanism_unknown",
      "related_concepts": ["string"],
      "related_evidence_ids": ["ev_001"],
      "importance_score": 0.8,
      "why_it_matters_for_hypothesis_generation": "string"
    }
  ],
  "user_constraints": {
    "max_hypotheses": 5,
    "language": "zh",
    "revision_feedback": "optional controller or downstream feedback"
  }
}
```

## 输出字段

核心输出位于：

```json
{
  "payload": {
    "hypothesis_cards": []
  },
  "self_review": {}
}
```

每条 `hypothesis_card` 包含：

```json
{
  "hypothesis_id": "hyp_001",
  "statement": "假设陈述",
  "hypothesis_type": "mechanism | causal | mediation | moderation | comparison",
  "rationale": "证据指出什么；知识空白是什么；因此提出什么假设",
  "based_on_evidence_ids": ["ev_001"],
  "evidence_bindings": [
    {
      "evidence_id": "ev_001",
      "used_as": "hypothesis_source",
      "linked_variable": "变量名",
      "inference_bridge": "说明这条证据如何启发当前假设"
    }
  ],
  "related_gap_ids": ["gap_001"],
  "target_variables": ["变量A", "变量B"],
  "expected_observation": "如果假设成立，应该观察到什么",
  "predictions": ["可检查预测"],
  "validation_idea": "验证思路",
  "risk_or_limitation": "风险或局限",
  "initial_scores": {
    "novelty": 0.0,
    "testability": 0.0,
    "relevance": 0.0,
    "evidence_alignment": 0.0,
    "risk": 0.0
  },
  "hypothesis_scores": {
    "novelty": 0.0,
    "testability": 0.0,
    "relevance": 0.0,
    "evidence_alignment": 0.0,
    "risk": 0.0
  }
}
```

## 新增的证据来源说明

`evidence_bindings` 是本次优化的重点。

它不是证据梳理结论，也不表示最终支持强度。它只说明：

- 这条假设为什么引用某条证据；
- 证据和哪个变量相关；
- 证据如何启发当前假设。

下游证据梳理 Agent 可以据此判断是否存在“假引用证据”或“证据不足”。

## 模块内审核

本模块包含三层审核：

1. JSON 和字段完整性检查。
2. 代码硬约束检查，包括变量覆盖率、证据关键词命中、知识空白关联、证据绑定完整性。
3. 可选独立评审模型，对假设进行审稿式评价。

独立评审只允许依据输入材料评价，不应引入输入之外的强制标准。若独立评审调用失败，模块不会崩溃，会在 `self_review.independent_eval` 中记录不可用原因。

## 对知识空白字段的使用

第二部分知识整合 Agent 的 `knowledge_gaps` 如果提供了以下字段，本模块会优先利用：

- `gap_type`：辅助判断假设类型，如机制未知、因果不确定、数据缺失或方法限制。
- `related_evidence_ids`：优先作为该 gap 对应假设的证据来源。
- `importance_score`：用于模块自评中的 gap 重要性审计。
- `why_it_matters_for_hypothesis_generation`：要求模型在 `rationale` 中体现该 gap 为什么值得生成假设。

当某个假设选择了一个带有 `related_evidence_ids` 的 gap 时，`based_on_evidence_ids` 应尽量与这些证据 ID 有交集，否则会被判为 gap-证据链较弱。

## 环境变量

```powershell
$env:DASHSCOPE_API_KEY="你的 API Key"
$env:DASHSCOPE_BASE_URL="你的百炼兼容接口地址"
$env:QWEN_MODEL="qwen-plus"
```

总控如果覆盖配置，建议不要把 `HYPOTHESIS_MIN_EVIDENCE_OVERLAP` 设为 `0`。建议值：

```powershell
$env:HYPOTHESIS_MIN_EVIDENCE_OVERLAP="0.08"
```

质量稳定后可以提高到 `0.12` 或 `0.15`。

## 单独检查

在项目根目录运行：

```powershell
python -m py_compile agents\hypothesis_generation\hypothesis_generation_agent.py
```

完整后端测试：

```powershell
python -m unittest discover -s backend\tests -v
```

## 常见问题

1. 生成结果引用了证据 ID，但证据梳理模块认为支持强度为 0。

   优先检查 `evidence_bindings.inference_bridge` 是否真正解释了证据和假设之间的推导关系，以及假设的预测是否写得过于具体。

2. 假设看起来很通顺，但评分不高。

   通常是因为变量覆盖不足、预测不可检查、知识空白绑定不明确，或独立评审认为证据链太弱。

3. 自动执行时多了一次模型调用。

   这是独立评审调用，用于降低单次自评分过高的问题。可以通过配置关闭，但比赛展示建议保留。
