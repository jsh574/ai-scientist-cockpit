# 第三轮大型优化计划

分支：`optimization/third-major-overhaul`

来源：用户提供的《AI Scientist Cockpit 项目当前 Bug、功能缺口与后续优化需求》

## 一、总体目标

第三轮优化不以新增 Agent 为主，而是优先打通现有系统的底层链路，让多 Agent 工作流真正可用、可见、可控、可恢复。

本轮优化的核心目标是稳定五条链路：

1. 用户文本和附件进入用户消息、Task Context、Controller、目标 Agent。
2. 知识整合的文献和证据中间结果可持续展示、持久化、恢复。
3. `knowledge_gaps` 可靠写入上下文并传给假设生成 Agent。
4. 审批事件只能被单次消费，工作流状态可靠推进。
5. 聊天区尊重用户阅读位置，不再被新内容强制拉到底部。

本轮不建议一口气改完所有内容。应按“先修状态一致性，再修科研能力，再修调试控制，最后升级多 Agent 架构”的顺序推进。

## 二、优先级总览

### P0：状态和数据一致性

必须最先完成，否则后续文件解析、多 Agent 协作、节点调试都会建立在不稳定状态上。

包括：

- 用户消息发送后文本必须进入聊天记录。
- 附件必须绑定到对应用户消息。
- 发送成功后待发送附件必须清空。
- 附件不能被错误附加到后续消息。
- 审批、继续、重跑按钮必须防重复点击。
- 同一个 approval 事件只能消费一次。
- 同一节点不能被重复运行或并发覆盖。
- 假设生成不能在 `knowledge_gaps` 未准备好时启动。

### P1：核心科研使用能力和前端体验

在 P0 稳定后推进，目的是让系统真正适合科研用户使用。

包括：

- PDF、DOCX、PPTX、XLSX 等文件上传和解析。
- 文件拖拽上传。
- 文件解析结果进入 Agent 上下文。
- 研究计划完整展示和通用 JSON 兜底渲染。
- 文献中间结果面板。
- 证据中间结果面板。
- 知识整合分阶段展示。
- 聊天滚动控制，支持类似 ChatGPT 的“回到最新输出”按钮。
- 中文 Mention 和 Agent 别名路由。
- ControllerConsole 和 NodeDebugger 接入入口。

### P2：工作流控制能力

在核心链路稳定后推进，提升可调试性和长任务可控性。

包括：

- 问题理解、假设生成、证据梳理拆成更细节点。
- 节点级取消、暂停、重试。
- 中间结果持久化和页面刷新恢复。
- 运行中指令的安全处理。
- 模型能力检测和 Provider Capability Matrix。
- 总控 Prompt 和上下文组装优化。

### P3：多智能体架构升级

最后推进，避免在底层链路不稳定时过早抽象。

包括：

- 统一 Agent Manifest。
- 消除重复 `AGENT_SPECS`、`stageMeta`、schema 定义。
- 动态 DAG。
- Agent-to-Agent Request。
- Agent-to-Agent Objection。
- 黑板式共享状态。
- Agent 并行执行。
- 能力驱动调度。
- 多 Agent 群聊界面。

## 三、分批实施计划

### Batch 0：基线核对和测试脚手架

目标：在开始大改前建立可重复验证的最小基线。

任务：

- 梳理当前前端消息对象、后端 Task Context、Artifact 的真实数据结构。
- 梳理当前审批 API、workflow run 状态机和按钮状态来源。
- 梳理当前附件上传 API、支持格式、持久化位置和前端状态。
- 增加最小回归测试或脚本：
  - 用户纯文本消息发送。
  - 用户文本加附件发送。
  - 审批按钮单次消费。
  - 知识整合输出 `literature_cards/evidence_cards/knowledge_gaps`。
  - 假设生成读取非空 `knowledge_gaps`。

验收：

- 能明确指出消息、附件、审批、知识整合、假设生成当前各自的数据入口和出口。
- 后续每个 Batch 都能用这些脚本做回归。

是否改产品行为：轻微。主要是测试和梳理。

### Batch 1：消息和附件绑定修复

目标：修复最底层的数据一致性问题。

任务：

- 发送消息时生成不可变消息快照：
  - `message_id`
  - `role`
  - `content`
  - `attachments`
  - `created_at`
- 附件从“输入框临时状态”移动到“消息对象持久字段”。
- 发送成功后清空输入框文本和待发送附件。
- 用户消息气泡中展示本次消息绑定的附件。
- 后端保存 `message_id -> file_id[] -> task_id` 的关系。
- Task Context 中记录附件引用，而不是只依赖当前输入框状态。
- 防止后续消息继承上一条消息附件。

