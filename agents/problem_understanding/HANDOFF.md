# problem_understanding 模块交接说明

更新时间：2026-08-12

## 当前目标

只修改 `agents/problem_understanding`，使问题理解模块既满足多轮历史回灌需求，又与其他 Agent 的输入校验、统一协议、输出重试和 `self_review` 设计对齐。不得修改 backend 或其他模块。

每个迭代轮的 Prompt 要包含原始问题、问题解释、上一轮 System/User Prompt、上一轮运行结果、上一轮问题卡和当前用户反馈。首轮反馈为空。

## 已完成内容

### 多轮上下文

- `UserInput.user_feedback` 默认 `""`。
- `PriorRound` 保存 `schema_version`、`iteration`、反馈、Prompt 快照、运行结果和问题卡。
- 下一轮完整注入最近一轮 System/User Prompt、运行结果和问题卡；更早轮次直接显示为反馈摘要。
- 成功响应返回 `data.prompt_snapshot` 和 `data.round_snapshot`。
- 支持 `reset_history=True`。
- 空字段从上一轮问题卡继承，避免局部修订导致其他字段退化。
- `revision_notes`、`unaddressed_feedback` 和 `feedback_directives` 记录反馈处理结果。

### 状态恢复

- `state_store.py` 使用 SQLite，默认路径为 `.runtime/iteration_state.sqlite3`。
- `PROBLEM_UNDERSTANDING_STATE_DB` 可改写数据库位置。
- `RoundStateProvider` 抽象允许替换状态实现。
- `PROBLEM_UNDERSTANDING_STATE_MODE` 支持：
  - `sqlite`：显式历史优先，SQLite 自动恢复和保存；默认。
  - `explicit`：只使用输入中的历史，不读写 SQLite。
  - `off`：不自动持久化；显式输入仍可使用。
- 状态隔离键：`task_id + question_id + original_question SHA-256 + iteration`。
- 同一 iteration 用 upsert 覆盖；加载时只读取 `< 当前 iteration`，同轮重试不会读取自身。
- 单条损坏或旧版快照会被跳过，不阻断其他有效历史。
- SQLite 连接由 `contextmanager` 在 `finally` 中关闭。

### 严格输出校验和修复

- 模型返回非 object、空 object 或缺少下游必需字段时，不再直接归一化成“成功”。
- 默认最多执行 2 次输出修复重试；通过 `PROBLEM_UNDERSTANDING_OUTPUT_RETRIES` 配置。
- 重试 Prompt 包含具体校验错误，要求保留已有有效字段。
- LLM 异常返回 `LLM_CALL_FAILED`；重试耗尽的坏输出返回 `OUTPUT_VALIDATION_FAILED`。
- 非法 iteration/version 返回 `INVALID_ITERATION`，不会抛出未处理异常。
- 最终响应记录 `attempt_count` 和 `output_retry_count`。

### 确定性 self_review

新增 `review.py`，不再只信任 LLM 自报 `confidence`。评审维度：

- `field_completeness`
- `question_clarity`
- `original_intent_preservation`
- `searchability`
- `verifiability`
- `feedback_compliance`
- `revision_stability`
- `model_confidence`

默认阈值 0.75，可通过 `PROBLEM_UNDERSTANDING_REVIEW_THRESHOLD` 配置。旧 `run()` 响应也包含 `self_review`。

### 统一协议和兼容入口

- `ProblemUnderstandingRequest` 使用 `problem_understanding_input_v1`。
- 规范 stage 为总控登记的 `question_understanding`。
- `ProblemUnderstandingResponse` 使用 `metadata / payload / self_review`。
- `ProblemUnderstandingAdapter.build_request()` 使用团队现有 `_feedback` 字段，同时兼容 `feedback` 和历史拼写 `_fedback`。
- 新增 `ProblemUnderstandingAgent.run_protocol()`。
- 现有 backend 依赖的 `ProblemUnderstandingAgent.run()` 及 `status/error/meta/data` 信封仍保留，没有修改 backend。

### Prompt 预算

- `PROBLEM_UNDERSTANDING_MAX_PROMPT_CHARS` 默认 120000。
- System + User Prompt 超限时返回可恢复的 `PROMPT_TOO_LARGE`，不会静默截断。
- 响应记录 `prompt_size_chars` 和 `history_rounds_included`。
- 完整回灌上一轮 User Prompt 意味着其中可能继续包含更早 Prompt；当前只做硬上限保护，没有自动摘要。

## 关键文件

