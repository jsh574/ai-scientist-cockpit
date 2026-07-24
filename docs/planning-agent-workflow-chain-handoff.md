# Planning Agent A/B/C 合并与部署交接

## 本次范围

Planning Agent 已新增三个可独立发布和调用的 Dify Workflow：

| 模块 | Dify Workflow | 输入重点 | End 输出 |
|---|---|---|---|
| A | Planning Design Candidate Generator | 单 hypothesis、`variant_mode`、证据包和约束 | `design_candidate`, `guardrail_report` |
| B | Planning Design Judge Selector | A 的候选数组、同一证据包和约束 | `design_selection`, `selected_design`, `selection_guardrail_report` |
| C | Research Planning Agent | 原模块 5 单 hypothesis 输入和带选中设计的约束 | `plan_result` |

完整测试实现、命令和 HTML 报告位于独立开发仓库：

```text
D:\Code\Project\Python\Planning Agent
```

核心交接文档：

```text
D:\Code\Project\Python\Planning Agent\docs\三工作流链路测试与前端对接.md
```

总控现已补齐 Planning 模块输入协议、同步 A/B/C DSL 资产并接入正式服务。A/B/C 是唯一运行路径，缺少任一 App 配置时 `run_planning_agent` 会明确失败。本阶段只返回阻塞式正式结果，trace 持久化、流式展示和人工选择后恢复协议仍待后续实现。
## 对外模块契约与内部 Workflow 契约

接入时必须区分三层数据：

| 层级 | 用途 | 是否写入 task_context |
|---|---|---|
| 模块 5 输入 | 总控从统一上下文切片后调用 Planning Agent | 否 |
| A/B/C 内部 trace | 候选生成、评审、路由、调试和人工介入 | 否 |
| 正式 AgentResponse | Review Gate 校验并写回研究计划 | 仅写入 research_plan |

### 模块 5 的正式输入

总控应提供 experiment_planner_input_v1：

~~~json
{
  "schema_version": "experiment_planner_input_v1",
  "task_id": "task_001",
  "iteration": 1,
  "request_mode": "batch",
  "question_card": {},
  "hypothesis_cards": [],
  "evidence_map": [],
  "literature_cards": [],
  "evidence_cards": [],
  "knowledge_gaps": [],
  "user_constraints": {},
  "planning_constraints": {}
}
~~~

字段来源固定：

- question_card 来自模块 1。
- literature_cards、evidence_cards、knowledge_gaps 来自模块 2。
- hypothesis_cards 来自模块 3。
- evidence_map 来自模块 4。
- user_constraints 来自 task_context.user_input.user_constraints。
- planning_constraints 由总控或 Planning Agent 使用默认值补齐。
- 上游不需要提供 problem_frame、hypothesis_bundles 或 hypothesis_evidence_package。

request_mode=single 表示只有一个待规划假设；batch 表示包含多个候选假设。未显式指定时，cockpit 适配器按 hypothesis_cards 数量推导。

### Workflow A 的实际输入

Planning Agent 先按 hypothesis_id 派生紧凑证据包，再针对同一假设以三个 variant_mode 并发调用 A。每次 Dify 请求的 inputs 是：

~~~json
{
  "task_id": "task_001",
  "iteration": 1,
  "hypothesis_id": "hyp_001",
  "variant_mode": "minimum_viable",
  "question_card": "{...JSON string...}",
  "hypothesis_evidence_package": "{...JSON string...}",
  "planning_constraints": "{...JSON string...}",
  "user_constraints": "{...JSON string...}",
  "_feedback": "本轮操作者或审核意见；首轮为空字符串"
}
~~~

三个 A 变体共享同一份 `_feedback`。A 必须在不改变 hypothesis 身份、不绕过 evidence/source allowlist 且不违反 planning/user constraints 的前提下优先吸收反馈。B 的 `revision_instruction` 仍是链路内部的单次受控修订指令，不替代 `_feedback`。

hypothesis_evidence_package 由 Planning Agent 内部派生：

~~~json
{
  "hypothesis_id": "hyp_001",
  "hypothesis": "假设陈述",
  "rationale": "上游给出的依据",
  "target_variables": [],
  "expected_observation": "",
  "validation_idea": "",
  "scores": {
    "initial_scores": {},
    "evidence_strength_score": 0.8,
    "selection_score": 0.75
  },
  "evidence_subset": {
    "supporting_evidence": [],
    "opposing_evidence": [],
    "uncertain_evidence": []
  },
  "source_literature": [],
  "knowledge_gaps": [],
  "limitations": [],
  "needs_more_evidence": false,
  "evidence_summary": {}
}
~~~

