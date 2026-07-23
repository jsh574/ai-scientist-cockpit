# Planning Agent 工作流拆分与前端流式对接说明

## 1. 先说结论

当前项目同时保留了两条执行路径，它们使用相同的模块 5 输入，但用途不同：

| 路径 | 命令 | 实际执行 | 用途 |
|---|---|---|---|
| C-only 兼容路径 | `python -m planning_agent.cli` | 直接为每个假设调用 Workflow C | 当前正式服务兼容、批量计划生成 |
| A/B/C 测试路径 | `python -m planning_agent.workflow_chain_cli` | 单个或多个假设分别执行 A -> B -> C | 验证拆分设计、并发隔离和中间结果 |

所以，执行 `planning_agent.cli` 时终端没有 A/B 日志是正常行为，不代表 A/B 出错或没有发布。这个命令从设计上就没有调用 A/B。

## 2. 为什么拆成 A、B、C

旧工作流直接从证据生成最终计划，优点是接口简单，缺点是过程近似一条长链：方案多样性不可见、为什么选这个方案不可解释、用户也没有合适的中间介入点。

拆分后的核心思路是“先发散、再选择、最后收敛”：

```text
模块 5 输入
  |
  +-> 针对一个 hypothesis 构建紧凑证据包
        |
        +-> Workflow A: 生成 3 个不同目标的实验设计候选
        |      |
        |      +-> minimum_viable
        |      +-> high_information
        |      +-> resource_efficient
        |
        +-> Workflow B: 比较候选、解释评分并决定下一步
               |
               +-> accept ----------> Workflow C
               +-> revise_once -----> A/B 有界重试
               +-> human_review ----> 等待用户选择
               +-> feedback_required -> 请求补充证据
               +-> failed ----------> 停止并报告

Workflow C: 将选中设计扩展成完整、可验证、可追踪的研究计划
```

拆分并不是为了让三个模型重复回答同一问题。A 负责设计空间，B 负责显式决策，C 负责详细计划与最终契约。

### 2.1 上游假设与 A 候选不是同一种对象

上游假设生成 Agent 回答“要验证什么”，输出的是科学命题及其理由、目标变量和预期观察。A 回答“怎样验证同一个命题”，输出的是实验设计候选。A 不应该生成新的 `hypothesis_id`，也不应该把一个假设改写成三个不同科学命题。

以 RAG 批量样例中的 `hyp_rag_002` 为例：

| 层级 | 内容 |
|---|---|
| 上游 hypothesis | 自适应检索门控可能在基本保持事实准确率时降低检索次数和 token 成本 |
| A minimum_viable | 用少量公开样本和几档固定阈值做最低成本的质量-成本比较 |
| A high_information | 扩大问题类型、阈值和消融组合，估计完整 Pareto 边界 |
| A resource_efficient | 使用较小样本、顺序检验或早停策略控制计算预算 |
| B | 比较三种验证设计是否可执行、信息量是否足够，并选择或要求修订 |
| C | 将选中设计扩展为数据、变量、步骤、统计检验、证伪标准和反馈任务齐全的计划 |

因此，多假设批量模式的结构是“多个上游科学假设，每个假设各自拥有三个 A 实验设计候选”，而不是“A 把每个假设再拆成三个新假设”。

## 3. 三个 Workflow 的职责

### 3.1 Workflow A: Design Candidate Generator

A 每次只接收一个假设证据包和一个 `variant_mode`。Python 对同一个假设并行调用三次 A。

主要输入：

```json
{
  "task_id": "task_001",
  "iteration": 1,
  "hypothesis_id": "hyp_001",
  "variant_mode": "minimum_viable",
  "question_card": "{...JSON string...}",
  "hypothesis_evidence_package": "{...JSON string...}",
  "planning_constraints": "{...JSON string...}",
  "user_constraints": "{...JSON string...}"
}
```

稳定输出：

- `design_candidate`: 候选设计、变量、步骤、指标、基线、证伪标准和资源画像。
- `guardrail_report`: schema、身份字段和证据引用检查。

