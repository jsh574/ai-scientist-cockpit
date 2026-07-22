# integration/agent-module-updates 分支 Agent 模块整合初审

审查日期：2026-07-22
审查范围：当前分支中整合进来的 Agent 模块改动，重点检查其与后端总控、工作流运行器、统一响应契约和仓库提交规范的兼容性。

## 结论

当前分支可以通过轻量语法编译，但不建议直接合并到主分支。主要问题不是单个 Agent 代码是否能独立运行，而是知识整合 Agent 与后端总控的调用契约已经断开；同时存在明文 API Key、工作流暂停/取消/恢复能力丢失、进度事件格式不兼容、假设生成响应契约校验失败等集成级风险。

优先级判断：

1. 必须先处理密钥泄露。
2. 必须恢复知识整合 Agent 的后端调用兼容性。
3. 必须恢复工作流 progress/checkpoint/cancellation/resume 契约。
4. 必须修正假设生成的 `dimension_scores` 类型问题。
5. 再决定新增字段和测试产物如何进入长期协议。

## 2026-07-22 修复与更新记录

本次修复原则：以当前后端总控和工作流运行器的契约为准，不要求总控迁就新整合进来的 Agent 私有接口；新 Agent 内部新增的 `progress_callback` 能力可以保留，但必须通过兼容层接回总控已有的 `progress_handler`、`cancellation_checker`、`extensions.workflow_resume`。

### 1. 修复知识整合 Adapter 调用签名

改动文件：

- `agents/knowledge_integration/knowledge_integration_agent/adapter.py`

改了什么：

- `KnowledgeIntegrationAdapter.call()` 重新支持后端总控传入的 `progress_handler` 和 `cancellation_checker`。
- 保留新模块已有的 `progress_callback` 参数，避免破坏其独立测试和演示调用。
- `build_request()` 重新透传 `task_context["extensions"]`，让 `workflow_resume` 能继续传入 Agent。

怎么改的：

```python
def call(
    self,
    task_context: dict[str, Any],
    progress_callback: ProgressCallback | None = None,
    progress_handler: Callable[[dict[str, Any]], None] | None = None,
    cancellation_checker: Callable[[], None] | None = None,
) -> dict[str, Any]:
    return self.agent.run(
        self.build_request(task_context),
        progress_callback=progress_callback,
        progress_handler=progress_handler,
        cancellation_checker=cancellation_checker,
    )
```

改后效果：

- 后端在 `_run_knowledge_integration()` 中按原方式调用不会再出现 `unexpected keyword argument 'progress_handler'`。
- 新增的模块级 `progress_callback` 调用方式仍然可用。
- 暂停后恢复所需的 `extensions.workflow_resume` 不会在 Adapter 层被丢弃。

### 2. 修复知识整合 Agent 的取消、恢复和 checkpoint 契约

改动文件：

- `agents/knowledge_integration/knowledge_integration_agent/agent.py`

改了什么：

- `KnowledgeIntegrationAgent.run()` 重新支持总控传入的 `progress_handler` 和 `cancellation_checker`。
- 在查询规划、数据库检索、文献抽取、证据抽取、知识空白综合、质量评审等关键步骤前后调用取消检查。
- 恢复读取 `request["extensions"]["workflow_resume"]["checkpoints"]` 的逻辑。
- 如果已有 `literature_extract`、`evidence_extract`、`gap_synthesis` checkpoint，则优先复用对应中间产物。

怎么改的：

- 在 `run()` 内部增加 `check_cancelled()` 和 `checkpoint()` 两个局部函数。
- `checkpoint()` 发出的事件继续使用总控标准字段：
  - `node_id`
  - `kind`
  - `message`
  - `progress`
  - `payload`
  - `operation`
- 恢复 checkpoint 读取：

```python
resume = (request.get("extensions") or {}).get("workflow_resume") or {}
checkpoint_payloads = {
    item.get("node_id"): item.get("payload") or {}
    for item in resume.get("checkpoints") or []
    if isinstance(item, dict)
}
```

改后效果：

- 用户在长任务中点击取消，总控可以通过 `cancellation_checker` 让知识整合阶段协作停止。
- 用户暂停后恢复时，已经完成的证据卡片和知识空白可以从 checkpoint 中恢复，避免全部重跑。
- 总控的 `workflow_runs._handle_progress()` 能继续保存 `partial_output` checkpoint。