默认 variant_mode 是 minimum_viable、high_information、resource_efficient。Workflow A End 输出：

~~~json
{
  "design_candidate": {
    "schema_version": "design_candidate_v1",
    "candidate_id": "hyp_001::minimum_viable",
    "hypothesis_id": "hyp_001",
    "variant_mode": "minimum_viable",
    "status": "success",
    "planning_objective": "",
    "design_type": "",
    "rationale": {},
    "variables": {},
    "operationalization": [],
    "data_contract": {},
    "method_steps": [],
    "baselines": [],
    "metrics": [],
    "statistical_analysis": [],
    "falsification_matrix": [],
    "contingencies": [],
    "resource_profile": {},
    "feedback_tasks": [],
    "limitations": []
  },
  "guardrail_report": {
    "passed": true,
    "issues": [],
    "allowed_evidence_count": 0,
    "allowed_source_count": 0,
    "normalized_identity_fields": []
  }
}
~~~

只有 guardrail_report.passed=true 且候选 status != failed 时才进入 B。A 阶段 partial_success 表示至少一个候选可用、但并非所有变体都可用；它不等于整条链路失败。

### Workflow B 的路由输出

B 接收 A 的可用候选数组，返回 design_selection_v1、selected_design 和 selection_guardrail_report。只有以下条件同时成立时才能调用 C：

~~~text
decision == accept
selection_guardrail_report.passed == true
selected_design 非空
~~~

human_review、feedback_required、revise_once 和 failed 都必须先停止，不能在前端或总控侧强行继续 C。

### Workflow C 的实际输入与输出

C 只接收 B 已选定设计后的链路输入：

~~~json
{
  "task_id": "task_001",
  "iteration": 1,
  "hypothesis_id": "hyp_001",
  "question_card": "{...JSON string...}",
  "hypothesis_evidence_package": "{...JSON string...}",
  "planning_constraints": "{...JSON string...}",
  "user_constraints": "{...JSON string...}"
}
~~~

A/B/C 链路会在 planning_constraints 内增加通过 B 的 selected_design 和 design_selection。Workflow C End 返回一个单假设结果，不返回模块级 plans[]：

~~~json
{
  "plan_result": {
    "schema_version": "experiment_planner_plan_result_v1",
    "agent_name": "ExperimentPlannerAgent",
    "task_id": "task_001",
    "iteration": 1,
    "hypothesis_id": "hyp_001",
    "status": "success",
    "error_message": "",
    "plan": {
      "problem_statement": "",
      "rationale": {"text": "", "logic_chain": []},
      "technical_details": {},
      "datasets": {"source": [], "target": []},
      "paper_title": "",
      "paper_abstract": "",
      "methods": {},
      "experiments": {},
      "results": {},
      "references": [],
      "feedback_tasks": [],
      "limitations": []
    }
  },
  "contract_report": {
    "passed": true,
    "issues": [],
    "normalized_identity_fields": []
  }
}
~~~

references[].source_id 只能引用 literature_cards[].literature_id；logic_chain[].evidence_ids 只能引用 evidence_cards[].evidence_id。C 的 Contract Code 节点负责覆盖系统身份字段并执行最终确定性校验。

### Cockpit 对外正式输出

Python 服务把多个单假设 plan_result 聚合后，由 cockpit 包装成统一响应：

~~~json
{
  "metadata": {
    "task_id": "task_001",
    "agent_id": "research_planning_agent",
    "stage": "research_planning",
    "iteration": 1,
    "status": "success"
  },
  "payload": {
    "research_plan": {
      "schema_version": "experiment_planner_output_v1",
      "agent_name": "ExperimentPlannerAgent",
      "task_id": "task_001",
      "iteration": 1,
      "status": "success",
      "plans": [
        {
          "hypothesis_id": "hyp_001",
          "status": "success",
          "error_message": "",
          "plan": {}
        }
      ]
    }
  },
  "self_review": {
    "passed": true,
    "overall_score": 0.82,
    "threshold": 0.75,
    "dimension_scores": {},
    "issues": [],
    "suggestions": []
  }
}
~~~

Review Gate 接受后只把 payload.research_plan 写入 task_context.research_plan。A/B 候选、评分、Dify run ID 和节点事件不能成为新的共享领域字段。

## 当前 Cockpit 调用方式

当前前端调用 POST /api/tasks/{task_id}/stages/research_planning/run，并阻塞等待整个阶段返回：

~~~text
task_context
-> backend.app.adapters.planning_request
-> agents/planning/planning_agent/service.py
-> Dify
-> metadata + payload + self_review
-> Review Gate
-> task_context.research_plan
~~~

