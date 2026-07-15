# 系统架构

```text
React Workbench
  |  task/review/feedback/artifact/version APIs + SSE
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

## Artifact 布局

```text
artifacts/tasks/{task_id}/
  manifest.json
  context/task_context.latest.json
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
