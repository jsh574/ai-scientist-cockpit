# 后端 API 对接指南

前端默认设置 `VITE_ENABLE_REAL_AGENTS=true`，任务创建后使用服务器返回的 `task_id`。后端是 `task_context`、审核、版本和 Artifact 的权威状态源。

## 调用顺序

```text
POST /api/tasks
  -> POST /api/tasks/{id}/stages/question_understanding/run
  -> ...
  -> POST /api/tasks/{id}/stages/final_review/run
```

也可以调用 `POST /api/tasks/{id}/start` 让后端连续执行，遇到人工审核、重试或失败时停止。

阶段运行返回：

```json
{
  "task_id": "task_x",
  "stage": "evidence_mapping",
  "status": "human_review",
  "response": {},
  "review": {},
  "task_context": {}
}
```

## 人工审核

```http
POST /api/tasks/{id}/reviews
Content-Type: application/json

{
  "stage": "evidence_mapping",
  "decision": "accept",
  "comment": "证据边界清楚，继续。"
}
```

`decision` 可为 `accept`、`retry` 或 `rollback`。只有等待人工审核的任务可以提交决定。

## 反馈迭代

```http
POST /api/tasks/{id}/feedback
Content-Type: application/json

{
  "target_stage": "hypothesis_generation",
  "comment": "缩小研究人群并增加反向因果检验。",
  "rerun_downstream": true,
  "execute": true
}
```

反馈会先增加 iteration、写入 `feedback_events` 并创建版本，然后从目标阶段重跑。前端需要自己控制逐阶段动画时，可发送 `execute=false`，再调用阶段接口。

## 查询与导出

- `GET /api/tasks/{id}/context`
- `GET /api/tasks/{id}/stages`
- `GET /api/tasks/{id}/stages/{stage}`
- `GET /api/tasks/{id}/versions`
- `GET /api/tasks/{id}/versions/diff?left=v001-001&right=v002-001`
- `GET /api/tasks/{id}/artifacts`
- `GET /api/tasks/{id}/events/stream?follow=true`
- `POST /api/tasks/{id}/export`

错误统一使用 FastAPI `detail` 字段。前端不得把 API 失败替换成成功 mock；可以显示失败响应并允许用户修正配置后重试。
