# Planning Agent：本地研究协议编译器

Planning 的正式运行路径是本地 Python 编排加阿里云百炼 Qwen。它不依赖
Dify、Docker、WSL 或本地模型权重。

## 职责边界

Planning 只把前四个 Agent 的结构化结果编译成研究协议：

- 问题理解已经确定研究对象、变量、子问题和 scope；
- 知识整合已经给出文献、证据与知识缺口；
- 假设生成已经固定 hypothesis、预期观测和 validation idea；
- 证据映射已经完成支持/反对/不确定分类、强度、冲突和限制分析。

Planning 不检索文献、不生成或改写 hypothesis、不重评证据、不发明数据集，
也不执行实验或声称出现了观测结果。

## 运行流程

```text
上游结构化输入
  -> Python 编译 planning_brief，并验证 hypothesis/证据可用性
  -> Qwen 生成一份 protocol draft
  -> 三个并行窄审查：methodology / statistics / feasibility
  -> Python 按 severity 稳定去重、合并 required changes
  -> Qwen synthesis 一次性定稿
  -> Python 校验 identity、JSON Schema、内容完整性、引用 allowlist 和数据集 URL
  -> 仅在可修复的终稿契约错误时追加一次 repair
  -> 按原 hypothesis 顺序聚合为 plans[]
```

正常每个 hypothesis 是 5 次模型调用：1 次草案、3 次并行审查、1 次定稿。
不存在模型选择器。单个审查失败会被隔离并令计划标为 `partial_success`；没有
上游证据、未知引用或发明数据集 URL 属于不可修复错误，不会用模型兜底。

这一结构借鉴了 [GPT Researcher](https://github.com/assafelovic/gpt-researcher)
的角色分离与聚合、[STORM](https://github.com/stanford-oval/storm) 的多视角审查、
[Open Deep Research](https://github.com/langchain-ai/open_deep_research) 的并行研究者
与最终综合，以及 [DeerFlow](https://github.com/bytedance/deer-flow) 的 Python 状态控制。
没有复制这些系统的检索、问题生成或工具执行阶段，因为它们已由上游 Agent 负责。

## 配置

```dotenv
DASHSCOPE_API_KEY=
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
PLANNING_MODEL=qwen3.7-max
PLANNING_MAX_RETRIES=1
PLANNING_MAX_REPAIR_ATTEMPTS=1
PLANNING_SYNTHESIS_CONTEXT_MAX_CHARS=16000
PLANNING_MAX_HYPOTHESES=2
PLANNING_MAX_PARALLEL_CALLS=1
PLANNING_SHOW_PROGRESS=1
```

兼容读取 `QWEN_API_KEY` / `LLM_API_KEY`、`QWEN_MODEL` / `LLM_MODEL`。
`PLANNING_MAX_PARALLEL_CALLS` 是跨 hypothesis 和三个审查的全局模型调用上限。
旧 `PLANNING_SELECTOR_MAX_FORMAT_RETRIES` 与 `PLANNING_FINAL_CONTEXT_MAX_CHARS`
只保留一个迁移版本的读取兼容；正式示例不再使用。

## 运行与测试

在仓库根目录重建虚拟环境并安装统一依赖后：

```powershell
cd agents/planning
..\..\.venv\Scripts\python.exe -m planning_agent.cli --print-runtime
..\..\.venv\Scripts\python.exe -m pytest tests -q
..\..\.venv\Scripts\python.exe -m planning_agent.workflow_chain_cli --sample --hypothesis-id hyp_short_001
..\..\.venv\Scripts\python.exe -m planning_agent.workflow_chain_cli --sample --all-hypotheses --max-parallel-hypotheses 2 --max-parallel-calls 3
```

正式调用入口仍为：

```python
from planning_agent import run_planning_agent

response = run_planning_agent(module5_input)
```

对外仍返回 `metadata + payload + self_review`，最终计划位于
`payload.plans[]`；经后端接入后位于 `payload.research_plan.plans[]`。

正式计划字段遵循 [数据规范 v0.1](docs/数据规范_v0.1.md#93-输出) 的
`experiment_planner_plan_result_v1` 嵌套契约。模型产生的数组/别名形状会在
`local_nodes.py` 归一化后再写出；前端只在读取历史任务时兼容旧形状。

## 代码结构

- `adapter.py`：构建单 hypothesis 证据包，保留 Evidence Mapping 的 detailed review；
- `local_nodes.py`：brief、审查合并、身份/引用/终稿契约等确定性函数；
- `stage_clients.py`：草案、三个审查、定稿和可选修复的本地百炼调用；
- `workflow_chain.py`：并发、全局调用上限、失败隔离、顺序聚合和取消；
- `runtime.py`：百炼流式 JSON 客户端、token 与脱敏事件；
- `schemas.py` / `prompts.py`：本地结构化契约与阶段提示；
- `dify/*.yml`：只读迁移审计材料，生产代码不会加载。