验收：

- 单文件发送后，附件出现在对应用户消息气泡中。
- 多文件发送后，所有附件只属于当前消息。
- 发送后输入框附件列表清空。
- 刷新页面后，用户消息和附件关系仍存在。
- 新建任务不会继承旧任务附件。

风险：

- 如果当前前端消息模型和后端持久化模型差异较大，需要先做兼容字段，不应一次性迁移全部历史结构。

### Batch 2：审批事件幂等和按钮状态修复

目标：保证审批、继续、重跑按钮不能重复消费同一事件。

任务：

- 为每个审批提示生成唯一 `approval_id` 或复用已有 run review id。
- 审批 API 后端做原子状态更新：
  - pending -> approved
  - pending -> retry
  - pending -> rejected
- 非 pending 状态再次点击时返回已处理结果，不推进工作流。
- 前端点击后立即禁用按钮并显示处理中。
- 成功后移除或置灰审批提示。
- workflow run 状态推进必须只发生一次。

验收：

- 连续双击“批准继续”只推进一次。
- 连续点击“继续执行”不会创建重复 run。
- 连续点击“重新执行”不会重复启动同一节点。
- 页面刷新后已处理审批不再显示为可操作。
- 并发请求下后端状态仍只变更一次。

风险：

- 如果当前审批提示没有稳定 ID，需要补一层兼容映射。

### Batch 3：Knowledge Gap 流转和知识整合阶段拆分展示

目标：让知识整合的三个阶段可见，并保证 `knowledge_gaps` 可靠传给假设生成。

任务：

- 将知识整合进度统一为三个标准节点：
  - `literature_search`
  - `evidence_integration`
  - `knowledge_gap_synthesis`
- 第一阶段完成后立即持久化并展示 `literature_cards`。
- 第二阶段完成后立即持久化并展示 `evidence_cards`。
- 第三阶段必须执行并生成 `knowledge_gaps`，但不一定单独做强展示面板。
- `knowledge_gaps` 必须写入 Task Context 和 Artifact。
- 假设生成启动前检查 `knowledge_gaps` 非空。
- 重新运行知识整合时清理旧 `knowledge_gaps`，避免假设生成使用旧 Gap。
- 假设生成的每个 hypothesis 至少引用一个合法 `gap_id` 和一个合法 `evidence_id`。

验收：

- 文献检索完成后，前端立即看到文献卡片。
- 证据提取完成后，前端立即看到证据卡片。
- 页面刷新后文献和证据仍存在。
- `knowledge_gaps` 为空时阻止假设生成并提示用户重跑知识整合。
- 假设生成结果中的 `related_gap_ids` 均能在当前上下文找到。
- 重新运行知识整合后，假设生成使用最新 Gap。

风险：

- 这部分和上一轮已修复的知识整合 `partial_output`、checkpoint 机制有关，需要在现有基础上增量演进，不要重复造一套事件协议。

### Batch 4：研究计划展示和通用 JSON 渲染兜底

目标：避免后端生成完整研究计划，但前端只显示一部分。

任务：

- 梳理研究计划 Agent 输出 schema 和前端展示字段。
- 设计研究计划专用展示区域：
  - 研究目标
  - 核心科学问题
  - 候选假设
  - 技术路线
  - 方法和模型
  - 数据集和数据来源
  - 实验设计
  - 评价指标
  - 对照实验
  - 时间安排
  - 里程碑
  - 风险和应对
  - 参考文献和证据来源
- 增加通用递归 JSON 渲染器：
  - 对象展开
  - 数组展开
  - 长文本折叠
  - JSON 路径显示
  - 一键复制
  - 原始 JSON 查看
  - 未识别字段提示
- 未被专用组件消费的字段必须出现在兜底区。

验收：

- 后端 JSON 中所有有效字段都能在页面找到。
- 嵌套对象和数组可展开。
- 长文本不会无提示截断。
- 新增字段即使没有专用 UI，也会通过通用组件显示。
- 页面刷新后研究计划完整恢复。

风险：

- 通用 JSON 渲染器需要控制视觉密度，不能让页面重新变成原始 JSON 堆叠。

### Batch 5：文件上传格式扩展和解析架构

目标：从“允许上传后缀”升级为“文件存储、解析、切分、检索、引用”的完整链路。

任务：

- 第一阶段支持：
  - `.pdf`
  - `.docx`
  - `.pptx`
  - `.xlsx`
  - `.txt`
  - `.md`
  - `.csv`
  - `.json`
- 对旧格式 `.doc/.ppt/.xls` 给出明确提示或转换策略。
- 建立文件元数据：
  - `file_id`
  - `file_name`
  - `file_type`
  - `mime_type`
  - `size`
  - `hash`
  - `task_id`
  - `message_id`
  - `upload_status`
  - `parse_status`
  - `error`