### 3. 修复知识整合进度事件格式兼容

改动文件：

- `agents/knowledge_integration/knowledge_integration_agent/agent.py`

改了什么：

- 保留新 Agent 自己的领域事件，例如 `retrieval_database_started`、`literature_extraction_completed`。
- 新增 `_emit_workflow_progress()`，把领域事件翻译成总控认识的节点事件。

怎么改的：

| 领域事件 | 转换后的 node_id | 转换后的 kind | 用途 |
| --- | --- | --- | --- |
| retrieval_database_started | source_search | progress | 展示当前检索数据库和查询词 |
| retrieval_database_completed | source_search | progress | 展示当前数据库返回数量 |
| retrieval_database_failed | source_search | progress | 展示单个数据库检索失败 |
| retrieval_completed | source_search | partial_output | 保存候选文献检索结果 |
| literature_extraction_completed | literature_extract | partial_output | 保存文献卡片 |
| evidence_extraction_completed | evidence_extract | partial_output | 保存证据卡片 |
| gap_synthesis_completed | gap_synthesis | partial_output | 保存知识空白 |

改后效果：

- 前端和总控不再只能看到泛化的 `knowledge_integration progress`。
- 节点级调试、节点中间产物、暂停恢复 checkpoint 可以继续工作。
- 新模块团队想保留的细粒度数据库检索事件也没有被删除。

### 4. 修复假设生成 `dimension_scores` 类型错误

改动文件：

- `agents/hypothesis_generation/hypothesis_generation_agent.py`

改了什么：

- `dimension_scores` 中不再写入 `None`。
- 独立评审不可用时，不再生成 `independent_eval_score: None`。
- 只有当独立评审真正可用且有数值评分时，才把 `independent_eval_score` 放入 `dimension_scores`。

怎么改的：

```python
dimension_scores = {
    "code_review_score": round(code_review_score, 3),
    ...
}
if eval_report and eval_report.get("available") and eval_report.get("overall_score") is not None:
    dimension_scores["independent_eval_score"] = round(
        float(eval_report["overall_score"]),
        3,
    )
```

改后效果：

- 后端 `AgentResponse.model_validate()` 不会再因为 `dict[str, float]` 中出现 `None` 而失败。
- 独立评审不可用的信息仍保留在 `independent_eval`、`issues`、`suggestions` 中。
- 总控 ReviewGate 可以继续按统一响应契约审查假设生成结果。

### 5. 收紧知识整合 LLM 凭证注释

改动文件：

- `agents/knowledge_integration/knowledge_integration_agent/llm.py`

改了什么：

- 移除“可以在本文件填写 API Key”的注释。
- 保持 `QWEN_API_KEY = ""`，明确共享代码只能通过环境变量读取凭证。

改后效果：

- 降低后续再次把密钥写入源码的概率。
- 与后端 `ProjectLLMClient` 的环境变量配置方式保持一致。

## 高优先级阻断项

### 1. 明文 API Key 进入代码

文件：

- `agents/knowledge_integration/knowledge_integration_agent/llm.py`

问题：

```python
QWEN_API_KEY = "sk-..."
```

风险：

- 属于真实密钥泄露风险，不能提交到远程仓库。
- 即使后续删除，若已经推送过，也需要轮换该 Key。
- 该写法绕过了项目原本通过环境变量配置模型凭证的方式，破坏部署一致性。

建议处理：

- 将 `QWEN_API_KEY` 改回空字符串或彻底移除硬编码 fallback。
- 只允许读取 `DASHSCOPE_API_KEY`、`QWEN_API_KEY`、`LLM_API_KEY` 等环境变量。
- 若该 Key 已经在任何远程或共享分支出现，需要立即吊销并重新生成。

### 2. 知识整合 Adapter 与后端总控调用签名不兼容

后端当前调用方式：

```python
KnowledgeIntegrationAdapter(...).call(
    adapted_context,
    progress_handler=progress_handler,
    cancellation_checker=cancellation_checker,
)
```

当前分支中的 Adapter 签名：

```python
def call(
    self,
    task_context: dict[str, Any],
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
```

