# EurekaLoop · 灵光闭环

EurekaLoop 是一个面向“AI agent for scientist”的科研工作台。问题理解、知识整合、候选假设生成、证据梳理和研究计划五个 Agent 的源码均已收进本仓库；最终审核仍使用 mock，以便在其余模块交付前保持完整演示链路。

## Repository Layout

```text
ai-scientist-cockpit-main/
├─ agents/                    # 五个可随仓库提交的 Agent 源码快照
├─ backend/                   # FastAPI 总控网关、适配器与测试
├─ src/                       # React 前端
├─ docs/                      # 接入和部署文档
└─ backend/.env.example       # 唯一配置模板，不含真实密钥
```

默认运行不依赖仓库外部目录。`backend/app/settings.py` 会从项目内的 `agents/` 加载所有 Agent；环境变量中的路径配置只用于单独调试外部源码。

## Current Scope

本仓库根据项目目录中的三份文档设计：

- `赛题文档.md`：要求展示可交互前端、测试入口、代表性案例，以及“科学问题 → 假设/计划 → 反馈迭代”的科研闭环。
- `数据规范_v0.1.md`：定义 `task_context`、5 个 Agent 的输入输出、统一响应格式 `metadata/payload/self_review`。
- `总控层与前端设计方案v0.1.md`：要求总控负责状态管理、调度、校验、Review Gate、Artifact/版本/事件追踪，前端负责可观察和可干预。

## Demo Features

- Codex-like conversation thread: user question, every Agent output, revision feedback, and final controller output are all shown as chat records.
- Inline Review Gate: approval and rerun controls appear at the end of the related module message, not in a separate popup.
- Message index rail: the thin left rail indexes each input/output and can jump back to a message.
- Controller controls: reasoning level, access permission, and memory level use Codex-style dropdown controls.
- File attachment affordance: the composer keeps a `+` button with the tooltip “添加文件等内容”.
- Side state tree: compact branch tree stays in the side rail; clicking a node opens the full React Flow state tree with six stage lanes, artifact branches, and concrete payload-derived summaries.
- Chinese / English switch and a concise in-app guide page.
- Browser-first direction. Desktop wrapping is deferred until the web experience and backend contract are stable.

## Hybrid Workflow

```text
用户输入
-> 问题理解
-> 知识整合
-> 候选假设生成
-> 证据梳理
-> 研究计划输出
-> 总控最终审核
```

Each module returns the planned unified response shape:

```text
metadata + payload + self_review
```

The controller only writes the payload into `task_context` after validation or approval.

前五个阶段通过 `backend/app/adapters.py` 调用队友 Agent，并统一输入输出格式。最终审核阶段暂由 `src/mockData.ts` 提供。

## Tech Stack

- React
- Vite
- TypeScript
- React Flow
- lucide-react
- FastAPI
- Python 3.10+

## Local Run

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Copy-Item backend\.env.example backend\.env
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

填写 `backend/.env` 中的模型密钥即可；Agent 路径默认已经指向项目内目录。再开一个终端启动前端：

```powershell
npm install
npm run dev
```

Windows 下也可以在仓库根目录用一个命令同时启动前后端：

```powershell
.\start.ps1
```

Open:

```text
http://localhost:5173
```

提交 Git 前确认没有加入 `backend/.env`、任何 Agent 的 `.env/.venv/.git`、缓存或日志。根目录 `.gitignore` 已覆盖这些内容。

## Build

```bash
npm run build
npm run preview
```

Preview:

```text
http://localhost:4173
```

## Backend Integration Path

The current mock layer can later be replaced by these routes:

```text
POST /api/tasks
POST /api/tasks/{task_id}/start
GET  /api/tasks/{task_id}/context
GET  /api/tasks/{task_id}/stages/{stage}
POST /api/tasks/{task_id}/reviews
POST /api/tasks/{task_id}/feedback
GET  /api/tasks/{task_id}/events/stream
POST /api/tasks/{task_id}/export
```

Recommended landing path:

1. Keep this React/Vite web app as the first deployable surface.
2. Connect the real orchestrator, Agent adapters, Review Gate, Artifact Service, and SSE event stream.
3. Deploy the web version through Vercel, Netlify, or GitHub Pages.
4. Only after the browser experience is stable, wrap the same frontend with Tauri or Electron if a desktop app is still necessary.

Detailed backend notes are in [docs/backend-integration-guide.md](docs/backend-integration-guide.md). Keep that file updated whenever the frontend adds or changes a backend-facing interaction.

真实 Agent 的路径、字段映射、错误语义和启动方式见 [docs/agent-integration.md](docs/agent-integration.md)。
