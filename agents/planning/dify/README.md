# Dify YAML 历史资产

本目录三个 YAML 只用于迁移审计，不是运行依赖。生产代码不会读取、导入或执行
这些文件，也不需要 Dify Server / Dify Cloud。

旧节点与当前本地实现的关系：

| 旧 Dify 节点 | 当前状态 |
|---|---|
| Candidate context / guard | 已退役；由 `compile_planning_brief` 和一份 protocol draft 取代 |
| Candidate generator A（三变体） | 已退役；不再重复生成三份完整计划 |
| Selection context / selector / guard B | 已退役；由三类窄审查和 Python `merge_protocol_reviews` 取代 |
| Final context / generator / contract C | 拆分为 synthesis、`guard_final_plan` 和可选 repair |
| Evidence normalization | 合并进 `compile_planning_brief`，并保留 Evidence Mapping `detailed_review` |

当前实现位于 `planning_agent/local_nodes.py`、`stage_clients.py`、
`workflow_chain.py`、`schemas.py` 和 `prompts.py`，不使用 `exec`、`eval` 或通用
代码沙箱。