- 建立标准化解析结果：
  - `metadata`
  - `sections`
  - `pages`
  - `tables`
  - `images`
  - `chunks`
- 长文档按章节、页码或语义切分。
- Agent 不接收所有文件全文，而是由 Controller 按职责检索相关片段。
- Agent 输出保留 `file_id`、页码、段落来源。

验收：

- PDF、DOCX、PPTX、XLSX 至少能上传、解析状态可见、基础文本可进入上下文。
- CSV 和 JSON 保留结构关系，不被粗暴转成纯文本。
- 文件解析失败时有明确提示。
- Agent 能引用文件名和页码回答。
- 删除附件后 Agent 不再访问该文件。

风险：

- PDF/OCR、Office 解析依赖较多，建议先做纯文本解析和元数据链路，再做 OCR 和复杂表格。

### Batch 6：拖拽上传和上传体验

目标：提升输入框文件交互体验。

任务：

- 支持单文件和多文件拖拽上传。
- 拖入输入区时高亮。
- 文件类型和大小校验。
- 上传进度显示。
- 上传失败重试。
- 重复文件提示。
- 文件移除。
- 上传完成和解析状态区分显示。
- 无障碍提示。

验收：

- 拖拽文件到输入框即可加入待发送附件。
- 多文件拖拽顺序和状态稳定。
- 不支持格式给出明确原因。
- 上传失败可重试。
- 发送后待发送附件清空。

风险：

- 必须复用 Batch 1 的消息附件绑定模型，不能再引入另一套附件临时状态。

### Batch 7：聊天滚动控制修复

目标：修复新内容强制滚到底部的问题。

任务：

- 建立 `ChatScrollState`：
  - `isNearBottom`
  - `autoFollowEnabled`
  - `hasUnreadOutput`
  - `isAgentStreaming`
- 只有用户接近底部时自动跟随新输出。
- 用户主动向上滚动后停止自动跟随。
- Agent 输出中，用户不在底部时显示三个波动点。
- Agent 输出结束后，按钮切换为向下箭头。
- 点击按钮后平滑滚动到底部并恢复自动跟随。
- 所有新内容共用同一套滚动策略：
  - Agent 消息
  - 文献卡片
  - 证据卡片
  - workflow 活动
  - Review Gate
  - 审批提示
  - 错误和重试提示

验收：

- 用户在底部时新输出自动跟随。
- 用户向上滚动后不再被新 token 拉到底部。
- 多 Agent 连续输出不覆盖用户阅读位置。
- 文献卡片、证据卡片、审批提示出现时不强制滚动。
- 点击悬浮按钮后恢复到最新输出。

风险：

- 需要找出所有当前调用 `scrollIntoView()` 或依赖消息变化强制滚动的地方，统一收口。

### Batch 8：Mention、总控答疑和 Agent 群聊入口

目标：让用户能更自然地调度目标 Agent，并为后续多 Agent 协作升级铺路。

任务：

- 建立统一 Mention 别名表：
  - 英文 Agent 名
  - 中文 Agent 名
  - 简称
  - 角色别名
- 支持中文 Mention。
- Mention 解析结果进入 Controller route intent。
- 总控能解释当前可用 Agent、输入要求和输出能力。
- Mention 到某 Agent 时，明确是：
  - 查询该 Agent 状态
  - 请求该 Agent 执行
  - 要求该 Agent 修改上一轮输出
  - 向该 Agent 提问
- 为后续 Agent-to-Agent Request 保留消息类型。

验收：

- `@知识整合`、`@假设生成` 等中文 Mention 能正确识别。
- 无歧义 Mention 直接路由到目标 Agent 或显示确认。
- 总控回答“我现在能让哪些 Agent 做什么”。
- Mention 不会绕过审批模式和工作流状态约束。

风险：

- 不应把 Mention 做成简单字符串匹配后直接运行 Agent，必须经过 Controller 和权限/状态检查。

### Batch 9：NodeDebugger 和 ControllerConsole 接入

目标：让用户能看到每个节点真实输入、输出、状态和重跑入口。

任务：

- 找到现有 NodeDebugger/ControllerConsole 组件或接口。
- 在前端合适位置接入：
  - 工作流侧栏
  - 节点详情抽屉
  - Agent 输出卡片调试入口
- NodeDebugger 支持：
  - 节点输入
  - 节点输出
  - progress events
  - partial outputs
  - error
  - retry
  - duration
  - model policy
- ControllerConsole 支持：
  - 路由决策
  - ReviewGate 决策
  - agents_to_rerun
  - 总控建议

