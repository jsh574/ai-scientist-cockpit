# 模块 4：证据梳理 Agent（Evidence Mapping）

负责人：吴泽宇  
版本：v0.2
对齐文档：`数据规范_v0.1` 第 8 章

## 1. 模块职责

将每个候选假设与 **支持 / 反对 / 不确定** 证据绑定，形成可追踪证据链，并输出深度质量评审 `evidence_map`。

本模块：

- **会**对每条假设独立评审（不是四选一；下游计划模块再按强度择优）
- **会**独立重判 `support_direction`（不信任模块 2 预分类）
- **会**按预测做绑定、四维打分、缺口识别、回退建议
- **默认走 LLM**；无 Key / 失败时规则引擎兜底

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
│   ├── prompts.py         # LLM 评审 Prompt（领域无关）
│   ├── llm.py             # OpenAI 兼容客户端
│   ├── llm_review.py      # LLM 评审解析与组装
│   ├── scorer.py          # 规则引擎：方向重判 + 四维评分（兜底）
│   ├── agent_helpers.py   # 缺口 / verdict 等共用逻辑
│   ├── validator.py       # 输出自检
│   └── agent.py           # 主流程 + Self 自评
├── examples/
│   ├── mock_input.json    # 联调输入样例
│   ├── run_demo.py        # 一键跑通
│   ├── adapter_example.py # 总控适配示例
│   └── mock_output.json   # 运行后生成
└── tests/
    └── test_agent.py
```

Cockpit 内镜像路径：`ai-scientist-cockpit/agents/evidence_mapping/`（逻辑应保持同步）。

## 3. 快速运行

```bash
cd evidence_mapping_agent
pip install -r requirements.txt
python examples/run_demo.py
python -m pytest tests -q
```

有 Key 时 Demo 默认走 LLM（每个假设一次调用，耗时更长）。

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

总控适配器会对上游别名做归一，例如：

- `source_literature_id` → `literature_id`
- `support_direction` → `support_direction_hint`
- `strength_score` → `confidence`（缺省时）

### 输出（统一外壳）

```json
{
  "metadata": { "agent_id": "evidence_mapping_agent", "stage": "evidence_mapping", "status": "success" },
  "payload": { "evidence_map": [ /* 每条假设一项 */ ] },
  "self_review": { "passed": true, "overall_score": 0.8, "threshold": 0.55, "issues": [], "suggestions": [] }
}
```

### 建议总控回退映射

| `verdict.rollback_target` | 动作 |
|---|---|
| `knowledge_integration` | 回退模块 2 补证据 |
| `hypothesis_generation` | 回退模块 3 改假设 |
| `none` | 进入模块 5（可带 limitations） |

## 5. 上游最小必填

**evidence_card**

- `evidence_id`（必需）
- `claim`（必需）
- `literature_id` / DOI/URL（建议，用于可靠性）
- `support_direction_hint`（可选，仅作参考）
- `quotes`（可选；**当前上游通常不产出，本模块不因缺 quotes 扣分**）

**hypothesis_card**

- `hypothesis_id`, `statement`（必需）
- `based_on_evidence_ids`（建议）
- `expected_observation` 或 `predictions[]`（强烈建议；用于缺口识别）
- `target_variables`（建议）

## 6. 处理流程

对 **每条假设** 执行：

1. 候选筛选：`based_on_evidence_ids` 必审 + 主题相关扫描
2. 方向重判 + 四维评分（LLM 或规则）
3. 归入 support / oppose / uncertain（互斥）
4. 缺口识别：预测覆盖、因果链、`why_no_oppose` 等
5. 综合强度 `evidence_strength_score` + `verdict` / `rollback_target`
6. 汇总全部假设，生成 `self_review`

四维权重：直接性 30% / 可靠性 25% / 充分性 25% / 适用性 20%。

### Self 自评（整阶段）

```text
Self ≈ 0.45 × 平均强度
     + 0.35 × 绑定均分(/10)
     + 0.20 × 有支持覆盖率
通过阈值：0.55
```

说明：Self 看全部假设的平均表现，不是「四选一」；弱假设会拉低平均。下游研究计划会再按强度等做 Top-K 择优。

## 7. 接入示例

```python
from evidence_mapping import EvidenceMappingAgent

agent = EvidenceMappingAgent()  # 默认 auto：有 Key 用 LLM
response = agent.run_dict(total_control_input_slice)
# response["payload"]["evidence_map"] -> 写入 task_context.evidence_map
```

## 8. 当前实现说明（v0.2）

- **默认 `auto`**：有 `DASHSCOPE_API_KEY` / `QWEN_API_KEY` / `LLM_API_KEY` 时，按假设调用 LLM（Prompt 见 `prompts.py`）。
- **兜底**：无 Key 或 LLM 失败 → 规则引擎；`self_review.issues` 可能含 `llm_fallback: ...`。
- **领域无关**：Prompt / 规则均不绑定特定病例；适用性按研究对象、实验条件、方法与假设预测是否匹配来评。
- **不因缺 quotes 扣分**（适配上游现状）。
- Schema / 输出外壳保持不变。
- 模式：`EVIDENCE_MAPPING_MODE=auto|llm|rules`

```powershell
# LLM（需与总控相同的工作区 Key + BASE_URL）
$env:DASHSCOPE_API_KEY="sk-ws-..."
python examples/run_demo.py

# 强制规则引擎
$env:EVIDENCE_MAPPING_MODE="rules"
python examples/run_demo.py
```

改 Prompt 或源码后，若经 cockpit 后端调用，需 **重启后端**（模块会被进程缓存）。
