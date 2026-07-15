# EurekaLoop 后端对接开发指南

本文档面向后端与 Agent 开发同学，说明当前前端 demo 中各个按钮、状态、对话记录和状态树未来应如何接入真实后端。后续前端交互有变化时，需要同步更新本文档。

## 1. 当前前端能力边界

当前版本仍是 mock-first 前端，但已经按真实系统预留了这些交互：

- 多项目侧边栏：每个项目对应一条独立科研任务会话。
- 对话式主流程：用户输入、总控消息、每个 Agent 输出、修改建议、最终输出都进入同一个 thread。
- Review Gate：需要审批时，审批按钮出现在对应 Agent 输出消息尾部。
- 修改重跑：用户可在对应消息尾部写反馈，重新运行当前模块。
- 控制策略：推理强度、访问权限、记忆能力。
- 文件上传入口：输入框左下角 `+`。
- 状态树：侧边小状态树 + 完整可视化状态树。

这些交互目前部分由前端 mock 状态驱动，后续需要用真实 API、SSE 和 Artifact Service 替换。

## 2. 推荐后端模块

建议后端拆成这些服务：

|模块|职责|
|---|---|
|Task API|创建项目/任务，读取项目列表，切换任务|
|Workflow Orchestrator|按状态机调度 Agent|
|TaskContext Manager|维护 `task_context`，裁剪输入，合并输出|
|Agent Adapter Layer|把各 Agent 封装成统一调用协议|
|Review Gate|校验格式、字段、自评分、下游可用性和审批策略|
|Artifact Service|保存 input/output/review/context/version/event/file|
|Event Stream|用 SSE/WebSocket 推送阶段状态和对话消息|
|File Service|处理上传文件、元数据、引用关系|

## 3. 前端状态与后端数据映射

### 3.1 Project Session

前端中的一个项目建议映射到后端的一个 `task` 或 `workspace_task`：

```json
{
  "project_id": "project_001",
  "task_id": "task_001",
  "title": "阿尔茨海默病的关键致病机制是什么？",
  "created_at": "2026-07-09T10:00:00Z",
  "updated_at": "2026-07-09T10:10:00Z",
  "status": "human_review",
  "current_stage": "hypothesis_generation",
  "iteration": 1
}
```

### 3.2 Thread Message

所有用户输入和 Agent 输出都应落成消息：

```json
{
  "message_id": "msg_001",
  "task_id": "task_001",
  "kind": "user | controller | agent",
  "stage": "question_understanding",
  "status": "running | validating | human_review | passed | failed | retrying",
  "body": "用户问题或总控说明",
  "response_ref": "artifacts/tasks/task_001/outputs/question_understanding.output.json",
  "review_ref": "artifacts/tasks/task_001/reviews/question_understanding.review.json",
  "needs_approval": true,
  "created_at": "2026-07-09T10:00:00Z"
}
```

前端不要求后端每次都返回完整大 JSON。推荐返回摘要 + artifact 引用，点击 JSON 时再拉取详情。

## 4. 推荐 API

### 4.1 项目列表

```text
GET /api/projects
POST /api/projects
GET /api/projects/{project_id}
PATCH /api/projects/{project_id}
POST /api/projects/archive-all
```

`POST /api/projects` 用于左侧“文件加号”创建新项目。

### 4.2 任务启动

```text
POST /api/tasks
POST /api/tasks/{task_id}/start
```

请求体示例：

```json
{
  "project_id": "project_001",
  "question": "阿尔茨海默病的关键致病机制是什么？",
  "settings": {
    "reasoning": "low | medium | high | ultra",
    "approval_mode": "ask | assist | auto",
    "memory": "low | medium | high"
  },
  "file_ids": ["file_001"]
}
```

### 4.3 阶段详情

```text
GET /api/tasks/{task_id}/stages
GET /api/tasks/{task_id}/stages/{stage}
GET /api/tasks/{task_id}/context
GET /api/tasks/{task_id}/messages
```