验收：

- 用户能查看某个节点真实输入和输出。
- 用户能看到总控为什么进入审批或重试。
- 节点重跑不会破坏其他节点产物。

风险：

- 需要严格区分普通用户视图和调试视图，避免主界面过载。

### Batch 10：协议单一源和多 Agent 架构升级预研

目标：减少重复配置，为动态 DAG 和能力调度做准备。

任务：

- 盘点重复协议：
  - `AGENT_SPECS`
  - `stageMeta`
  - schema JSON
  - TypeScript types
  - Adapter writes/reads
- 设计 Agent Manifest 草案：
  - `agent_id`
  - `display_name`
  - `aliases`
  - `reads`
  - `writes`
  - `required_inputs`
  - `optional_inputs`
  - `progress_nodes`
  - `model_requirements`
  - `approval_policy`
- 先生成文档和校验脚本，不急于迁移运行时代码。

验收：

- 能从单一 manifest 推导前端 stage 信息和后端 AgentSpec。
- 新增字段时至少有校验提示，不再静默丢字段。

风险：

- 这是架构升级，不应和 P0/P1 修 bug 混在同一批大改里。

## 四、建议执行顺序

推荐按以下顺序执行，每个 Batch 单独提交、单独验证：

1. Batch 0：基线核对和测试脚手架。
2. Batch 1：消息和附件绑定修复。
3. Batch 2：审批事件幂等和按钮状态修复。
4. Batch 3：Knowledge Gap 流转和知识整合阶段展示。
5. Batch 7：聊天滚动控制修复。
6. Batch 4：研究计划展示和通用 JSON 渲染。
7. Batch 5：文件格式扩展和解析架构。
8. Batch 6：拖拽上传和上传体验。
9. Batch 8：Mention、总控答疑和 Agent 群聊入口。
10. Batch 9：NodeDebugger 和 ControllerConsole 接入。
11. Batch 10：协议单一源和多 Agent 架构升级预研。

说明：

- Batch 7 提前到 Batch 4 之前，是因为聊天强制滚动会影响所有长内容阅读，包括文献、证据和研究计划。
- Batch 5/6 放在较后，是因为文件支持必须建立在消息附件绑定稳定之后。
- P3 架构升级最后做，避免在基础链路未稳定时扩大复杂度。

## 五、本轮不建议立即做的事

- 不建议马上做动态 DAG。
- 不建议马上做 Agent 并行执行。
- 不建议马上做完整 Agent-to-Agent 协议。
- 不建议为了支持文件格式只扩展前端 `accept` 后缀。
- 不建议把所有文件全文塞给所有 Agent。
- 不建议在每个新 token 到达时无条件 `scrollIntoView()`。
- 不建议先改 UI 文案掩盖审批按钮重复消费问题。

## 六、第一批建议启动内容

如果确认开始实施，建议第一批只做以下三件事：

1. Batch 0 的基线核对和最小回归脚本。
2. Batch 1 的消息和附件绑定。
3. Batch 2 的审批幂等。

理由：

- 这三项是 P0，直接影响数据可靠性。
- 不依赖复杂文件解析能力。
- 能为后续知识整合、研究计划展示和调试工具提供稳定基础。

第一批完成后，再进入：

1. Knowledge Gap 流转和知识整合分阶段展示。
2. 聊天滚动控制。
3. 研究计划展示。

## 七、全局验收标准

本轮优化完成后，应满足：

- 用户发送文本和附件后，消息记录、Task Context、Controller、Agent 看到的是同一份绑定关系。
- 审批、继续、重跑操作具备幂等性，不会重复推进工作流。
- 文献、证据、Knowledge Gap 能分阶段产出、持久化、刷新恢复。
- 假设生成只在 `knowledge_gaps` 有效时启动。
- 研究计划 JSON 的有效字段不会被前端静默丢弃。
- 文件上传不只是允许后缀，而是有存储、解析、切分、检索、引用链路。
- 用户向上滚动阅读时，新内容不会强制拉到底部。
- 用户能通过 Mention 和总控明确调度 Agent，但所有执行仍受工作流状态和审批模式约束。
- 节点调试信息可以被查看，便于解释总控和 Agent 的决策。

## 八、实施记录

### 2026-07-22：第一批 P0 链路修复

本次优先落地 Batch 0、Batch 1、Batch 2 中不会破坏现有总控协议的部分，目标是先把“用户消息/附件/审批”三条基础状态链路稳定住。

#### 1. 消息和附件绑定

改动内容：

