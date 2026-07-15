# Dify Workflow 测试资产

目标空白工作流：

```text
http://115.190.208.240:31880/workflow/289a1LGKdhR84gEN
```

## 文件说明

- `Research Planning Agent.yml`：当前主 Dify Workflow DSL 文件，请导入或覆盖这个文件。
- `planning_agent_workflow.json`：轻量 JSON 参考文件，主要供本地测试使用。

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

`Research Planning Agent.yml` 仍然是单假设工作流，但内部已经拆成多阶段：

```text
Start
-> Normalize Evidence Context
-> Build Evidence Brief JSON
-> Draft Plan Skeleton JSON
-> Generate Full Plan Result JSON
-> Critic and Repair Plan Result JSON
-> End
```

各阶段职责：

- `Normalize Evidence Context`：Code 节点，解析 Start 输入，压缩成 `normalized_evidence_context`，包含 evidence_rows、source_literature、knowledge_gaps、guardrails。
- `Build Evidence Brief JSON`：LLM 节点，区分 supporting、opposing、uncertain 证据，并显式写出 missing_evidence。
- `Draft Plan Skeleton JSON`：LLM 节点，先生成变量、验证策略、数据需求、分析计划、失败判据和 feedback_tasks 骨架。
- `Generate Full Plan Result JSON`：LLM 节点，按 `experiment_planner_plan_result_v1` 生成完整单假设计划。
- `Critic and Repair Plan Result JSON`：LLM 节点，静默检查引用来源、证据 ID、feedback_tasks、方法步骤和 falsification criteria，并只返回修复后的最终 `plan_result`。

这个设计借鉴了 PaperQA2/STORM/AI Scientist/Agent Laboratory/OpenScholar 的常见拆分方式：先整理证据和结构，再写最终计划，最后做自检修复。Dify 不负责多 hypothesis 循环，也不负责文件系统/MCP。
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

- 可以引入“反思/差距分析”思想，把当前 `Draft Plan Skeleton JSON` 扩展为更明确的 `known / missing / next_validation_tasks`，但仍只能基于输入证据包。
- 可以增加一个轻量的 progress payload 设计，让前端展示“证据摘要、计划骨架、最终质检”三个阶段，而不是直接展示原始 text chunk。
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
export DIFY_API_KEY="<workflow-app-api-key>"
export DIFY_USER="research-planning-agent"
export DIFY_RESPONSE_MODE="streaming"
export DIFY_TIMEOUT_SECONDS="300"
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
4. 发布 Workflow 应用并复制 API Key。
5. 使用 `samples/input/` 中的样例输入运行本地 CLI；response 默认写入被忽略的 `samples/output/planning_responseMM_DD-HH_MM.json`。

```powershell
.\.venv\Scripts\python -m planning_agent.cli --input samples/input/module5_input_sample.json --show-progress
```

如果 Dify 未配置或调用失败，本地封装层会返回 `failed`，不会生成本地兜底研究计划。