实际验证结果：

```text
TypeError: KnowledgeIntegrationAdapter.call() got an unexpected keyword argument 'progress_handler'
```

风险：

- 通过后端运行知识整合阶段时会直接失败。
- 这不是前端展示问题，而是主流程无法进入该 Agent。

建议处理：

- 保持向后兼容签名：

```python
def call(
    self,
    task_context: dict[str, Any],
    progress_handler: Callable[[dict[str, Any]], None] | None = None,
    cancellation_checker: Callable[[], None] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
```

- 如果保留新 `progress_callback` 语义，需要在 Adapter 内做桥接，而不是要求后端总控同步改名。
- 后端和 Agent 模块之间应统一一份正式接口协议，避免团队成员各自发明回调命名。

### 3. 知识整合 Agent 丢失暂停、取消、恢复契约

主工作流依赖：

- `workflow_runs.py` 在恢复时向 `context.extensions.workflow_resume` 写入 checkpoint。
- 运行 Agent 时注入 `progress_handler` 和 `cancellation_checker`。
- `_handle_progress()` 根据 `partial_output` 事件保存 checkpoint。

当前分支变化：

- `KnowledgeIntegrationAgent.run()` 不再接收 `cancellation_checker`。
- Adapter 的 `build_request()` 不再透传 `extensions`。
- 原有 `workflow_resume` checkpoint 恢复逻辑被删除。

风险：

- 用户点击暂停后，知识整合 Agent 内部无法在长耗时检索、抽取、综合过程中协作停下。
- 用户点击取消后，Agent 可能继续跑到当前阶段结束。
- 暂停后恢复无法复用已完成的文献卡片、证据卡片、知识空白。
- 最新主分支新增的长任务体验会被回退。

建议处理：

- `KnowledgeIntegrationAgent.run()` 至少兼容：

```python
def run(
    self,
    request: dict[str, Any],
    *,
    progress_handler: Callable[[dict[str, Any]], None] | None = None,
    cancellation_checker: Callable[[], None] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
```

- 在检索前、每个数据库搜索前后、文献抽取前后、证据抽取前后、知识空白综合前后调用 `cancellation_checker()`。
- 恢复 `request["extensions"]["workflow_resume"]` 的读取逻辑。
- 对已经完成的 `literature_cards`、`evidence_cards`、`knowledge_gaps` 允许从 checkpoint 恢复。

### 4. 知识整合进度事件格式与工作流节点事件不兼容

当前新事件形态：

```python
{
    "metadata": ...,
    "event": "retrieval_database_started",
    "component": "RetrievalService",
    "payload": {...}
}
```

工作流期望形态：

```python
{
    "node_id": "source_search",
    "kind": "started",
    "message": "Searching configured literature sources.",
    "progress": 0.16,
    "payload": {...},
    "operation": "append"
}
```

风险：

- `_handle_progress()` 会把缺失的 `node_id` 默认为整个 stage。
- 前端节点级进度会退化成泛化消息。
- `partial_output` 不再稳定触发 checkpoint 保存。
- 中间产物替换/追加语义不清晰，调试面板和恢复逻辑都受影响。

建议处理：

- 保留新事件中的领域信息，但包装成工作流标准事件。
- 推荐节点映射：

| 领域事件 | node_id | kind | 建议说明 |
| --- | --- | --- | --- |
| retrieval_database_started | source_search | progress | 当前数据库和 query |
| retrieval_database_completed | source_search | progress | 当前数据库返回数量 |
| retrieval_completed | source_search | partial_output | retrieved_sources 或统计信息 |
| literature_extraction_completed | literature_extract | partial_output | literature_cards |
| evidence_extraction_completed | evidence_extract | partial_output | evidence_cards |
| gap_synthesis_completed | gap_synthesis | partial_output | knowledge_gaps |

### 5. 假设生成 `dimension_scores` 存在类型不兼容

文件：

- `agents/hypothesis_generation/hypothesis_generation_agent.py`

问题：

```python
"independent_eval_score": (
    round(float(eval_report["overall_score"]), 3)
    if eval_report and eval_report.get("available") and eval_report.get("overall_score") is not None
    else None
)
```

后端统一契约：

```python
dimension_scores: dict[str, float]
```

实际验证：

