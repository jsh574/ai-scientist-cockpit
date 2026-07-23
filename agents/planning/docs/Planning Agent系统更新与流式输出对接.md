# Planning Agent 系统更新与流式输出对接

## 1. 文档目的

本文面向负责总控、后端事件服务和前端展示的开发人员，说明 Planning Agent 当前 A/B/C 链路的输入输出、Dify 流式事件能力、并发关联方式以及产品化接入建议。

本次只更新 Planning Agent 仓库，没有修改 `ai-scientist-cockpit`。因此本文区分“Planning Agent 已具备的能力”和“主系统仍需完成的接入工作”。

## 2. 当前交付状态

Planning Agent 已具备：

- 单个或多个上游 hypothesis 分别执行完整 A -> B -> C。
- 每个 hypothesis 内并行执行 A 的三个固定设计取向。
- B 对可用候选进行评分、解释和路由。
- C 使用 B 的 `selected_design` 快速生成最终计划。
- Dify streaming SSE 事件解析。
- A/B/C 事件的本地 correlation 字段。
- 单 hypothesis 和 batch JSON/HTML 测试报告。

当前尚未接入主系统正式执行路径：

- `planning_agent.workflow_chain_cli` 是 A/B/C 验证和对接入口。
- `planning_agent.service.run_planning_agent` 仍保留 C-only 兼容路径。
- 主系统还没有把 Planning Agent 事件写入任务事件日志。
- 前端还没有订阅 Planning Agent 事件的 EventSource，也没有 B 人工选择后的恢复接口。

所以，当前 CLI 能证明事件和中间 JSON 可获得，但 CLI stderr 不是正式前端协议。

## 3. 三层数据边界

### 3.1 正式模块输入

总控传入 `experiment_planner_input_v1`，主要字段为：

```json
{
  "schema_version": "experiment_planner_input_v1",
  "task_id": "task_001",
  "iteration": 1,
  "request_mode": "batch",
  "question_card": {},
  "hypothesis_cards": [],
  "evidence_map": [],
  "literature_cards": [],
  "evidence_cards": [],
  "knowledge_gaps": [],
  "user_constraints": {},
  "planning_constraints": {}
}
```

字段来源：

- `hypothesis_cards` 来自上游假设生成 Agent。
- `evidence_map` 来自证据梳理 Agent。
- `literature_cards` 和 `evidence_cards` 来自检索与证据模块。
- 上游不需要生成 `hypothesis_evidence_package`；Planning Agent 适配层按 `hypothesis_id` 派生。

### 3.2 A/B/C 内部 trace

每个 hypothesis 独立产生一份内部 trace：

```text
hypothesis
  -> A.minimum_viable
  -> A.high_information
  -> A.resource_efficient
  -> B.selection
  -> C.plan_result
```

这些对象用于过程展示、调试和人工审核，不应直接增加为上游领域字段。

### 3.3 正式模块输出

每个 C 只返回单 hypothesis 的 `plan_result`。Python 服务聚合后，正式模块结果仍是：

```json
{
  "schema_version": "experiment_planner_output_v1",
  "status": "success",
  "plans": [
    {
      "hypothesis_id": "hyp_001",
      "status": "success",
      "plan": {}
    }
  ]
}
```

前端最终研究计划只能从正式 `plan_result` / `research_plan` 读取，不应从 `text_chunk` 拼接最终 JSON。

## 4. Dify 能提供哪些流式事件

每一次 `POST /v1/workflows/run` 且 `response_mode=streaming` 的调用都会建立独立 SSE 响应流。常用事件如下：

| Dify event | 可表达内容 | 建议前端用途 |
|---|---|---|
| `workflow_started` | 一次 A、B 或 C 调用开始 | 槽位进入 running |
| `node_started` | Workflow 内节点开始 | 展示阶段名称或进度 |
| `text_chunk` | LLM 输出片段 | 仅作为生成活动指示，不直接展示思考内容 |
| `node_finished` | 节点结束及执行状态 | 更新阶段完成状态 |
| `workflow_finished` | Workflow 成功结束并携带 End outputs | 读取结构化中间结果或最终结果 |
| `workflow_failed` | Workflow 执行失败 | 槽位进入 failed，展示安全错误摘要 |

Dify 的执行状态只说明节点或 Workflow 是否完成。业务上还必须检查：

- A 的 `guardrail_report.passed`。
- B 的 `selection_guardrail_report.passed` 和 `decision`。
- C 的 `plan_result.status`、非空 `plan` 和最终 contract。

## 5. 本次新增的事件关联字段

`GenericDifyWorkflowClient.run()` 新增可选的本地参数 `event_context`。它不会写入 Dify 请求的 `inputs`，只会在调用本地 `event_handler` 前，把关联信息附加到事件副本：