- 前端发送初始问题或项目反馈时，先生成稳定的 `message_id`，并把本次选择的文件快照绑定到该用户消息。
- 用户消息气泡现在展示本消息自己的附件 chip，包括上传中、解析中、失败、完成等状态。
- 后端附件上传接口新增 `message_id` 表单字段。
- `ArtifactService.add_attachment()` 持久化 `message_id`、`upload_status`、`parse_status`。
- Task Context 中继续保留全局 `user_input.attachments`，同时新增 `extensions.message_attachments[message_id]`，用于恢复“某条消息对应哪些附件”的关系。
- 附件上传事件 `attachment_uploaded` 增加 `message_id`，便于后续 ControllerConsole / NodeDebugger 追踪。

实现方式：

- 前端通过 `pendingFileAttachments(files, messageId)` 创建临时附件占位，上传成功后用后端返回的真实附件替换同一条用户消息上的附件列表。
- 上传失败时，不再让附件静默消失，而是把该消息上的附件标记为 `failed`。
- 上传成功后清空输入框待发送附件，避免下一条消息继承上一条消息文件。
- 后端保留原有 `attachments/index.json` 和 `question_description` 注入方式，避免一次性迁移历史上下文结构。

改后效果：

- 单条消息、多文件上传时，附件只归属当前消息。
- 后续用户反馈不会错误继承上一条消息的附件。
- 后端上下文和事件流可以追踪附件来自哪条用户消息。
- 页面刷新后的完整消息恢复还需要后续基于 `extensions.message_attachments` 接入消息历史恢复逻辑，本批先完成后端持久化基础。

#### 2. 审批事件幂等和按钮防重复

改动内容：

- `HumanReviewRequest` 新增 `approval_id`。
- 前端对每条审批/重跑消息引入消息级 busy 状态，按钮点击后立即禁用。
- 前端审批请求使用 `task_id + stage + message_id + action` 生成稳定 `approval_id`。
- 后端在 `context.extensions.processed_approvals` 记录已处理审批。
- 重复提交同一个 `approval_id` 时，后端直接返回已处理结果，并标记 `idempotent: true`，不再二次推进工作流。
- accept、retry、rollback 三类审批分支都会记录处理结果。

实现方式：

- `Orchestrator.submit_review()` 在进入状态判断前先检查 `processed_approvals`。
- 首次审批完成后通过 `_remember_approval()` 写回上下文。
- 前端 `beginMessageAction()` / `finishMessageAction()` 用 `Set` 管理正在处理的消息操作，覆盖成功、失败和提前返回路径。

改后效果：

- 用户连续双击“批准继续”不会重复写入 review、version 或 event。
- 质量门禁的“继续执行”和“重新执行”按钮在请求未完成时不可再次点击。
- 后端对重复请求有兜底保护，不只依赖前端按钮禁用。

#### 3. 回归验证

新增/强化测试：

- `backend/tests/test_persistence.py`
  - 验证附件 `message_id` 被写入附件索引。
  - 验证 `user_input.attachments` 保留附件引用但不暴露 `text_excerpt`。
  - 验证 `extensions.message_attachments[message_id]` 能找到对应附件。
- `backend/tests/test_orchestrator.py`
  - 新增重复人工审批幂等测试。
  - 验证第二次同 `approval_id` 审批不会新增 review、version、event。

已执行验证：

- `npm run typecheck`
- `python -m unittest discover -s backend/tests -v`
- `python -m py_compile backend/app/contracts.py backend/app/artifact_service.py backend/app/main.py backend/app/orchestrator.py`

验证结果：

- TypeScript 类型检查通过。

### 2026-07-22：文件上传格式扩展和基础解析架构

本次推进 Batch 5 的第一阶段，先把“更多文件格式可上传、可解析、可进入 Task Context”的基础链路打通。复杂 OCR、图片抽取、表格结构化检索后续再做。

改动内容：

- 后端附件支持格式从 `.txt/.md/.csv/.json` 扩展为：
  - `.txt`
  - `.md`
  - `.csv`
  - `.json`
  - `.pdf`
  - `.docx`
  - `.pptx`
  - `.xlsx`
- `.doc/.ppt/.xls` 旧 Office 格式仍不支持，并返回明确转换提示。
- 附件元数据新增：
  - `file_id`
  - `file_type`
  - `hash`
  - `parsed_path`
  - `parse_error`
  - `chunk_count`
- 每个附件会保存原始文件和解析结果：
  - 原始文件：`attachments/{attachment_id}_{filename}`
  - 解析结果：`attachments/parsed/{attachment_id}.parsed.json`
- 解析结果标准化为：
  - `metadata`
  - `sections`
  - `pages`
  - `tables`
  - `images`
  - `chunks`