阶段详情应返回：

```json
{
  "stage": "hypothesis_generation",
  "status": "human_review",
  "input": {},
  "output": {
    "metadata": {},
    "payload": {},
    "self_review": {}
  },
  "review": {},
  "allowed_writes": ["hypothesis_cards"]
}
```

### 4.4 审批与重跑

```text
POST /api/tasks/{task_id}/reviews
POST /api/tasks/{task_id}/stages/{stage}/rerun
POST /api/tasks/{task_id}/feedback
```

审批请求：

```json
{
  "stage": "hypothesis_generation",
  "decision": "accept",
  "operator": "human",
  "comment": "人工审批通过"
}
```

重跑请求：

```json
{
  "stage": "hypothesis_generation",
  "feedback": "请减少过于宽泛的假设，优先保留可用公开数据验证的机制假设。",
  "rerun_mode": "same_stage",
  "base_version_id": "v003"
}
```

## 5. 控制策略字段

### 5.1 推理强度

前端枚举：

```text
low | medium | high | ultra
```

后端可映射到：

- 模型选择
- 最大上下文长度
- 是否启用多轮自检
- 是否启用更严格的 Review Gate

### 5.2 访问权限

前端枚举：

```text
ask     请求批准：进入下一层模块输出前始终询问
assist  替我审批：仅对检测到的风险操作请求批准
auto    完全自动：完全由模型自行审批
```

建议后端映射：

|前端值|后端模式|行为|
|---|---|---|
|ask|manual|每个阶段结束都进入 `human_review`|
|assist|hybrid|关键阶段或风险阶段进入 `human_review`|
|auto|auto|总控自动审批，保留日志|

### 5.3 记忆能力

前端枚举：

```text
low | medium | high
```

建议后端映射：

- `low`：只保留当前阶段必要上下文。
- `medium`：保留阶段摘要、关键反馈、版本索引。
- `high`：保留完整对话、artifact 引用、版本 diff 和反馈历史。

## 6. 文件上传

输入框左下角 `+` 后续应调用：

```text
POST /api/projects/{project_id}/files
GET /api/projects/{project_id}/files
DELETE /api/projects/{project_id}/files/{file_id}
```

上传后返回：

```json
{
  "file_id": "file_001",
  "name": "paper.pdf",
  "mime_type": "application/pdf",
  "size": 123456,
  "artifact_path": "artifacts/tasks/task_001/files/paper.pdf",
  "status": "uploaded | parsed | failed"
}
```

## 7. 状态树数据

完整状态树不应该只返回 6 个阶段节点。当前前端会本地生成三列结构：

- 第 1 列：六个主阶段，从上到下固定为 `question_understanding -> knowledge_integration -> hypothesis_generation -> evidence_mapping -> research_planning -> final_review`。
- 第 2 列：每个阶段允许写入的 artifact，例如 `question_card`、`literature_cards`、`evidence_map`。
- 第 3 列：从 Agent 真实 `payload` 中抽取的可读摘要，例如核心问题、文献标题、假设语句、证据强度、方案方法、总控评分。

后续建议后端直接提供 `/api/tasks/{task_id}/state-tree`，让前端只负责渲染。节点字段建议如下：