```json
{
  "event": "node_started",
  "data": {
    "title": "Generate Design Candidate JSON"
  },
  "planning_context": {
    "workflow_stage": "A",
    "hypothesis_id": "hyp_rag_001",
    "variant_mode": "minimum_viable",
    "round": 1,
    "attempt": 1
  }
}
```

字段定义：

| 字段 | A | B | C | 含义 |
|---|---:|---:|---:|---|
| `workflow_stage` | 是 | 是 | 是 | `A`、`B`、`C` |
| `hypothesis_id` | 是 | 是 | 是 | 当前假设稳定 ID |
| `variant_mode` | 是 | 否 | 否 | A 的候选槽位 |
| `round` | 是 | 是 | 否 | A/B 有界修订轮次 |
| `attempt` | 是 | 是 | 否 | 当前轮格式重试次数 |
| `selected_candidate_id` | 否 | 否 | 是 | C 使用的 B 选中设计 |

CLI 现在会打印：

```text
[dify:workflow_a][hyp=hyp_rag_001][variant=minimum_viable][round=1][attempt=1] node_started ...
[dify:workflow_b][hyp=hyp_rag_001][round=1][attempt=1] workflow_finished ...
[dify:workflow_c][hyp=hyp_rag_001][candidate=hyp_rag_001::minimum_viable] node_started ...
```

并发情况下这些行仍会交错，这是正常行为。产品端必须按 `planning_context` 分组，不能按事件到达顺序推断归属。

## 6. `text_chunk` 能否直接显示文字

原始 Dify `text_chunk` 通常包含文字，但不建议把原始内容直接展示给用户：

- thinking 模型可能输出 `<think>...</think>`。
- JSON 是增量片段，中途通常不是合法对象。
- 多个并发请求的片段会交错到达。
- 输出可能包含内部提示词、错误堆栈或尚未通过 guardrail 的内容。

当前 A/B/C CLI 默认只打印字符数：

```text
text_chunk chars=32
```

前端建议把 chunk 映射为“正在生成”的活动信号，例如更新 spinner、最近活动时间或非精确进度动画。业务卡片内容应等 `workflow_finished` 后读取 End outputs。

严禁向用户展示 chain-of-thought。需要自然语言进度时，应由后端根据 `workflow_stage`、节点名和状态生成固定文案。

## 7. 分假设和分候选展示方案

### 7.1 页面稳定结构

收到模块输入后，前端或总控可以立即按 `hypothesis_cards[]` 创建 hypothesis 容器；每个容器预建三个 A 槽位：

```text
hyp_rag_001
  minimum_viable     queued
  high_information  queued
  resource_efficient queued
  B selection        queued
  C final plan       queued
```

候选槽位使用 `(task_id, iteration, hypothesis_id, variant_mode, round)` 作为稳定键。

### 7.2 推荐状态机

A 候选：

```text
queued -> running -> success
                  -> rejected
                  -> failed
```

B 评审：

```text
queued -> running -> accept
                  -> revise_once
                  -> human_review
                  -> feedback_required
                  -> failed
```

C 终稿：

```text
queued -> running -> success
                  -> partial_success
                  -> failed
```

`partial_success` 的语义不能混用：

- A 阶段 `partial_success`：三个变体中至少一个通过 guardrail，但不是全部通过；可用候选仍可进入 B。
- C 的 `partial_success`：计划存在但业务状态要求人工复核。

### 7.3 候选展示内容

A 的 `workflow_finished` 后读取：

- `design_candidate.candidate_id`
- `variant_mode`
- `planning_objective`
- `design_type`
- `method_steps`
- `metrics`
- `falsification_matrix`
- `resource_profile`
- `limitations`
- `guardrail_report`

B 的 `workflow_finished` 后读取：

- `decision`
- `selected_candidate_id`
- `candidate_reviews[]`
- `revision_instruction`
- `feedback_tasks[]`
- `meta_review`
- `selection_guardrail_report`

C 的 `workflow_finished` 后读取：

- `plan_result`
- `contract_report`

## 8. 总控事件桥接建议

浏览器不应持有 Dify App API Key，也不应直接连接 Dify。总控应消费 Planning Agent 的回调事件，清洗后写入任务事件存储，再通过主系统 SSE 提供给浏览器。

推荐公开事件 envelope：

```json
{
  "event_id": "evt_000123",
  "task_id": "task_001",
  "iteration": 1,
  "agent_id": "planning_agent",
  "event_type": "planning.node_started",
  "timestamp": "2026-07-22T20:00:00+08:00",
  "correlation": {
    "workflow_stage": "A",
    "hypothesis_id": "hyp_rag_001",
    "variant_mode": "minimum_viable",
    "round": 1,
    "attempt": 1,
    "selected_candidate_id": ""
  },
  "status": "running",
  "payload": {
    "node_title": "Generate Design Candidate JSON",
    "workflow_run_id": "...",
    "chars_delta": 0
  }
}
```

