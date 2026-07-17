# EurekaLoop - AI Scientist Cockpit

EurekaLoop 面向挑战杯赛题 XH-202619 的“科学假设生成与研究计划设计”方向。系统把问题理解、知识整合、假设生成、证据梳理、研究计划和总控审核组织成可追踪、可审核、可反馈迭代的科研闭环。

## 已实现能力

- 5 个真实 Agent 源码均在 `agents/`，核心算法保持独立。
- 统一 Agent 协议：`metadata + payload + self_review`，自动补充 trace ID 和耗时。
- Schema-first 总控：输入裁剪、写入白名单、Review Gate 和证据 ID 追溯检查。
- 任务级编排：自动、人工和混合三种模式。
- Artifact 持久化：阶段输入输出、审核、事件、上下文和版本快照均落盘。
- 反馈迭代：反馈进入下一轮上下文，可从指定阶段重跑并比较版本差异。
- MCP Artifact Service：基于官方 MCP Python SDK，限制在任务目录内访问。
- React 工作台：真实调用后端 Agent、人工审核、反馈重跑、状态树和系统面板。
- 后端项目恢复与归档：刷新页面后恢复未归档项目，归档操作真实更新任务 manifest。
- 任务附件：支持 UTF-8 的 `.txt`、`.md`、`.csv`、`.json`，上传后进入任务上下文。
- 运行策略：推理强度、审批模式和记忆等级同时影响新任务与后续反馈迭代。
- 可调用 API、SSE 事件流和任务 ZIP 导出。

## 目录

```text
agents/                 五个 Agent 和机器可读注册表
artifacts/tasks/        运行期任务产物，默认不提交 Git
backend/app/            协议、适配器、总控、审核、Artifact 服务和 API
backend/mcp_server.py   MCP stdio 服务
backend/tests/          Agent 契约与总控测试
schemas/                task_context 和统一响应 JSON Schema
src/                    React 工作台
docs/                   架构、差距分析、MCP、部署和接入文档
```

## 快速启动

要求：Python 3.10+、Node.js 20+。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Copy-Item backend\.env.example backend\.env
npm install
```

在 `backend/.env` 中配置模型与 Dify 凭据。问题理解、知识整合和假设生成使用 OpenAI 兼容接口；研究计划 Agent 使用其原生 Dify Workflow 后端。密钥文件不会被 Git 跟踪。

一键启动：

```powershell
.\start.ps1
```

访问：

- 前端：http://127.0.0.1:5173
- API：http://127.0.0.1:8000
- OpenAPI：http://127.0.0.1:8000/docs

## MCP

MCP 服务由客户端按 stdio 方式启动：

```powershell
.\.venv\Scripts\python.exe -m backend.mcp_server
```

工具包括任务列表、上下文读取、Artifact 列举/读取、评审笔记、版本比较和导出。服务拒绝绝对路径与 `..` 路径穿越。配置示例见 [docs/mcp.md](docs/mcp.md)。

## 测试与构建

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend\tests -v
npm run typecheck
npm run build
```

容器启动：

```powershell
docker compose up --build
```

## 核心 API

```text
POST /api/tasks
POST /api/tasks/{task_id}/start
POST /api/tasks/{task_id}/stages/{stage}/run
GET  /api/tasks/{task_id}/stages/{stage}
GET  /api/tasks
POST /api/tasks/{task_id}/archive
GET  /api/tasks/{task_id}/attachments
POST /api/tasks/{task_id}/attachments
POST /api/tasks/{task_id}/reviews
POST /api/tasks/{task_id}/feedback
GET  /api/tasks/{task_id}/versions/diff
GET  /api/tasks/{task_id}/artifacts
GET  /api/tasks/{task_id}/events/stream
POST /api/tasks/{task_id}/export
```

## 文档

- [现状与差距](docs/gap-analysis.md)
- [系统架构](docs/architecture.md)
- [Agent 接入规范](docs/agent-integration.md)
- [后端 API 对接指南](docs/backend-integration-guide.md)
- [MCP 服务](docs/mcp.md)
- [部署](docs/deployment.md)
- [分支批次优化记录](docs/branch-optimizations/feat-product-hardening-batch-1/README.md)

`backend/.env`、根目录 `.env`、运行 Artifact、日志、虚拟环境和构建目录均不得提交。