```text
AgentResponse ValidationError:
self_review.dimension_scores.independent_eval_score
Input should be a valid number
```

风险：

- 当独立评审不可用时，假设生成 Agent 即使产出了 payload，也会在 ReviewGate/AgentResponse 校验处失败。
- 该问题是数据契约问题，不是模型质量问题。

建议处理：

- 不要在 `dimension_scores` 中放 `None`。
- 可选方案：
  - 缺失时删除 `independent_eval_score` 字段。
  - 或写 `0.0`，并在 `issues` 中说明 independent evaluation unavailable。
  - 或修改统一契约允许 `dict[str, float | None]`，但这会影响全局契约，不建议为单个字段放宽。

## 中优先级风险

### 1. 知识整合在无证据卡片时直接失败，可能过于脆弱

当前逻辑：

```python
if not evidence_cards:
    return self._failure_response(...)
```

风险：

- 文献检索和文献卡片已经成功时，只因证据抽取失败就整体 `failed`。
- ReviewGate 又要求 required writes 非空，因此 `evidence_cards=[]` 和 `knowledge_gaps=[]` 会导致阶段失败。
- 对真实科研问题来说，LLM 对某几篇文献抽取失败是常见情况，不一定应该全盘失败。

建议处理：

- 区分 `failed` 和 `partial_success`。
- 如果有 `literature_cards` 但没有 `evidence_cards`，可以返回 `partial_success`，并通过 ReviewGate 决定是否人工审阅或重试证据抽取节点。
- 证据抽取错误应进入 `self_review.issues`，不要吞掉已经可用的文献产物。

### 2. 假设生成新增 `evidence_bindings`，但跨 Agent 链路没有闭合

当前变化：

- 假设卡片新增必填 `evidence_bindings`。
- 本 Agent 内部会校验它与 `based_on_evidence_ids` 一致。

风险：

- 证据映射 Agent 的 `HypothesisCard` 当前没有显式消费该字段。
- 前端类型和展示也未明确接入该字段。
- 这会形成“上游认真生成，下游忽略”的半集成状态。

建议处理：

- 若保留该字段，应升级为正式跨 Agent 协议：
  - schema 中声明字段。
  - `src/types.ts` 增加类型。
  - 证据映射 Agent 将 `evidence_bindings.inference_bridge` 纳入 evidence review prompt。
  - 前端在假设详情或证据映射视图展示证据推理桥。
- 若暂不闭合链路，则不要设为硬性必填，可作为 optional enrichment。

### 3. 假设生成默认开启独立评审，会增加延迟和调用成本

当前配置：

```python
enable_independent_eval: bool = True
eval_weight: float = 0.2
```

风险：

- 每次假设生成至少多一次 LLM 调用。
- 如果第一次生成失败并重试，整体成本和等待时间进一步上升。
- 当前后端 `_run_hypothesis_generation()` 没有显式暴露该开关，用户不知道为什么变慢。

建议处理：

- 通过环境变量或 `model_policy` 暴露开关，例如 `HYPOTHESIS_ENABLE_INDEPENDENT_EVAL=false`。
- 默认可先关闭，在“高质量模式”或“最终评审前”再打开。
- 在前端/日志中显示独立评审是否启用。

## 仓库卫生问题

### 1. 新增测试目录包含生成产物

当前未跟踪内容包括：

- `agents/knowledge_integration/tests/output.json`
- `agents/knowledge_integration/tests/output_chinese_20260721_163948.json`
- `agents/knowledge_integration/tests/output_chinese_20260721_170511.json`
- `agents/knowledge_integration/tests/__pycache__/`

风险：

- 输出 JSON 属于运行产物，不应进入代码提交。
- `__pycache__` 已被 `.gitignore` 忽略，但目前目录存在于工作区，需要避免强行添加。
- 大输出文件会污染 diff，降低代码审查效率。

建议处理：

- 只提交真正的测试代码。
- 输出 JSON 改写到 `test-artifacts/` 或临时目录。
- 如果需要保留样例输出，应放到 `examples/`，并命名为小体积、脱敏、稳定的 fixture。

### 2. 多个文件出现 CRLF/LF 提示

现象：

```text
LF will be replaced by CRLF the next time Git touches it
```

风险：