```json
{
  "nodes": [
    {
      "id": "question_understanding",
      "kind": "stage",
      "stage": "question_understanding",
      "title": "问题理解",
      "status": "passed",
      "lane": 1,
      "column": "stage",
      "summary": "把原始问题转成可检索、可验证、可迭代的 question_card。"
    },
    {
      "id": "question_understanding:question_card",
      "kind": "artifact",
      "stage": "question_understanding",
      "title": "question_card",
      "status": "ready",
      "lane": 1,
      "column": "artifact",
      "summary": "核心问题：神经炎症是否通过促进 Tau 病理扩散，加速阿尔茨海默病认知功能下降？",
      "source_payload_path": "payload.question_card"
    },
    {
      "id": "question_understanding:detail:core_question",
      "kind": "detail",
      "stage": "question_understanding",
      "parent_id": "question_understanding:question_card",
      "title": "核心问题",
      "status": "ready",
      "lane": 1,
      "column": "detail",
      "summary": "神经炎症是否通过促进 Tau 病理扩散，加速阿尔茨海默病认知功能下降？",
      "source_payload_path": "payload.question_card.core_question",
      "preview_fields": ["core_question", "key_variables", "sub_questions"]
    }
  ],
  "edges": [
    {
      "source": "question_understanding",
      "target": "knowledge_integration",
      "kind": "workflow"
    },
    {
      "source": "question_understanding",
      "target": "question_understanding:question_card",
      "kind": "writes"
    },
    {
      "source": "question_understanding:question_card",
      "target": "question_understanding:detail:core_question",
      "kind": "explains"
    }
  ]
}
```

摘要节点必须来自真实 Agent 返回内容，而不是固定模板。推荐映射：

|阶段|artifact|detail 摘要建议|
|---|---|---|
|问题理解|`question_card`|`core_question`、`key_variables`、`sub_questions`|
|知识整合|`literature_cards` / `evidence_cards` / `knowledge_gaps`|代表文献标题、证据 claim、知识空白 description|
|假设生成|`hypothesis_cards`|`hypothesis_id`、`statement`、`validation_idea`、`initial_scores.testability`|
|证据梳理|`evidence_map` / `reviews`|支持/反对证据摘要、`evidence_strength_score`、评审建议|
|研究计划|`research_plan`|`problem_statement`、方法、数据集、指标、失败判据、反馈任务|
|总控最终输出|`final_review` / `versions`|总体评分、优势、剩余风险、是否需要修订、最终快照状态|

为了避免前端分支重叠，后端如能计算布局，可返回 `lane`、`column`、`parent_id`；如果不返回，前端会按阶段自动分配泳道，并将主流程纵向、artifact/detail 横向展开。

## 8. 事件流

建议用 SSE：

```text
GET /api/tasks/{task_id}/events/stream
```

事件类型：

```text
task_created
task_started
stage_started
agent_output_received
review_gate_passed
human_review_requested
human_review_approved
stage_retry_requested
context_snapshot_created
task_completed
task_failed
```

SSE payload 示例：

```json
{
  "event_id": "evt_001",
  "task_id": "task_001",
  "type": "stage_started",
  "stage": "knowledge_integration",
  "message": "知识整合开始执行。",
  "created_at": "2026-07-09T10:03:00Z"
}
```

## 9. Artifact 目录建议

```text
artifacts/
  tasks/
    task_001/
      manifest.json
      task_context.latest.json
      inputs/
      outputs/
      reviews/
      versions/
      events/
      files/
      reports/
      exports/
```

关键规则：

- Agent 原始输出先写入 `outputs/`。
- Review Gate 通过后才合并 `payload` 到 `task_context.latest.json`。
- 每次合并生成 `versions/context_vXXX.json`。
- 前端 JSON 查看按钮优先读取 artifact 引用。

## 10. 推荐实现顺序

1. 先实现 `GET/POST /api/projects`，让左侧项目列表持久化。
2. 实现 `POST /api/tasks` 和 `POST /api/tasks/{id}/start`，先接 mock Agent。
3. 实现 `GET /api/tasks/{id}/messages`，让对话记录由后端恢复。
4. 实现 `POST /reviews` 和 `POST /stages/{stage}/rerun`，打通人工审批和重跑。
5. 实现 `GET /events/stream`，替换前端本地事件。
6. 实现 Artifact Service 和 JSON 详情接口。
7. 逐个替换真实 Agent Adapter。
8. 最后接入文件上传、版本 diff、导出报告和提交包。
