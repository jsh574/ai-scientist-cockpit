# 后端 API 对接指南

前端默认设置 `VITE_ENABLE_REAL_AGENTS=true`，任务创建后使用服务器返回的 `task_id`。后端是 `task_context`、审核、版本和 Artifact 的权威状态源。

## 项目恢复与归档

```text
GET  /api/tasks                         # 默认只返回未归档任务
GET  /api/tasks?include_archived=true   # 管理端需要时包含归档任务
GET  /api/tasks/{id}                    # manifest + task_context
POST /api/tasks/{id}/archive            # {"archived": true|false}
```

前端启动后应逐任务读取 events、attachments 和 stage detail。某个历史任务结构异常时，不得阻止其他任务恢复。

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
  "execute": true,
  "mode": "hybrid",
  "reasoning_level": "high",
  "memory_level": "medium"
}
```

反馈会先增加 iteration、写入 `feedback_events` 并创建版本，然后从目标阶段重跑。前端需要自己控制逐阶段动画时，可发送 `execute=false`，再调用阶段接口。

主输入框必须遵守以下语义：

1. 本地草稿没有后端 `task_id`：调用一次 `POST /api/tasks`。
2. 已有后端 `task_id`：调用反馈接口，明确 `target_stage`，不得创建新任务。
3. 等待人工审核：在对应模块消息尾部调用 reviews 接口，不使用主输入框绕过审核。
4. 新建任务必须通过“创建新项目”操作生成独立草稿。

## 附件

```text
GET  /api/tasks/{id}/attachments
POST /api/tasks/{id}/attachments   multipart/form-data，字段名 files
```

- 允许扩展名：`.txt`、`.md`、`.csv`、`.json`。
- 默认单文件上限 2,000,000 字节，可用 `ATTACHMENT_MAX_BYTES` 调整。
- 注入上下文的总字符数默认 30,000，可用 `ATTACHMENT_CONTEXT_CHARS` 调整。
- 文件必须是 UTF-8 文本；服务端重新校验文件名、扩展名、编码和任务目录边界。
- 前端先校验以改善体验，但不能替代服务端校验。
- Artifact Service 把附件文本写入 `task_context.user_input.question_description`；OpenAI 兼容 Agent 的 `ProjectLLMClient` 会把该背景注入用户消息，Planning Agent 则通过上游结构化上下文和 Dify Workflow 输入获得任务背景。
- 自动测试使用唯一标记断言附件文本实际出现在 OpenAI 兼容请求的 `messages[1].content`，用于防止“上传成功但模型看不到”的回归。

## 模型耗时与超时

- `LLM_TIMEOUT_SECONDS`：单次模型 HTTP 请求超时，默认 120 秒。
- `LLM_MAX_RETRIES`：OpenAI 兼容 SDK 的自动重试次数，默认 0。每增加一次重试，单个逻辑调用的最坏等待时间会再增加一个超时周期。
- `KNOWLEDGE_LLM_MAX_ATTEMPTS`：知识整合内部模型尝试次数，默认 1。该模块本身包含多个模型步骤，不应与 SDK 重试无上限叠加。
- `QWEN_ENABLE_THINKING`：默认 `false`；只有设置为 `true` 且推理等级为 high/ultra 时开启 thinking。
- high 的生成上限为 6144 tokens；ultra 使用 `LLM_MAX_TOKENS` 的完整预算。需要缩短 Demo 等待时间时，优先使用 low/medium，减少检索查询和候选数量，再按实际网络延迟调整 timeout。

某阶段长时间等待通常不等于接口断开。应先检查事件日志和阶段错误：thinking、多模型步骤、知识整合内部尝试和 SDK 重试会累积总耗时。前端会显示正在运行的累计时间；超过 120 秒转为警示色，完成后保留实际用时。

## 查询与导出

- `GET /api/tasks/{id}/context`
- `GET /api/tasks/{id}/attachments`
- `GET /api/tasks/{id}/stages`
- `GET /api/tasks/{id}/stages/{stage}`
- `GET /api/tasks/{id}/versions`
- `GET /api/tasks/{id}/versions/diff?left=v001-001&right=v002-001`
- `GET /api/tasks/{id}/artifacts`
- `GET /api/tasks/{id}/events/stream?follow=true`
- `POST /api/tasks/{id}/export`

错误统一使用 FastAPI `detail` 字段。前端不得把 API 失败替换成成功 mock；可以显示失败响应并允许用户修正配置后重试。

## 健康状态

`GET /api/health` 同时返回当前 `model`、API capabilities、每个 Agent source 的 `available`、`ready`、`credential_required`、`credential_configured`、`mode`，以及以下运行策略：

```json
{
  "llm": {
    "timeout_seconds": 120,
    "max_retries": 0,
    "thinking_enabled": false,
    "knowledge_max_attempts": 1
  }
}
```

前端必须用 `model` 动态显示真实模型名，不能写死 GPT 或 Qwen 版本；只能把 `ready=true` 计为可执行 Agent。源码目录存在但缺少模型密钥时应显示降级状态。
