# Dify Workflow 资产

## 文件说明

- `Planning Design Candidate Generator.yml`：Workflow A，生成设计候选。
- `Planning Design Judge Selector.yml`：Workflow B，评审并选定候选。
- `Research Planning Agent.yml`：Workflow C，将 B 选中的设计扩展为最终研究计划。

## 架构边界

Dify Workflow 一次只处理一个 hypothesis：

```text
one hypothesis_evidence_package -> one plan_result
```

本地 `planning_agent` 封装层负责：

- 接收模块 5 全量输入
- 派生全部 hypothesis evidence packages
- 按分数选择 1-3 个 hypothesis
- 对每个 hypothesis 单独调用 Dify Workflow
- 将多个 `plan_result` 聚合成最终模块响应的 `payload.plans[]`

## 当前主工作流阶段

`Research Planning Agent.yml` 仍然是单假设工作流。A/B 已经完成候选设计和选择后，C 使用快速终稿路径：

```text
Start
-> Normalize Evidence Context
-> Generate Final Plan Fast
-> Normalize Final Plan Contract
-> End
```

各阶段职责：

- `Normalize Evidence Context`：Code 节点，解析 Start 输入，压缩 evidence、literature、knowledge gaps、guardrails，并强制要求 `planning_constraints.selected_design`。
- `Generate Final Plan Fast`：唯一的 LLM 节点，以 B 选中的设计为基线，只负责补齐执行、统计、证伪、资源和反馈细节。
- `Normalize Final Plan Contract`：Code 节点，确定性归一化身份字段并验证 `plan_result` 契约。

该调整把 C 从四次串行 LLM 调用缩减为一次；最终质量控制依赖 A/B 的显式设计评审、严格 structured output、证据 allowlist、本地 hard gate 和最终 Contract Code。Dify 不负责多 hypothesis 循环，也不负责文件系统/MCP。
## 官方 Research Agent Process Example 对比

`dify/example/Example research agent process flow.yml` 是一个 Dify `advanced-chat` deep research 示例，核心编排是：

```text
Start -> Exa Answer -> Intent analysis -> Answer stream
-> Loop(reasoning -> Act agent/tools -> url extract -> Variable Assigner)
-> finalize_summary -> Answer
```

它和本项目当前主 workflow 的主要差异：

- 官方 example 面向交互式 research agent，使用 `answer` 节点持续向前端输出阶段性状态；本项目面向后端 Workflow API，使用 `end` 节点返回结构化 `plan_result`。
- 官方 example 在 Dify 内部做联网检索、URL 内容读取、think 工具调用和 loop 变量累积；本项目的 evidence/literature/hypothesis 由上游 agent 提供，Dify 不应自行扩展真实文献或数据源。
- 官方 example 用 `loop` + `assigner` 管理 `findings`、`executed_queries`、`visitedURLs`、`knowledge_gap`；本项目当前由 Python wrapper 管理多 hypothesis 并发、重试、聚合和最终校验。
- 官方 example 用中间 `answer` 节点缓解用户等待；本项目已经在 API 层解析 Dify streaming SSE 事件，未来前端应展示 workflow/node 进度，而不是依赖 Dify answer 节点。
- 官方 example 的 YAML 是 Dify 导出的完整展开风格；因此本项目主 DSL 也避免手写 YAML anchor/alias，减少导入兼容风险。

可以借鉴但暂不直接照搬的优化：

- 可以在 B 的评审结果中继续加强 `known / missing / next_validation_tasks`，但仍只能基于输入证据包。
- 前端应展示 A 候选、B 评审和 C 终稿等结构化阶段，不直接展示原始 text chunk。
- 如果未来总控允许 planning agent 主动补证据，再考虑引入 Dify loop/agent/tool；当前比赛初版不建议，因为会破坏上游 agent 边界和引用可控性。

## Dify DSL 语法规则

详细规则写在根目录 `AGENTS.md`。修改 `Research Planning Agent.yml` 时特别注意：