A 的 `partial_success` 表示三种变体里至少有一个可用，但不是全部可用。可用候选仍然可以进入 B。

### 3.2 Workflow B: Design Judge Selector

B 接收同一假设下 A 的可用候选，进行比较、评分和路由。B 的价值不只是选一个 ID，而是把“为什么选择”变成可展示、可审核的结构化结果。

稳定输出：

- `design_selection`: 决策、候选评分、优缺点、选择理由和下一步动作。
- `selected_design`: 只有 `decision=accept` 时才允许非空。
- `selection_guardrail_report`: 对选择结果进行确定性校验。

只有以下三个条件同时成立才能调用 C：

```text
decision == accept
selection_guardrail_report.passed == true
selected_design 非空
```

### 3.3 Workflow C: Research Planning Agent

C 接收一个假设证据包。如果来自 A/B/C 路径，`planning_constraints` 中还会携带 B 选中的设计和选择理由。快速版 C 用一个结构化 LLM 直接将选中设计扩展为完整研究计划，再由确定性 Code 节点执行最终契约检查；C-only 输入仍兼容。

稳定输出：

- `plan_result`: 单个假设的完整计划，schema 为 `experiment_planner_plan_result_v1`。
- `contract_report`: 最终字段、身份、证据 ID 和文献 ID 校验结果。

C 一次仍只处理一个假设。Python wrapper 负责多假设选择、调用和聚合，最后形成：

```json
{
  "schema_version": "experiment_planner_output_v1",
  "status": "success",
  "plans": [
    {"hypothesis_id": "hyp_001", "status": "success", "plan": {}},
    {"hypothesis_id": "hyp_002", "status": "success", "plan": {}}
  ]
}
```

## 4. 为什么这次终端没有 A/B/C

你运行的是：

```powershell
.\.venv\Scripts\python -m planning_agent.cli `
    --input samples\input\module5_input_rag_batch.json `
    --show-progress `
    --output samples\test-artifacts\rag-batch-c-only.json
```

`planning_agent.cli` 调用 `run_planning_agent()`，这是保留的 C-only 兼容服务：

```text
3 个 hypothesis
  -> Python 构建 3 个 hypothesis_evidence_package
  -> 按 selection_score 选择最多 max_hypotheses 个
  -> 每个 package 调用一次 Workflow C
  -> 聚合成 payload.plans[]
```

它不会创建 A 候选，也不会调用 B。测试单个假设的 A/B/C：

```powershell
.\.venv\Scripts\python -m planning_agent.workflow_chain_cli `
    --input samples\input\module5_input_rag_batch.json `
    --hypothesis-id hyp_rag_001
```

测试输入中的全部假设，并允许 3 条假设并发执行完整 A/B/C：

```powershell
.\.venv\Scripts\python -m planning_agent.workflow_chain_cli `
    --input samples\input\module5_input_rag_batch.json `
    --all-hypotheses `
    --max-parallel-hypotheses 3 `
    --output samples\test-artifacts\rag-batch-abc.json `
    --html samples\test-artifacts\rag-batch-abc.html
```

批量报告根 schema 是 `planning_workflow_chain_batch_test_v1`，每个假设的完整单链报告位于 `hypothesis_runs[]`。数组顺序按本地 selection score 恢复，不受并发完成顺序影响。

## 5. 这次运行是否成功，结果在哪里

结果文件是：

```text
D:\Code\Project\Python\Planning Agent\samples\test-artifacts\rag-batch-c-only.json
```

实际检查结果：

```text
metadata.status     = success
payload.status      = success
self_review.passed  = true
overall_score       = 0.82
plans count         = 3
self_review.issues  = []
```

三个假设均成功：

```text
hyp_rag_001: success
hyp_rag_002: success
hyp_rag_003: success
```

终端中的：

```text
[dify] workflow_finished status=succeeded
[planning-agent] Dify finished for hypothesis hyp_rag_002
```

