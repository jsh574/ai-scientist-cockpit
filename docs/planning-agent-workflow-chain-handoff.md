# Planning Agent 协议编译器交接

## 正式架构

Planning 已从 Dify 形状的 A/B/C 候选链路迁移为本地协议编译器：

```text
planning_brief (Python)
 -> protocol draft (Qwen)
 -> methodology / statistics / feasibility reviews (Qwen, parallel)
 -> merge reviews (Python)
 -> synthesis (Qwen, thinking off)
 -> final contract (Python)
 -> repair (Qwen, optional, at most once)
```

节点 ID：

- `planning:{hypothesis_id}:brief`
- `planning:{hypothesis_id}:draft`
- `planning:{hypothesis_id}:review:methodology`
- `planning:{hypothesis_id}:review:statistics`
- `planning:{hypothesis_id}:review:feasibility`
- `planning:{hypothesis_id}:synthesis`
- `planning:{hypothesis_id}:repair`（可选）

事件仍为 `stage_started`、`model_stream_progress`、`stage_finished`、
`stage_failed`，只包含阶段、hypothesis、角色、字符数、token、时长和脱敏错误，
不保存 prompt、完整模型输出、API key 或隐藏推理。

## 与前四个 Agent 的边界

Planning 把 `question_card`、`hypothesis_cards`、`evidence_map`、
`evidence_cards`、`literature_cards`、`knowledge_gaps` 当作既有事实输入。
它不会再次检索、生成假设或重新判断证据方向。Evidence Mapping 的
`detailed_review`（binding/conflict/gap/verdict）会完整进入 planning brief。

无绑定证据时在模型调用前失败并请求上游补证据；未知 citation、未知 evidence ID
和上游未给出的 dataset URL 不进入 repair。

## 配置与兼容

正式配置：

```dotenv
PLANNING_MODEL=qwen3.7-max
PLANNING_MAX_RETRIES=1
PLANNING_MAX_REPAIR_ATTEMPTS=1
PLANNING_SYNTHESIS_CONTEXT_MAX_CHARS=16000
PLANNING_MAX_HYPOTHESES=2
PLANNING_MAX_PARALLEL_CALLS=1
PLANNING_SHOW_PROGRESS=0
```

`run_planning_agent`、AgentResponse、`research_plan` 写回、批量顺序、取消检查、
`workflow_runner` 注入以及 `workflow_event_handler` 兼容别名保持不变。
`PlanningWorkflowChainRunner` 仅保留一个版本的类型别名，生产实例使用
`PlanningProtocolRunner`。

## 正式计划契约

公开 `experiment_planner_plan_result_v1.plan` 使用
`agents/planning/docs/数据规范_v0.1.md` 定义的嵌套结构。内部模型允许出现
迁移期数组或常见 Qwen 别名，但 `local_nodes.py` 必须在 schema 校验前统一：

- technical detail 别名归并为五个固定数组字段；
- datasets/methods/experiments 归并为嵌套对象；
- `experiments.items[]` 保留多实验细节，聚合字段服务旧消费者；
- reference 从上游 literature cards 补齐，feedback task 统一为对象。

后端 ReviewGate、ControllerAssistant 和前端详情/Markdown/摘要均消费这一
规范；前端的历史兼容逻辑不得反向影响新任务的正式写出格式。

## 验收

```powershell
cd agents/planning
..\..\.venv\Scripts\python.exe -m pytest tests -q
..\..\.venv\Scripts\python.exe -m planning_agent.cli --print-runtime
..\..\.venv\Scripts\python.exe -m planning_agent.workflow_chain_cli --sample --hypothesis-id hyp_short_001

cd ../..
.\.venv\Scripts\python.exe -m pytest backend/tests -q
npm run typecheck
npm run build
```

真实 smoke 的成功判据：一个 hypothesis 有 draft、三类 review、synthesis 共 5 次
调用，最终 `references[].source_id` 和 `rationale.logic_chain` 的 ID 都来自输入
allowlist，且不出现 dataset URL 或已执行实验声明。
