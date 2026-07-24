# Planning Agent Engineering Guide

本目录是总控仓库内 Research Planning Agent 的唯一维护源。用户直接指令和
总控仓库协议优先于本文件。

## Git 协作规则

- 开始修改前先同步总控当前 `STAR/` 功能分支，确认没有落后其远程分支。
- 不要直接向 `main` 推送。后续不再以独立 Planning Agent 仓库作为开发源。
- 修改后先从本目录运行 `python -m pytest`，再运行相关总控集成测试。
- 不要提交 `.env`、`.venv/`、`.tmp/`、CLI 输出、Dify 网页调试导出或其它本地运行产物。

## Dify Workflow DSL 规则

Dify 导出的工作流文件是 YAML DSL，不是任意 JSON。手写或修改 `dify/*.yml` 时必须遵守下面规则。

### 顶层结构

- 保留 `kind: app`、`version: 0.6.0`、`app`、`dependencies`、`workflow.features`、`workflow.graph.nodes`、`workflow.graph.edges`。
- 当前主文件 `dify/Research Planning Agent.yml` 必须保持 `app.mode: workflow`，不要改成 `advanced-chat`，除非同步改造 API 调用和输出读取逻辑。
- 官方 example 中的 `answer` 节点适合 `advanced-chat` 前端流式展示；本项目通过 Workflow API 调用，最终输出必须走 `end` 节点。

### 节点结构

- 每个节点外层 `type` 通常是 `custom`；真正的节点类型在 `data.type`，例如 `start`、`llm`、`code`、`tool`、`agent`、`answer`、`loop`、`assigner`、`end`。
- 节点 `id` 是变量引用和边连接的唯一标识。改节点 `id` 时必须同步更新所有 `{{#node_id.field#}}`、`value_selector`、`variable_selector`、edge `source/target`。
- `start` 节点输入字段写在 `data.variables[]`，每项包含 `label`、`variable`、`type`、`required` 等。
- `end` 节点输出写在 `data.outputs[]`，每项用 `value_selector` 指向上游节点输出，并用 `variable` 定义 Workflow API 返回字段。

### 边结构

- 每条边必须放在 `workflow.graph.edges[]`。
- `edge.source`、`edge.target` 必须等于真实节点 `id`。
- `edge.data.sourceType`、`edge.data.targetType` 必须匹配对应节点的 `data.type`。
- 常规边保留 `sourceHandle: source`、`targetHandle: target`、`type: custom`、`zIndex: 0`。

### 变量引用

- Prompt 内变量使用 Dify 模板语法：`{{#node_id.output_key#}}`。
- 结构化输出字段使用：`{{#node_id.structured_output.field#}}`。
- Code/Tool/End 等节点的变量选择用数组形式：
  - `value_selector: [node_id, field]`
  - `variable_selector: [node_id, field]`
- 不要给中间 LLM 输出编造 `variable:` 字段；`variable:` 只用于 Start 输入声明、End 输出声明、Code 节点入参名等 DSL 允许的位置。

### LLM 与结构化输出

- LLM 节点应包含 `data.model`、`data.prompt_template[]`、`data.structured_output_enabled`。
- 需要严格 JSON 时同时设置 `completion_params.response_format: json_object` 和 `structured_output.schema`。
- `structured_output.schema` 使用 JSON Schema：`type`、`properties`、`required`、`items`、`enum`、`additionalProperties`。
- 为了 Dify 导入稳定性，不要在主 DSL 中手写 YAML anchor/alias（例如 `&schema`、`*schema`）。即使 YAML 语法合法，也优先使用 Dify 官方导出的完整展开风格。

### Code 节点

- Code 节点必须包含 `data.code`、`data.code_language`、`data.outputs`、`data.variables[]`。
- `data.variables[]` 中每项用 `value_selector` 指向上游输入，并用 `variable` 定义传入代码的参数名。
- 本项目的 Dify Code 节点只做 JSON 解析、字段压缩和格式转换；不要在 Code 节点里读写本地文件、执行系统命令或发网络请求。

### Loop / Agent / Tool 节点

- 官方 `dify/example/Example research agent process flow.yml` 演示了 `loop`、`loop-start`、`agent`、`assigner`、`tool` 和 `answer` 的高级用法。
- `loop` 节点需要 `loop_variables`、`break_conditions`、`start_node_id`；循环内部节点需要 `parentId`、`data.isInLoop: true`、`data.loop_id`。
- `assigner` 用于 append/extend/+= 更新 loop 变量。
- 本项目当前不把检索循环放进 Dify。planning agent 的证据来自上游模块，Dify 只做单假设研究计划生成和自检修复。

### 本项目主 Workflow 契约

- Start 输入必须是单数 `hypothesis_evidence_package`，不要恢复为 `hypothesis_evidence_packages`。
- End 输出变量必须是 `plan_result`。
- Dify Workflow 一次只处理一个 hypothesis；多 hypothesis 选择、并发、聚合由本地 Python wrapper 负责。
- 不得在 Dify 中编造新文献、数据集 URL 或已完成实验结果。

## 总控运行契约

- 保留本 Agent 的验证、候选修复、评审选择、聚合和可追溯性护栏。
- 总控通过 `planning_agent.service.run_planning_agent` 调用本包；不得破坏
  `progress_handler`、`workflow_event_handler` 和 `cancellation_checker`。
- 正式响应仍使用总控 AgentResponse，并且 `research_planning` 只能写入
  `research_plan`。
- 总控会加载本目录的 `.env`；该文件必须保持 Git ignored，任何 API Key 都不得
  进入提交、测试快照或日志。