只能证明 `hyp_rag_002` 对应的单次 Dify 请求正常结束。批量任务是否整体成功，要继续检查最终 JSON 的 `metadata.status`、`payload.status`、各 `plans[].status` 和 `self_review`。

因为命令显式传了 `--output`，CLI 会把最终 JSON 写入文件，不再把整份 JSON 打印到终端。这是当前代码的预期行为。

可用 PowerShell 快速检查：

```powershell
$r = Get-Content -Raw samples\test-artifacts\rag-batch-c-only.json | ConvertFrom-Json
$r.metadata.status
$r.payload.status
$r.payload.plans | Select-Object hypothesis_id, status
$r.self_review
```

## 6. 流式输出是谁提供的

底层流式事件由 Dify Workflow API 提供。请求使用 `response_mode=streaming` 后，每一次 Workflow API 调用都会建立一条独立 SSE 响应流。本地 Python 客户端逐行读取 `data: {...}` 事件。

常见事件包括：

```text
workflow_started
node_started
text_chunk
node_finished
workflow_finished
workflow_failed
```

`text_chunk` 原始事件中包含文字。你看到的：

```text
[dify] text_chunk #1219 +12 chars total=13649 phase=json
```

不是 Dify 只能提供字符数，而是本地 `_StreamProgressPrinter` 主动把文字隐藏了，只打印：

- chunk 序号；
- 本次字符数；
- 累计字符数；
- 当前阶段 `thinking/json/answer`。

这样做是为了避免将 `<think>...</think>`、不完整 JSON 片段或敏感上下文直接泄露到终端和前端。

本地调试时可以开启清理后的短预览：

```powershell
$env:DIFY_SHOW_TEXT_CHUNKS = "1"
```

随后再执行带 `--show-progress` 的 CLI。预览仍会过滤 thinking 内容，并且只显示短片段；它不是面向最终用户的连续自然语言答案。

## 7. 并行调用能否分别获取流式输出

技术上可以。每次并行调用都有自己的 HTTP 响应流，因此可以分别消费事件。比如同一个 hypothesis 的三个 A 变体可以分别显示：

```text
minimum_viable      running -> validating -> success
high_information    running -> generating -> success
resource_efficient  running -> generating -> rejected
```

A/B/C 测试链路现在会在本地事件副本中增加 `planning_context`，CLI 前缀示例：

```text
[dify:workflow_a][hyp=hyp_rag_001][variant=minimum_viable][round=1][attempt=1] node_started ...
[dify:workflow_b][hyp=hyp_rag_001][round=1][attempt=1] workflow_finished ...
[dify:workflow_c][hyp=hyp_rag_001][candidate=hyp_rag_001::minimum_viable] node_started ...
```

并发事件在终端中的物理到达顺序仍然会交错，这是正确的并发行为；前端应按 correlation 字段分组，而不是等待日志变成串行。终端输出仍不能直接作为前端协议：

1. C-only 兼容 CLI 仍复用原有 `_StreamProgressPrinter`，多个请求的 chunk 计数会交错。
2. 原始事件包含 Dify 内部字段，前端不应该依赖其全部结构。
3. 浏览器不能直接持有 Dify App API Key，也不应直接连接 Dify。
4. 总控还需要把清洗后的事件持久化并桥接到自己的 SSE 接口。

因此需要由后端为每次调用建立关联上下文，再把清理后的事件汇总到 Cockpit SSE。

建议的前端事件 envelope：

```json
{
  "event_id": "evt_000123",
  "task_id": "task_001",
  "execution_id": "planning_exec_001",
  "stage": "research_planning",
  "workflow": "A",
  "hypothesis_id": "hyp_rag_001",
  "variant_mode": "minimum_viable",
  "round": 1,
  "attempt": 1,
  "workflow_run_id": "dify-run-id",
  "event": "node_started",
  "node_id": "candidate_llm",
  "node_title": "Generate Design Candidate JSON",
  "status": "running",
  "output_chars_delta": 0,
  "output_chars_total": 0,
  "phase": "json",
  "occurred_at": "2026-07-22T10:00:00Z"
}
```