- DOCX/PPTX/XLSX 采用 zip XML 基础文本抽取。
- PDF 优先尝试 `pypdf`，不可用时使用保守字节文本回退。
- `/api/health` 的 `attachments.allowed_extensions` 改为来自后端统一列表。
- 前端文件选择 `accept` 和 fallback allowed extensions 同步扩展。
- 前端附件 chip 和系统页附件列表显示解析状态、文件类型和 chunk 数。

实现方式：

- 在 `ArtifactService` 中新增附件解析辅助方法。
- 上传时先解析，再写入原文件和 parsed JSON。
- Task Context 继续注入解析后的文本摘要，保证 Agent 仍能通过 `user_input.question_description` 读取附件背景。
- `user_input.attachments` 不暴露 `text_excerpt`，只保留文件引用和解析元数据。
- 保留原有附件索引 `attachments/index.json`，避免迁移历史任务结构。

改后效果：

- 用户可以上传 PDF、DOCX、PPTX、XLSX 等科研常见文件。
- Agent 上下文可以拿到这些文件的基础文本内容。
- 系统能记录文件 hash、解析状态、解析结果路径和 chunk 数，为后续检索/引用链路做准备。
- 解析失败时有 `parse_status` 和 `parse_error`，不会只显示“上传失败”这一种笼统状态。

已执行验证：

- `test_docx_attachment_is_parsed_and_contextualized`
  - 验证 DOCX 能解析文本。
  - 验证 parsed JSON 被写入。
  - 验证解析文本进入 Task Context。
  - 验证附件元数据包含 `file_type/hash/chunk_count/parsed_path`。
- `python -m unittest discover -s backend/tests -v`
- `python -m py_compile backend/app/artifact_service.py backend/app/main.py`
- `npm run typecheck`

验证结果：

- 后端 unittest 48 个全部通过。
- Python 编译检查通过。
- TypeScript 类型检查通过。

### 2026-07-22：拖拽上传和待发送附件体验

本次推进 Batch 6 的第一阶段，重点修复输入框文件交互不够自然、待发送文件不可单独管理的问题。

改动内容：

- Composer 支持文件拖拽进入和拖拽释放。
- 拖拽文件悬停时显示高亮态和 drop 提示。
- 点击添加文件和拖拽添加文件共用同一套校验逻辑。
- 支持多文件去重，避免同一文件重复加入待发送列表。
- 待发送附件在输入框下方以 chip 形式展示。
- 每个待发送附件可单独移除。
- 支持一键清空待发送附件。
- 文件类型和大小校验继续读取后端 `/api/health` 返回的 `allowed_extensions/max_bytes`，离线 fallback 同步包含 PDF/Office 新格式。

实现方式：

- 新增 `composerDragActive` 状态。
- 新增 `addPendingFiles()`，集中处理类型校验、大小校验、去重和错误提示。
- 新增 `removePendingFile()`，用于单个文件移除。
- Composer 根节点增加 `dragenter/dragover/dragleave/drop` 事件。
- 原文件 input 的 `onChange` 改为调用 `addPendingFiles()`。
- 新增 `.pending-file-list` 和 `.composer.drag-active` 样式。

改后效果：

- 用户可以直接把 PDF/DOCX/PPTX/XLSX 等文件拖到输入框加入待发送附件。
- 发送前能看清楚哪些文件会随本条消息提交。
- 添加错文件时可以单独移除，不需要清空整个输入。
- 文件校验逻辑不会因为点击上传和拖拽上传两套入口而分叉。

已执行验证：

- `npm run typecheck`

验证结果：

- TypeScript 类型检查通过。

### 2026-07-22：聊天滚动控制修复

本次推进 Batch 7，修复新消息和 Agent 输出到达时无条件把用户拉到底部的问题。

改动内容：

- 新增 `ChatScrollState`：
  - `isNearBottom`
  - `autoFollowEnabled`
  - `hasUnreadOutput`
  - `isAgentStreaming`
- 将原来的 `messages/running` 变化后无条件 `scrollIntoView()` 改为条件跟随。
- 用户接近底部时继续自动跟随新输出。
- 用户主动向上滚动后停止自动跟随。
- 新输出到来但用户不在底部时，只显示“新输出 / New output”按钮。
- 用户点击按钮后平滑回到底部，并恢复自动跟随。
- 用户手动滚回底部后，自动清除未读状态。
- 新增底部 sticky 按钮样式，运行中显示小圆点，提示仍有 Agent 输出在生成。

实现方式：

