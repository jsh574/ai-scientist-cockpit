# MCP Artifact Service

服务入口：

```powershell
.\.venv\Scripts\python.exe -m backend.mcp_server
```

默认使用 stdio，由 MCP 客户端启动。示例配置：

```json
{
  "mcpServers": {
    "eurekaloop-artifacts": {
      "command": "D:/path/to/project/.venv/Scripts/python.exe",
      "args": ["-m", "backend.mcp_server"],
      "cwd": "D:/path/to/project"
    }
  }
}
```

## 工具

| 工具 | 权限 |
| --- | --- |
| `list_tasks` | 读取任务 manifest |
| `get_task_context` | 读取最新 task_context |
| `list_task_artifacts` | 列举任务内文件 |
| `read_task_artifact` | 读取 1 MB 以内 UTF-8 文件 |
| `write_task_note` | 仅写入 `notes/*.md` |
| `compare_task_versions` | 比较两个上下文快照 |
| `export_task_bundle` | 导出任务 ZIP |

MCP 不提供任意路径读写。绝对路径、`..`、非法任务 ID、非 Markdown 评审笔记和超限文件都会被拒绝。Agent 正式输出必须通过 Orchestrator 写入，不能通过 MCP 绕过 Review Gate。