- `problem_understanding/schema.py`：问题卡、轮次快照、统一请求/响应和 self-review 模型。
- `problem_understanding/config.py`：集中配置及环境变量。
- `problem_understanding/prompts.py`：首轮、迭代轮和输出修复 Prompt。
- `problem_understanding/review.py`：确定性质量评分。
- `problem_understanding/agent.py`：兼容入口、协议入口、校验重试、状态恢复、评审和持久化。
- `problem_understanding/adapter.py`：团队统一总控适配器。
- `problem_understanding/state_store.py`：可替换状态协议和 SQLite 实现。
- `tests/test_iteration_state.py`：多轮、隔离、重试轮次和并发测试。
- `tests/test_protocol_and_quality.py`：协议、严格校验、LLM 异常、Prompt 上限和状态损坏测试。
- `README.md`：输入、迭代协议、统一协议和运行配置。

## 验证结果

系统 PATH 没有 Python。使用：

```powershell
$py = 'C:\Users\creat\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
Set-Location 'C:\Users\creat\Desktop\丁立中老师\2026挑战杯问题理解模块\项目\ai-scientist-cockpit\agents\problem_understanding'
& $py -m unittest discover -s tests -v
```

最近结果：16 项测试全部通过，约 0.5 秒。已验证：

- 首轮 Prompt 不变。
- 跨 Agent/进程式重建后 SQLite 恢复上下文。
- 下一轮包含所有要求的输入块。
- 同轮重试、任务隔离、显式历史优先、reset 和 8 线程并发。
- 统一适配器协议和失败协议。
- 非法 iteration 结构化失败。
- 空输出不再被伪装修复；校验错误可触发重试。
- LLM 异常可重试恢复。
- Prompt 超限不调用 LLM、不静默截断。
- SQLite 不可用时保留有效问题卡并产生 `STATE_*` 告警。
- 损坏快照被跳过。
- `explicit` 模式不访问状态提供器。

## 当前卡点及原因

没有已知模块内测试失败。

尚未验证：

- 真实总控两轮端到端运行；本任务禁止修改 backend。
- 真实 LLM 输出下 self-review 分数分布和重试频率；测试使用 Stub LLM，不访问网络。
- 默认 `.runtime` 在部署环境中的写权限。
- 多轮 Prompt 达到 120000 字符上限后的产品交互，目前只返回错误，没有摘要。

## 下一步计划（按优先级）

1. P0：在不修改 backend 的前提下运行真实总控两轮集成测试，检查 SQLite 恢复和 Prompt 内容。
2. P0：用 3～5 个真实问题执行两轮真实 LLM 小样本，校准 self-review 阈值和维度权重。
3. P1：确认部署状态目录；必要时设置 `PROBLEM_UNDERSTANDING_STATE_DB`。
4. P1：根据用户决定增加 Prompt 超限时的摘要或人工确认策略。
5. P1：根据长期任务量决定 SQLite TTL/清理策略。
6. P2：用户确认后提交当前未提交改动。

## 已踩过的坑，禁止重复

1. 不要恢复 `include_prior_prompt=False`；那会让上一轮 Prompt 实际不进入下一轮。
2. 不要只依赖总控显式回传历史；当前 backend 会新建 Agent，SQLite 是严格模块范围内的恢复兜底。
3. 不要用内存字典代替持久化，Agent 重建后会丢历史。
4. 不要递归保存完整响应信封；`run_result`、`prompt_snapshot`、`question_card` 必须分开。
5. 同轮重试必须查询 `iteration < 当前 iteration`。
6. 不要把 `{}` 或缺失关键字段的模型输出自动补成成功问题卡。
7. 不要只使用模型自报 confidence 作为质量评分。
8. `with sqlite3.connect()` 只管理事务，不保证关闭 Windows 文件句柄；使用 `closing` 或模块的 `_connect()`。
9. 当前环境使用工作区 Python，不要调用不存在的系统 `python`。
10. PowerShell here-string 管道可能破坏中文断言；使用 UTF-8 测试文件。
11. Git dubious ownership 用单次 `git -c safe.directory=...`，不要修改用户全局配置。
12. 不要修改 backend 或其他 Agent。

## 仍需用户决定的事项

- 是否允许运行真实总控两轮测试以及它产生的 artifact。
- 是否允许调用真实 LLM；会产生网络请求和 API 费用。
- 部署状态库继续放模块 `.runtime`，还是配置统一持久目录。
- Prompt 超限时直接失败、摘要旧历史还是请求人工选择。
- SQLite 是否需要自动清理及清理规则。
- 是否提交当前改动；目前没有 commit。