- 新增 `threadAreaRef` 指向真实滚动容器。
- 新增 `syncChatScrollPosition()` 计算距离底部是否小于 96px。
- 新增 `scrollToLatest()` 作为所有“回到最新输出”的唯一入口。
- 保留原有 `threadEndRef`，但只在允许自动跟随或用户点击按钮时使用。
- `updateChatScrollState()` 内部做状态比较，避免滚动事件造成重复渲染。

改后效果：

- 用户在阅读历史 Agent 输出、文献卡片、证据卡片、研究计划时，不会被新消息强制拉到底部。
- 仍在底部等待输出的用户，体验保持原来的自动跟随。
- 多 Agent 连续输出时，用户可以通过悬浮按钮明确回到最新内容。

已执行验证：

- `npm run typecheck`

验证结果：

- TypeScript 类型检查通过。

### 2026-07-22：研究计划完整展示与通用 JSON 兜底

本次推进 Batch 4，解决研究计划 Agent 已经返回较完整 JSON，但前端只消费部分字段的问题。

改动内容：

- 强化 `normalizeResearchPlan()`，补全以下字段默认值：
  - `technical_details`
  - `datasets.target`
  - `rationale.text`
  - `rationale.logic_chain.source_ids`
  - `references.used_for`
  - `feedback_tasks.input_requirements`
- 研究计划专用展示新增：
  - 科学依据
  - 技术路线
  - 统计检验和软件栈
  - 目标数据集
  - 实验流程
  - 证据逻辑链
  - 参考文献用途
  - 反馈任务详情
- 新增通用递归 `JsonTree` 渲染器。
- 研究计划卡片底部新增“完整 JSON 兜底”，可展开查看原始 plan。
- 原有 JSON Modal 从纯 `<pre>` 改为结构化树形展示，并保留 Raw JSON 折叠区。

实现方式：

- 专用 UI 继续优先展示高价值字段，避免用户只看到原始 JSON。
- `JsonTree` 对数组和对象默认展开前两层，深层按需展开。
- JSON 树和 Raw JSON 共用紧凑样式，避免长字段撑破弹窗或消息气泡。
- 不改后端 research plan schema，不影响 Agent 输出协议。

改后效果：

- 研究计划中的关键字段不再因为前端没有专用组件而静默丢失。
- 新增字段即使没有专用 UI，也能通过通用 JSON 兜底被用户找到。
- JSON Modal 对所有阶段输出都更容易阅读和定位字段路径。

已执行验证：

- `npm run typecheck`

验证结果：

- TypeScript 类型检查通过。

### 2026-07-22：聊天滚动控制修复

本次推进 Batch 7，修复新消息和 Agent 输出到达时无条件把用户拉到底部的问题。

改动内容：

- 新增 `ChatScrollState`：
  - `isNearBottom`
  - `autoFollowEnabled`
  - `hasUnreadOutput`
  - `isAgentStreaming`
- 将原来的 `messages/running` 变化后无条件 `scrollIntoView()` 改为条件跟随：
  - 用户接近底部时，继续自动跟随新输出。
  - 用户主动向上滚动后，停止自动跟随。
  - 新输出到来但用户不在底部时，只显示“新输出 / New output”按钮。
  - 用户点击按钮后平滑回到底部，并恢复自动跟随。
  - 用户手动滚回底部后，自动清除未读状态。
- 对话区域增加滚动监听，统一维护当前滚动状态。
- 新增底部 sticky 按钮样式，运行中显示小圆点，提示仍有 Agent 输出在生成。

实现方式：

- 新增 `threadAreaRef` 指向真实滚动容器。
- 新增 `syncChatScrollPosition()` 计算距离底部是否小于 96px。
- 新增 `scrollToLatest()` 作为所有“回到最新输出”的唯一入口。
- 保留原有 `threadEndRef`，但只在允许自动跟随或用户点击按钮时使用。
- `updateChatScrollState()` 内部做状态比较，避免滚动事件造成重复渲染。

改后效果：

- 用户在阅读历史 Agent 输出、文献卡片、证据卡片、研究计划时，不会被新消息强制拉到底部。
- 仍在底部等待输出的用户，体验保持原来的自动跟随。
- 多 Agent 连续输出时，用户可以通过悬浮按钮明确回到最新内容。

已执行验证：

- `npm run typecheck`

验证结果：

- TypeScript 类型检查通过。
- 后端 unittest 从 44 个增加到 45 个，全部通过。
- Python 编译检查通过。

#### 4. 本批仍未完成的 P0/P1 内容

- 知识整合三阶段展示尚未改造。
- 聊天滚动控制尚未改造。
- 研究计划完整展示和通用 JSON 渲染兜底尚未实现。
- PDF/DOCX/PPTX/XLSX 等复杂文件解析链路尚未实现。
- ControllerConsole / NodeDebugger 入口尚未接入。