当前生产 `service.py` 始终执行完整 A/B/C 链路，并将内部 batch trace 归一化回既有 `metadata + payload(research_plan) + self_review` 契约；配置不完整时返回明确的配置错误。A/B trace 仍不写入共享 `task_context`。

Cockpit 已有 GET /api/tasks/{task_id}/events/stream?follow=true，但目前只读取总控事件。Planning Agent 的 Dify workflow_started/node_started/node_finished/workflow_finished 尚未桥接到该事件日志。前端 agentApi.ts 也只有阻塞式 executeStage() 和一次性 fetchTaskEvents()，没有 EventSource 订阅，也没有 B 候选选择并恢复 C 的协议。

## 后续前端接入工作

前端开发前先完成两项后端工作：

1. 将 A/B/C runner 接入 planning_agent/service.py，保持正式 AgentResponse 和 research_plan 写回契约不变。
2. 将可公开的 Planning trace 持久化为独立 stage artifact，并把结构化进度桥接到 cockpit task event。

事件只应携带 workflow、event、round、attempt、variant_mode、node_title、status 和 workflow_run_id；禁止携带 API Key、chain-of-thought 或未清理的 text_chunk。

前端负责人随后需要：

1. 在 agentApi.ts 增加 EventSource 订阅 /events/stream?follow=true，按 event_id 去重并支持断线重连。
2. 保留 executeStage() 作为启动命令；SSE 只负责进度，最终状态以 stage detail 或 task_context 快照为准。
3. 展示 A 的三个稳定槽位、候选状态和 guardrail，而不是原始 token。
4. 展示 B 的七维评分、优缺点、选择理由和 next_action。
5. 对 human_review 提供候选选择界面。现有人工审核只能接受/重试整个 stage，尚不能提交 selected_candidate_id 并从 C 恢复，因此需要先新增后端协议。
6. C 完成后从 payload.research_plan 或 task_context.research_plan 渲染最终计划，不从 trace 拼装正式结果。
7. 页面刷新后先请求 stage detail/trace artifact 重建状态，再订阅新事件。

| 状态 | 前端含义 |
|---|---|
| running | 阶段正在执行 |
| A partial_success | 部分候选被拒，但仍有候选可进入 B |
| human_review | 等待用户选择或确认，不是系统错误 |
| feedback_required | 需要上游或用户补证据 |
| partial_success final | 已有计划，但需要复核 |
| failed | API、schema、guardrail 或计划生成失败 |
| success | C 返回有效计划并通过总控 Review Gate |

## 部署环境变量

三个 Workflow 是三个 Dify App，即使共用 Dify Host，也必须分别创建 App API Key。将真实值写到 `backend/.env`，不要写入或提交 `backend/.env.example`：

```env
DIFY_API_URL=https://your-dify-host.example.com
DIFY_WORKFLOW_A_API_KEY=<candidate-generator-app-key>
DIFY_WORKFLOW_B_API_KEY=<judge-selector-app-key>
DIFY_WORKFLOW_C_API_KEY=<final-planner-app-key>

DIFY_CHAIN_RESPONSE_MODE=streaming
DIFY_CHAIN_TIMEOUT_SECONDS=300
DIFY_WORKFLOW_C_PLANNING_CONSTRAINTS_MAX_CHARS=12000
```

配置规则：

- A/B/C 的 `*_API_URL` 为空时共用 `DIFY_API_URL`。
- A/B/C 的三个 `*_API_KEY` 都是必填项，彼此不得回退或复用；Dify App Key 不能跨 App 调用。

Docker Compose 已通过 `backend/.env` 注入 backend 容器，因此不需要把 Key 写进 `docker-compose.yml` 或镜像。部署时只需要更新服务器上的 `backend/.env` 并重建/重启 backend。

## 测试报告与正式 Agent 输出的区别

测试运行器返回 `planning_workflow_chain_test_v1`，用于验证和展示中间结果：

```json
{
  "status": "success",
  "decision": "accept",
  "next_action": "continue_to_product",
  "stages": [],
  "intermediate_results": {
    "candidate_rounds": [],
    "selection_rounds": []
  },
  "final_result": {},
  "errors": []
}
```

它不是模块 5 的正式 Agent response。当前正式 response 仍为：

```text
metadata + payload(research_plan) + self_review
```

前端合并时不要用测试报告根结构替换正式 Agent response，也不要改变 `backend/app/agent_protocol.py` 中 `research_planning` 只能写 `research_plan` 的边界。

## 前端可使用的稳定中间字段

