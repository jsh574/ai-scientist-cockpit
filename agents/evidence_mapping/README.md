# 模块 4：证据梳理 Agent（Evidence Mapping）

负责人：吴泽宇  
版本：v0.1  
对齐文档：`数据规范_v0.1` 第 8 章

## 1. 模块职责

将每个候选假设与 **支持 / 反对 / 不确定** 证据绑定，形成可追踪证据链，并输出深度质量评审 `evidence_map`。

本模块：

- **会**独立重判 `support_direction`（不信任模块 2 预分类）
- **会**按预测做绑定、四维打分、缺口识别、回退建议

## 2. 目录结构

```text
evidence_mapping_agent/
├── README.md
├── requirements.txt
├── schemas/
│   └── evidence_map.schema.json
├── src/evidence_mapping/
│   ├── __init__.py
│   ├── models.py          # 输入输出 Pydantic 模型
│   ├── scorer.py          # 方向重判 + 四维评分
│   ├── validator.py       # 输出自检
│   └── agent.py           # 主流程
├── examples/
│   ├── mock_input.json    # 联调输入样例（AD 案例）
│   ├── run_demo.py        # 一键跑通
│   └── mock_output.json   # 运行后生成
└── tests/
    └── test_agent.py
```

## 3. 快速运行

```bash
cd evidence_mapping_agent
pip install -r requirements.txt
python examples/run_demo.py
python -m pytest tests -q
```

## 4. 与总控的接口

### 输入（总控裁剪后）

```json
{
  "task_id": "task_001",
  "stage": "evidence_mapping",
  "iteration": 1,
  "threshold": 7.0,
  "hypothesis_cards": [],
  "evidence_cards": [],
  "literature_cards": []
}
```

### 输出（统一外壳）

```json
{
  "metadata": { "agent_id": "evidence_mapping_agent", "stage": "evidence_mapping", "status": "success" },
  "payload": { "evidence_map": [ /* ... */ ] },
  "self_review": { "passed": true, "overall_score": 0.8, "issues": [], "suggestions": [] }
}
```

### 建议总控回退映射

| `verdict.rollback_target` | 动作 |
|---|---|
| `knowledge_integration` | 回退模块 2 补证据 |
| `hypothesis_generation` | 回退模块 3 改假设 |
| `none` | 进入模块 5（可带 limitations） |

## 5. 上游最小必填（请模块 2/3 确认）

**evidence_card**

- `evidence_id`（必需）
- `claim`（必需）
- `quotes`（强烈建议；无 quotes 会降权）
- `literature_id` / DOI/URL（建议）
- `support_direction_hint`（可选，仅作参考）

**hypothesis_card**

- `hypothesis_id`, `statement`（必需）
- `based_on_evidence_ids`（建议）
- `expected_observation` 或 `predictions[]`（强烈建议；用于缺口识别）
- `target_variables`（建议）

## 6. 处理流程

1. 证据绑定：`based_on_evidence_ids` + 全量证据扫描  
2. 证据验证：独立重判方向，写入 `recheck_delta`  
3. 质量评估：直接性 30% / 可靠性 25% / 充分性 25% / 适用性 20%  
4. 缺口识别：预测覆盖、因果链、反对证据缺失说明  
5. 综合判定：`evidence_strength_score` + `needs_more_evidence`  
6. 生成反馈：`detailed_review.verdict` 含回退建议  

## 7. 接入示例

```python
from evidence_mapping import EvidenceMappingAgent

agent = EvidenceMappingAgent()
response = agent.run_dict(total_control_input_slice)
# response["payload"]["evidence_map"] -> 写入 task_context.evidence_map
```

## 8. 当前实现说明

- v0.1 为**可运行规则引擎 + 结构化评审**，无需 API Key，便于总控/前端先联调。
- 后续可把 `recheck_direction` / 摘要生成替换为 LLM 调用，但 **JSON Schema 与外壳保持不变**。
- 示例案例与数据规范一致：阿尔茨海默病 / 神经炎症-Tau。
