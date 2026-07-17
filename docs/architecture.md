# 系统架构

```text
React Workbench
  |  task/archive/attachment/review/feedback/artifact/version APIs + SSE
FastAPI
  |-- Orchestrator
  |     |-- Context slicer and merge policy
  |     |-- Review Gate
  |     `-- Iteration controller
  |-- Unified Agent Registry
  |     |-- question understanding
  |     |-- knowledge integration
  |     |-- hypothesis generation
  |     |-- evidence mapping
  |     `-- research planning
  `-- Artifact Service <---- MCP stdio server
        `-- artifacts/tasks/{task_id}/...
```

## 统一 Agent 契约

每个 Agent 在 `agents/registry.json` 声明 `agent_id`、阶段、入口、读取字段和写入字段。总控只传递声明的上下文切片，并拒绝 Agent 写入契约外字段。

所有响应必须符合 `schemas/agent_response.schema.json`：

```json
{
  "metadata": {
    "task_id": "task_x",
    "agent_id": "hypothesis_generation_agent",
    "stage": "hypothesis_generation",
    "iteration": 1,
    "status": "success",
    "trace_id": "trace_x",
    "duration_ms": 1234
  },
  "payload": {},
  "self_review": {
    "passed": true,
    "overall_score": 0.86,
    "threshold": 0.75,
    "dimension_scores": {},
    "issues": [],
    "suggestions": []
  }
}
```

## Review Gate

Review Gate 在合并上下文前执行：

- Pydantic 结构校验。
- task、stage、iteration 一致性校验。
- 阶段写入白名单与非空检查。
- 文献 DOI/URL 追溯检查。
- hypothesis/evidence/literature ID 引用完整性检查。
- Agent 自评分与总控评分阈值检查。

自动模式通过后直接合并；人工模式暂停；混合模式在证据梳理和研究计划阶段暂停。

## 前端状态边界

- 后端 `task_context` 和 `manifest.json` 是已创建任务的权威状态源。
- 前端只在尚未提交科学问题时维护本地草稿；首次提交调用一次 `POST /api/tasks`。
- 已有 `task_id` 后，主输入框只提交指定阶段的反馈，不得再次创建任务。
- 页面刷新通过 `GET /api/tasks` 恢复未归档任务，再读取 context、events、attachments 和六阶段详情。
- 单个旧格式任务恢复失败时，其余任务继续载入，错误原因显示在系统页。
- PC 完整状态树一次展开六阶段主干、当前有效 Artifact 和 Agent 输出摘要，按“阶段主干 -> 写入对象 -> 返回摘要”的阶段带展示；节点标注产出来源 iteration，完整输入、输出、审核和 JSON 在右侧检查器中展示。
- 反馈回退会使目标阶段及其下游结果失效：前端立即清空对应 StageRun，后端同时清空 task_context 字段和旧审核、重置 manifest 状态。状态树不得用旧 `latest.output.json` 填充 queued/retrying/running 节点。
- 亮色与暗色主题由根节点 `data-theme` 和 CSS 语义变量控制，用户选择保存在浏览器本地；任务数据和主题偏好互不耦合。

## Artifact 布局

```text
artifacts/tasks/{task_id}/
  manifest.json
  context/task_context.latest.json
  attachments/index.json
  attachments/{attachment_id}_{filename}
  stages/{stage}/i001.input.json
  stages/{stage}/i001.output.json
  stages/{stage}/latest.output.json
  reviews/{stage}.latest.review.json
  versions/{version_id}/task_context.json
  events/trace.jsonl
  notes/*.md
  exports/{task_id}.zip
```

JSON 文件使用同目录临时文件和原子替换写入。所有任务 ID 和相对路径都经过白名单与目录边界校验。

附件仅允许 UTF-8 文本格式。文件元数据写入 `user_input.attachments`，文本摘要合并到 `user_input.question_description`。`ProjectLLMClient` 会把该背景注入所有真实模型请求的用户消息，已有相同内容时不重复注入。