未来接入实时/阶段展示时，建议将测试报告字段作为 Planning Agent 自己的 trace，而不是新建上游领域字段：

| 页面区域 | 字段 |
|---|---|
| 总状态 | `status`, `decision`, `next_action`, `duration_seconds` |
| 阶段时间线 | `stages[].stage_id`, `status`, `workflow_run_id`, `elapsed_time` |
| 候选设计对比 | `intermediate_results.candidate_rounds[].candidates[]` |
| 候选护栏 | `guardrail_reports[]` |
| 评审与选择理由 | `selection_rounds[].design_selection` |
| 选择校验 | `selection_guardrail_report` |
| 最终计划 | `final_result` |
| 阻断处理 | `errors[]`, `next_action` |

不要展示模型 chain-of-thought。Streaming 的 `text_chunk` 仅用于字符计数或调试；前端应展示结构化节点/阶段事件和 End JSON。

## 状态机

```text
A candidates
  -> B accept ---------------------------> C plan_result
  -> B revise_once -> A/B retry once ----> accept or requires_action
  -> B feedback_required ----------------> stop, request upstream/user input
  -> B human_review ---------------------> stop, show comparison for review
  -> B failed ---------------------------> stop, show run ID and error
```

只有 `accept` 且 `selected_design` 非空时可以调用 C。B 的确定性 guard 会在所有非 `accept` 决策下清空 `selected_design`，因此不能把 `revise_once` 当作可继续生成最终计划的状态。

## 上下文控制

C 的 `planning_constraints` 当前 DSL 长度上限为 12000 字符。调用 C 时先尝试传入：

```json
{
  "...original_constraints": "...",
  "selected_design": {},
  "design_selection": {}
}
```

超过预算时，只移除 B 中可重建的逐候选评审明细，完整保留 `selected_design` 和决策摘要。压缩后仍超过预算必须返回明确失败，不能截断 JSON。测试报告的 C stage 会通过 `context_control.strategy` 表明是否发生压缩。

## Thinking 模型与结构化输出对接规则

2026-07-22 的真实测试暴露了两类不同故障：

1. A 曾经生成了正确 structured_output，但 Guard Code 错读 text；text 中包含 thinking 内容，直接 JSON 解析失败。
2. B 在开启思考模式时，text 中已经有完整 design_selection_v1，但 Dify structured_output 错误变成单个 schema 叶子 {"type":"boolean"}。关闭 B 思考模式后，structured_output 和业务 guard 均恢复正常，单次耗时也从约 122 秒降到约 20 秒。

因此当前发布基线是：

- A/B 的严格 JSON 节点统一设置 enable_thinking=false。
- A Guard 读取 design_candidate_llm.structured_output。
- B Guard 读取 judge_selector.structured_output。
- Code 入参使用对象语义名称 candidate_payload / selection_payload。
- Dify status=success 只表示执行成功；仍须校验 End schema、业务 guard、系统身份和引用 allowlist。
- 如果未来必须开启思考，不要同时依赖当前 provider 的 structured_output 提取；应改为读取 text、剥离 thinking/围栏后使用结构化 JSON 解码器显式解析和报错。

| Workflow | 思考模式 | Guard Code 输入 |
|---|---|---|
| A | false | design_candidate_llm.structured_output |
| B | false | judge_selector.structured_output |
| C | false | 各 LLM structured_output，最终进入 final_contract |

本地 YML 变更不会自动更新已经发布的 Dify App。部署人员必须在画布中同步或重新导入并发布，再执行真实 smoke test。

## 建议验证顺序

1. 在总仓库 `agents/planning` 执行离线全量 pytest。
2. 配置三个真实 Dify App Key，执行 `--print-targets`。
3. 使用 short sample 跑 `accept` smoke test，保存 JSON/HTML 本地产物并按 run ID 对照 Dify 后台。
4. 验证 `human_review`、错误 Key、未知 hypothesis ID 等阻断路径。
5. 最后由前端负责人按上述稳定字段增加阶段展示；加载动画和实时 SSE 展示不属于本次变更。

## 合并前验收

- 三个 App Key 均可用，报告和日志中不出现 Key。
- A 三种模式返回不同 `candidate_id`，且 hypothesis ID 一致。
- B 收到的候选是 JSON 数组，不是双重转义对象。
- B 的 candidate/source/evidence ID 全部在输入 allowlist 内。
- 非 `accept` 路径没有 Workflow C run ID。
- C 返回可解析的 `plan_result`，现有模块 5 schema/traceability 测试继续通过。
- 未修改 `src`、其他 Agent 目录和 `backend/app/agent_protocol.py`。