### 2026-07-22：Knowledge Gap 流转的后端门禁

本次先落地 Batch 3 中最关键的后端一致性约束，暂不改造知识整合三阶段 UI。

改动内容：

- `Orchestrator.run_stage()` 在执行 `hypothesis_generation` 前检查上游上下文。
- 当 `knowledge_gaps` 缺失、为空或没有有效 `gap_id` 时，阻止 Hypothesis Agent 启动。
- 当 `evidence_cards` 缺失、为空或没有有效 `evidence_id` 时，同样阻止 Hypothesis Agent 启动。
- 前置检查失败时不抛出系统异常，而是生成结构化 `partial_success` 响应，交给 ReviewGate 形成 `retry`，使 workflow run 保持可恢复。
- 新增 `stage_preflight_blocked` 事件，后续可被 ControllerConsole / NodeDebugger 用来解释“为什么没有调用目标 Agent”。
- ReviewGate 强化假设生成校验：
  - 每条 hypothesis 必须引用至少一个 `evidence_id`。
  - 每条 hypothesis 必须引用至少一个 `gap_id`。
  - `based_on_evidence_ids` 必须存在于当前 `evidence_cards`。
  - `related_gap_ids` 必须存在于当前 `knowledge_gaps`。
  - Agent 自评里的 issues 会进入系统 review issues，避免前端只看到笼统的“自评未通过”。

实现方式：

- 新增 `_stage_preflight_issues()`，集中定义阶段启动前置条件。
- 新增 `_preflight_response()`，用统一 AgentResponse 结构表达“未满足前置条件”。
- 保留原有 `run_stage()` 输入、输出、review、node run、event 持久化路径，避免给前端和 workflow run 增加额外状态分支。
- ReviewGate 在 `hypothesis_generation` 的 traceability 校验中同时检查 evidence 和 gap 两类引用。

改后效果：

- `knowledge_gaps` 没准备好时，假设生成不会空跑，也不会产出与知识空白无关的 hypothesis。
- 旧数据或异常恢复场景下，即使 `current_stage` 已经来到 `hypothesis_generation`，后端仍能拦住错误执行。
- Agent 编造不存在的 `gap_id` 会被判定为 `retry`，不会写入通过态上下文。

新增验证：

- `test_hypothesis_generation_is_blocked_without_knowledge_gaps`
  - 验证缺少 `knowledge_gaps` 时 Hypothesis Agent 不会被调用。
  - 验证会写入 `stage_preflight_blocked` 事件。
- `test_hypothesis_review_requires_known_gap_references`
  - 验证假设引用不存在的 `gap_id` 时 ReviewGate 返回 `retry`。

### 2026-07-22：知识整合结果的阶段化展示

本次继续推进 Batch 3 的前端可见性部分。后端暂不拆分 `knowledge_integration` 运行节点，而是在现有输出 schema 上增强展示，让用户能直接看出知识整合内部的三段产物是否齐备。

改动内容：

- 新增 `KnowledgeIntegrationOutput` 前端展示组件。
- 将原本平铺的知识整合结果改为三段：
  - Literature Search / 文献检索
  - Evidence Integration / 证据整合
  - Knowledge Gap Synthesis / 知识空白合成
- 三段顶部增加状态条，显示每段产物数量。
- 文献卡片展示标题、年份、来源、摘要/总结预览、DOI/URL/文献 ID。
- 证据卡片展示 `evidence_id`、`source_literature_id`、claim 和证据类型/强度。
- Knowledge Gap 展示 `gap_id`、description、research opportunity / why it matters。
- 当文献、证据或 gap 为空时，显示明确空态；其中 gap 为空会提示假设生成会被后端门禁拦截。
- 增加移动端单列布局，避免卡片和阶段条在窄屏挤压。

实现方式：

- 继续消费现有 `payload.literature_cards`、`payload.evidence_cards`、`payload.knowledge_gaps`，不改 Agent 输出协议。
- 复用现有 `arrayValue`、`objectField`、`trimPreview` 辅助函数，避免为展示层引入新的解析规则。
- 通过 CSS 的固定网格和 `minmax(0, 1fr)` 控制长标题、DOI、URL 的溢出，避免撑破消息气泡。

改后效果：

- 用户能在单个知识整合输出中看到“文献 -> 证据 -> 知识空白”的内部链路。
- 当知识整合 Agent 没有生成 gap 时，前端会提前暴露问题，不再等到假设生成阶段才让用户困惑。
- 后续若把知识整合拆成真实三个子节点，该展示组件可以继续作为三个子节点输出汇总视图使用。

已执行验证：

- `npm run typecheck`

验证结果：

- TypeScript 类型检查通过。