- app mode 保持 `workflow`；不要引入 `answer` 节点作为最终输出。
- 节点真实类型在 `data.type`，edge 的 `sourceType/targetType` 必须与之匹配。
- Prompt 变量使用 `{{#node_id.output#}}`，结构化字段使用 `{{#node_id.structured_output.field#}}`。
- Code/End 等节点使用 `value_selector` 数组选择上游变量。
- End 输出变量必须继续叫 `plan_result`。
- Start 输入必须继续使用单数 `hypothesis_evidence_package`。
- 主 DSL 不使用手写 YAML anchor/alias；重复 schema 时直接完整展开。
## Dify 预期输入

本地适配层会向 Dify Workflow 发送以下输入：

- `task_id`：字符串
- `iteration`：数字
- `hypothesis_id`：当前单个假设 ID
- `question_card`：模块 1 输出的问题卡片 JSON 字符串
- `hypothesis_evidence_package`：当前单个假设证据包 JSON 字符串
- `planning_constraints`：研究计划生成约束 JSON 字符串
- `user_constraints`：用户约束 JSON 字符串

注意：不再向 Dify 传 `hypothesis_evidence_packages` 复数数组。

## Dify 预期输出

Dify Workflow End 节点需要返回名为 `plan_result` 的输出变量。该变量应是一个 JSON 对象或 JSON 字符串，顶层结构为：

```json
{
  "schema_version": "experiment_planner_plan_result_v1",
  "agent_name": "ExperimentPlannerAgent",
  "task_id": "...",
  "iteration": 1,
  "hypothesis_id": "...",
  "status": "success",
  "error_message": "",
  "plan": {}
}
```

本地封装层会把多个 `plan_result` 重组为模块级 `payload.plans[]`。

如果模型输出混入 `<think>...</think>`，本地客户端会先剥离 thinking 段，再抽取第一个符合 planner schema 的 JSON 对象。仍然建议在 Dify 里优先选择不会暴露 reasoning 的模型或关闭思考输出，因为这能减少响应体大小和解析风险。

## Dify API 环境变量

推荐把配置写在项目根目录 `.env`，该文件已被忽略：

```env
export DIFY_API_URL="http://115.190.208.240:31880"
export DIFY_WORKFLOW_A_API_KEY="<candidate-generator-app-key>"
export DIFY_WORKFLOW_B_API_KEY="<judge-selector-app-key>"
export DIFY_WORKFLOW_C_API_KEY="<final-planner-app-key>"
export DIFY_CHAIN_USER="research-planning-agent"
export DIFY_CHAIN_RESPONSE_MODE="streaming"
export DIFY_CHAIN_TIMEOUT_SECONDS="300"
export DIFY_SHOW_PROGRESS="1"
```

本地封装层会调用：

```text
POST /v1/workflows/run
```

如果网页端能正常运行但本地 CLI 超时，优先使用 streaming 模式并延长超时。本地客户端会解析 Dify SSE 中的 `workflow_finished` / `workflow_failed` 事件，并继续输出最终聚合后的模块响应 JSON。

注意：streaming 不等于一定能看到 LLM token 级输出。Workflow 的最终 `plan_result` 通常仍要等 LLM 节点完成后才出现；可见的中间过程取决于 Dify 是否发送 `node_started`、`node_finished` 等 SSE 事件。

## 导入后检查

导入 `Research Planning Agent.yml` 后：

1. 检查 LLM 节点模型供应商和模型是否可用。
2. 确认 Start 节点输入是 `hypothesis_evidence_package` 单数，不是 `hypothesis_evidence_packages`。
3. 确认 End 节点输出变量名为 `plan_result`。
4. 分别发布 A/B/C 三个 Workflow 应用并复制各自的 API Key。
5. 使用 `samples/input/` 中的样例输入运行完整链路 CLI；response 默认写入被忽略的 `samples/output/planning_responseMM_DD-HH_MM.json`。

```powershell
.\.venv\Scripts\python -m planning_agent.cli --input samples/input/module5_input_sample.json --show-progress
```

如果 Dify 未配置或调用失败，本地封装层会返回 `failed`，不会生成本地兜底研究计划。