- 当前真实 diff 主要集中在 4 个 Python 文件，但 `git status` 里还显示了若干疑似换行符变化文件。
- 如果提交时混入纯换行符变化，会放大审查噪音。

建议处理：

- 提交前使用 `git diff --name-only` 和 `git diff --ignore-space-at-eol --name-only` 对照。
- 避免提交没有实质改动的文档、`__init__.py`、schema 文件。
- 后续可统一 `.gitattributes`，明确 Python、Markdown、JSON 的行尾策略。

## 已执行的轻量验证

### 通过项

执行过 Python 编译检查：

```powershell
python -m py_compile `
  agents\hypothesis_generation\hypothesis_generation_agent.py `
  agents\knowledge_integration\knowledge_integration_agent\agent.py `
  agents\knowledge_integration\knowledge_integration_agent\adapter.py `
  agents\knowledge_integration\knowledge_integration_agent\llm.py
```

结果：通过。

执行过空白检查：

```powershell
git diff --check
```

结果：未发现空白错误，仅有 CRLF/LF 提示。

### 未完成项

尝试运行 pytest：

```powershell
python -m pytest agents\knowledge_integration\tests\test_knowledge_integration_agent.py -q
```

结果：

```text
No module named pytest
```

说明：当前环境缺少 pytest，尚未完成新增测试集验证。

## 建议修复顺序

### 第一步：安全清理

- 移除硬编码 API Key。
- 清理输出 JSON 和 `__pycache__`。
- 暂不提交纯换行符变化文件。

### 第二步：恢复主控兼容

- Adapter 兼容 `progress_handler`、`cancellation_checker`。
- `KnowledgeIntegrationAgent.run()` 恢复兼容签名。
- `build_request()` 继续透传 `extensions`。
- 加回 `workflow_resume` checkpoint 读取。

### 第三步：统一进度事件

- 领域事件可以保留，但必须转换为标准节点事件。
- 标准事件字段至少包含：
  - `node_id`
  - `kind`
  - `message`
  - `progress`
  - `payload`
  - `operation`

### 第四步：修正响应契约

- `dimension_scores` 中不要放 `None`。
- 保证所有 Agent 返回都能通过 `AgentResponse.model_validate()`。
- 新增一个后端测试覆盖：
  - 知识整合 adapter 的 backend-style callback 调用。
  - 假设生成 independent eval 不可用时的响应校验。

### 第五步：决定新增能力是否进入正式协议

对于 `evidence_bindings` 有两个可选路线：

方案 A：正式纳入跨 Agent 协议。

优点：

- 能体现多 Agent 链路价值。
- 证据映射可以利用上游“证据到假设”的推理桥，提高审查质量。
- 前端可以展示每个假设为什么由这些证据推出。

缺点：

- 需要同步改 schema、类型、证据映射 prompt、前端展示。
- 改动面更大，需要更多测试。

方案 B：暂作为假设生成内部增强。

优点：

- 改动范围小。
- 不影响下游 Agent 和前端。
- 适合先快速稳定集成分支。

缺点：

- 多 Agent 协作优势体现有限。
- 新增字段价值没有真正释放。

建议：如果本分支目标是“整合其他人模块并尽快稳定”，先走方案 B；如果目标是借此升级多 Agent 协作协议，再走方案 A，但需要单独列一个协议升级任务。

## 合并前检查清单

- [x] 已删除明文 API Key，并收紧源码注释，真实 Key 是否轮换需由 Key 持有人确认。
- [x] `KnowledgeIntegrationAdapter.call()` 支持后端现有调用方式。
- [x] `KnowledgeIntegrationAgent.run()` 支持 `progress_handler` 和 `cancellation_checker`。
- [x] `extensions.workflow_resume` 能传入并被消费。
- [x] 知识整合节点进度事件能生成 `partial_output` checkpoint。
- [x] 假设生成响应能通过 `AgentResponse.model_validate()`。
- [x] `dimension_scores` 中不存在 `None`。
- [ ] 未提交 `output*.json`、`__pycache__`、临时运行产物。
- [ ] 新增测试不依赖真实 API，真实 API 演示脚本与 CI 单测分离。
- [ ] `pytest` 环境补齐后跑过知识整合和后端 adapter 相关测试。
