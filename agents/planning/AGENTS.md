# Planning Agent Engineering Guide

本目录是总控仓库内 Research Planning Agent 的唯一维护源。用户直接指令和总控仓库协议优先于本文件。

## Git 与验证

- 开始修改前同步总控当前 `STAR/` 功能分支，确认没有落后远程；网络不可用时必须记录。
- 不要直接向 `main` 推送，也不要提交 `.env`、`.venv/`、`.tmp/`、CLI 输出、模型响应或调试日志。
- 修改后先从本目录运行 `python -m pytest`，再运行相关总控集成测试。

## 正式运行架构

- 唯一正式路径是本地 Python `protocol draft -> 三类并行 review -> synthesis -> 可选 repair` 编排，并通过阿里云百炼 OpenAI 兼容接口调用 Qwen。
- `dify/*.yml` 是只读历史迁移资产，不得被生产代码、健康检查、启动脚本或正式配置加载。
- 固定确定性节点位于 `planning_agent/local_nodes.py`。不得使用 `exec`、`eval` 或引入通用用户代码沙箱。
- 模型 JSON 在身份归一化、审查范围约束和引用清理后必须通过 `planning_agent/schemas.py` 的 JSON Schema。
- 不得新增输入 allowlist 之外的文献或证据，不得编造数据集 URL，不得声称实验已执行。

## 总控运行契约

- 保留确定性 brief、审查合并、最终契约修复、反馈传递、失败隔离、聚合和可追溯性护栏。
- 总控通过 `planning_agent.service.run_planning_agent` 调用本包；保留 `progress_handler`、`cancellation_checker` 和离线 `workflow_runner` 注入点。
- 新事件入口是 `execution_event_handler`；`workflow_event_handler` 只保留一个版本的兼容别名，两者不得同时传入。
- 正式响应继续使用总控 AgentResponse，`research_planning` 只能写入 `research_plan`。
- 凭据只允许从环境变量读取。事件和日志不得包含 prompt、原始模型输出、API Key 或 `reasoning_content`。