每次调用的 Python 代码在提交线程任务时已经知道 `hypothesis_id`、`variant_mode`、`round` 和 `attempt`。应该通过每次调用独立的 callback closure 把这些字段附加到 Dify 事件，而不是事后根据终端文本猜测。

## 8. 推荐的前端接入方式

推荐数据流：

```text
浏览器
  -> POST 启动 Planning stage
Cockpit backend
  -> 为每次 A/B/C 调用创建 correlation context
  -> 调用 Dify streaming API
  -> 清理并持久化结构化事件
  -> 写入 Cockpit task event stream
浏览器
  <- EventSource 订阅 /api/tasks/{task_id}/events/stream?follow=true
  <- 最终从 stage detail 或 task_context.research_plan 读取正式结果
```

前端不要直接解析 `[dify] text_chunk ...` 终端字符串。终端格式只是调试输出，不是稳定 API。

### 8.1 A 阶段

为每个 hypothesis 展示三个稳定候选槽位：

- 变体名称；
- 当前节点或阶段；
- `running/success/rejected/failed`；
- 已生成字符量或进度指示；
- 完成后的候选摘要和 guardrail issues。

### 8.2 B 阶段

展示：

- 候选对比评分；
- 主要优点和风险；
- 选择理由；
- `decision` 和 `next_action`。

如果是 `human_review`，前端需要候选选择控件，并调用新的后端恢复接口提交 `selected_candidate_id`。现有的整阶段接受/重试接口不足以表达这个动作。

### 8.3 C 阶段

展示节点级进度即可，不建议逐 token 渲染 JSON。C 完成后从正式 `payload.research_plan` 或 `task_context.research_plan` 渲染最终计划。

### 8.4 页面刷新和断线

- SSE 事件必须带单调递增的 `event_id`。
- 前端按 `event_id` 去重并使用 `Last-Event-ID` 恢复。
- 页面刷新时先读取已持久化 stage trace，再订阅新事件。
- 最终成功状态以持久化 stage detail/AgentResponse 为准，不能只依赖最后一个 SSE 事件。

## 9. 多假设下的并发边界

C-only 当前支持多个 hypothesis 串行或并行执行：

```powershell
$env:DIFY_MAX_PARALLEL_CALLS = "3"
```

结果数组会按本地选择顺序恢复，不会因为并发完成顺序而乱序。

当前批量测试器已经实现两层并发；正式产品 `planning_agent/service.py` 仍是 C-only，接入 A/B/C 后也会形成相同结构：

```text
外层: 多个 hypothesis
内层: 每个 hypothesis 的 3 个 A variants
```

如果一次处理 3 个 hypothesis，直接全开可能产生 9 个并发 A 请求。产品后端应设置全局并发上限或队列，例如全局最多 3 到 4 个 Dify 请求，而不是简单使用 `3 x 3` 个线程。前端仍可先创建所有候选槽位，并把排队中的槽位显示为 `queued`。

## 10. 当前尚未完成的产品接入工作

目前具备：

- A/B/C 三个可独立调用的 Dify Workflow；
- A/B/C 单假设与多假设批量测试 runner，以及结构化 HTML/JSON 报告；
- C-only 多假设聚合；
- Dify SSE 读取和终端诊断。

仍需完成：

1. 把 A/B/C runner 接入正式 `planning_agent/service.py`。
2. 给每次并发调用增加 hypothesis/variant/round/attempt correlation。
3. 将清理后的事件桥接到 Cockpit 已有 task event SSE。
4. 持久化 Planning trace，支持刷新恢复。
5. 新增 B `human_review` 的候选提交与恢复 C 接口。
6. 前端增加 EventSource、候选卡槽、B 评审视图和最终计划视图。

在这些工作完成前，A/B/C 的实时终端日志可以用于测试，但不能直接视为已经完成的前端流式接口。