后端桥接伪代码：

```python
def handle_planning_event(workflow_name, raw_event):
    context = raw_event.get("planning_context", {})
    public_event = sanitize_and_map(workflow_name, raw_event, context)
    task_event_store.append(public_event)
    task_sse.publish(public_event)

runner = PlanningWorkflowChainRunner.from_env(
    progress_handler=log_progress,
    event_handler=handle_planning_event,
)
report = runner.run_batch(
    module5_input,
    max_parallel_hypotheses=3,
)
```

公开 payload 应使用白名单，不要把完整 Dify 原始事件透传给浏览器。

## 9. 并发、顺序和限流

批量链路包含两层并发：

- 外层：多个 hypothesis 并发。
- 内层：每个 hypothesis 的三个 A 变体并发。

例如 `max_parallel_hypotheses=3` 时，最多可能同时出现 9 个 A 请求。B/C 会在各自 hypothesis 的前置条件满足后继续，因此不同 hypothesis 可能处于不同阶段。

建议总控增加全局 Dify 并发上限，避免多个任务叠加后超过模型供应商或 Dify 的连接限制。事件展示顺序必须由 correlation 和状态机决定；最终 batch 报告按本地 hypothesis 选择顺序恢复，不按完成顺序排列。

## 10. 持久化、刷新和去重

产品化 SSE 至少需要：

- 单调递增或全局唯一的 `event_id`。
- 后端持久化公开事件，而不是仅在进程内转发。
- 浏览器重连携带 `Last-Event-ID`。
- 后端支持按 `event_id` 补发未消费事件。
- 前端按 `event_id` 去重。
- 提供任务快照接口，刷新后先恢复当前槽位状态，再继续订阅增量事件。

Dify 自身的 SSE 是一次请求级连接，不能替代主系统的任务级恢复机制。

## 11. B 人工审核与恢复

当 B 返回 `human_review` 时，链路会在 C 前停止。主系统需要新增业务接口保存用户选择，并从已持久化的候选和上下文恢复 C。建议保存：

- `task_id`、`iteration`、`hypothesis_id`
- A 的可用 `design_candidates[]`
- B 的 `candidate_reviews[]`
- 用户确认的 `selected_candidate_id`
- 当前 `planning_constraints`
- 相关 workflow run ID

不要通过重新解析终端日志恢复状态。

## 12. Workflow C 更新后的部署影响

快速版 C 保持以下 Start inputs 不变：

```text
task_id
iteration
hypothesis_id
question_card
hypothesis_evidence_package
planning_constraints
user_constraints
```

End outputs 保持不变：

```text
plan_result
contract_report
```

Python 调用方不需要修改 C 的请求或响应字段，但 Dify 管理员需要重新导入并发布 `dify/Research Planning Agent.yml`。快速版 C 从四个串行 LLM 节点缩减为一个，并关闭 thinking；A/B/C 路径优先使用 B 的选中设计，C-only 路径继续兼容。

## 13. 联调命令

测试多个上游 hypothesis 的完整 A/B/C 链路：

```powershell
.\.venv\Scripts\python -m planning_agent.workflow_chain_cli `
  --input samples\input\module5_input_rag_batch.json `
  --all-hypotheses `
  --max-parallel-hypotheses 3 `
  --output samples\test-artifacts\rag-batch-abc.json `
  --html samples\test-artifacts\rag-batch-abc.html
```

检查重点：

- 每个 hypothesis 都有自己的 `hypothesis_runs[]` 报告。
- A 三个 `variant_mode` 的事件带不同 correlation。
- A 的候选保持原始 `hypothesis_id` 和假设语义。
- B 未 `accept` 时 C 没有执行记录。
- C 成功时最终结果来自 `plan_result`，不是 chunk 拼接。
- JSON 和 HTML 报告中的 hypothesis 顺序稳定。

## 14. 主系统最小接入清单

总控后端：

- 将正式 Planning 执行从 C-only 切换或灰度到 A/B/C runner。
- 给 runner 注入事件 handler。
- 将清洗事件持久化并桥接到任务 SSE。
- 增加全局并发上限。
- 保存 A/B 中间结果和 B 人工审核恢复状态。
- 保持最终 `AgentResponse` 和 `research_plan` 写回契约不变。

前端：

- 使用 EventSource 订阅主系统事件，不直接访问 Dify。
- 按 hypothesis 建立顶层容器。
- 为三个 A 变体建立稳定候选槽位。
- 用 correlation 更新槽位，不依赖事件到达顺序。
- B 展示评分、理由和选择状态。
- C 完成后展示正式研究计划。
- 支持刷新恢复、断线重连和 event ID 去重。
- 不展示原始 thinking 或未通过 guardrail 的结构化内容。
