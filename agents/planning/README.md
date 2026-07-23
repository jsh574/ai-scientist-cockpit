# Research Planning Agent

这是模块 5“研究计划输出 Agent”的初版可运行实现。

该 Agent 接收 `docs/数据规范_v0.1.md` 中定义的模块 5 输入，派生内部假设证据包。本地封装层负责选择多个 hypothesis、逐个调用 Dify Workflow，并把每次返回的单个 `plan_result` 聚合为统一模块响应中的 `plans[]`：

```text
metadata + payload(plans[]) + self_review
```

当前版本没有本地研究计划生成兜底逻辑。如果 Dify 未配置或调用失败，封装层会直接返回 `failed` 或把失败的 hypothesis 标记为单条 failed plan。

## 依赖与虚拟环境

运行时代码只使用 Python 标准库；开发和测试需要 `pytest`。

首次进入项目后创建并安装开发依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

后续命令建议都使用 venv 中的 Python：

```powershell
.\.venv\Scripts\python -m pytest
```

## 环境变量

脚本会默认读取项目根目录的 `.env`，该文件已被 `.gitignore` 忽略。你只需要在 `.env` 中填写自己的 Dify API Key：

```env
export DIFY_API_URL="http://115.190.208.240:31880"
export DIFY_API_KEY="<workflow-app-api-key>"
export DIFY_USER="research-planning-agent"
export DIFY_RESPONSE_MODE="streaming"
export DIFY_TIMEOUT_SECONDS="300"
export DIFY_SHOW_PROGRESS="1"
export DIFY_MAX_PARALLEL_CALLS="1"
```

PowerShell 里临时设置的环境变量优先级更高，会覆盖 `.env`。测试或隔离运行时可设置：

```powershell
$env:PLANNING_AGENT_SKIP_DOTENV = "1"
```

总控启动时也会加载 `agents/planning/.env`。环境变量优先级为：进程环境变量、
总仓库根目录 `.env`、`agents/planning/.env`、`backend/.env`；先出现的值不会
被后续文件覆盖。Planning 专用的 A/B/C 凭据因此可以只维护在本目录 `.env` 中。

## 运行测试

```powershell
.\.venv\Scripts\python -m pytest
```

如果暂时不使用 venv，也可以在已安装 pytest 的环境中运行：

```powershell
python -m pytest
```

## 仓库协作约定

Planning Agent 现已内置在总控仓库 `agents/planning`，后续功能开发、测试和
Dify DSL 维护都以总控仓库中的这份代码为准。执行前先同步总控当前功能分支，
修改完成并通过 Planning 独立测试和总控集成测试后再提交。不要直接向 `main`
推送，使用 `STAR/` 前缀的功能分支。

提交前请确认 `.env`、`.venv/`、`.tmp/`、CLI 输出、Dify 网页调试导出、`samples/output/planning_response*.json`、`samples/test-artifacts/` 等本地文件没有被 staged。Windows/Linux 换行由 `.gitattributes` 统一处理，看到 “CRLF will be replaced by LF” 是预期行为。

## 运行 CLI

确认当前脚本将访问的 Dify API：

```powershell
.\.venv\Scripts\python -m planning_agent.cli --print-dify-target
```

运行短样例，适合日常测试，token 消耗较少：

```powershell
.\.venv\Scripts\python -m planning_agent.cli --sample --show-progress
```

保留完整规格样例用于回归测试：`--full-sample` 或 `--input samples/input/module5_input_sample.json`。短样例文件写在 `samples/input/module5_input_short.json`，方便直接传给 `--input`。默认 response 会写入 `samples/output/planning_responseMM_DD-HH_MM.json`；该输出文件被忽略，不要提交。`DIFY_MAX_PARALLEL_CALLS=1` 默认为串行；设为 `2` 时两个 hypothesis 会并行请求 Dify Workflow。未配置 Dify 时，CLI 会输出 `failed` 响应并以非零退出码结束。

测试多个上游假设分别完整通过 A/B/C：

```powershell
.\.venv\Scripts\python -m planning_agent.workflow_chain_cli `
  --input samples\input\module5_input_rag_batch.json `
  --all-hypotheses `
  --max-parallel-hypotheses 3 `
  --output samples\test-artifacts\rag-batch-abc.json `
  --html samples\test-artifacts\rag-batch-abc.html
```

该命令与 `planning_agent.cli` 的 C-only 批量测试不同：每个假设都会独立执行 A 三候选、B 评审和 C 最终计划。

## 样例与测试产物目录

- `samples/input/`：可提交的测试输入，包含 short、full 和 3 假设 RAG batch 模块 5 输入。
- `samples/output/`：CLI/API 测试 response 输出目录，默认命名为 `planning_responseMM_DD-HH_MM.json`，response 文件由 `.gitignore` 忽略。
- `samples/test-artifacts/`：自动化测试中间文件目录，整体忽略。测试不要向 `samples/` 根目录写临时文件。

## 关于 Streaming

`DIFY_RESPONSE_MODE=streaming` 表示本地客户端用 Dify Workflow 的 SSE 流式接口接收事件。它可以让终端看到 `workflow_started`、`node_started`、`workflow_finished` 等事件，并降低长时间 blocking 请求超时的概率。

但它不保证 LLM 结构化输出会逐 token 打到终端。当前 Workflow 的 End 节点仍然要等 LLM 节点完成后才有最终 `plan_result`，所以如果模型节点内部生成很久，终端可能仍会在两条事件之间等待较长时间。`DIFY_SHOW_PROGRESS=1` 或 `--show-progress` 至少会显示本地封装层正在处理第几个 hypothesis，以及 Dify 发来的节点事件。

`text_chunk` 事件默认只显示 chunk 序号、字符数、累计字符数和阶段，避免把 `<think>...</think>` 推理内容泄露到终端或未来前端。调试时可以设置 `DIFY_SHOW_TEXT_CHUNKS=1`，只展示非 thinking 内容的短预览。前端建议展示结构化事件：当前 hypothesis、节点 started/finished、LLM 输出字符累计、阶段 `thinking/json/answer`，不要直接把原始 text chunk 当作用户可见内容。

## 运行 HTTP API

```powershell
.\.venv\Scripts\python -m planning_agent.server --host 127.0.0.1 --port 8088
```

接口地址：

```text
POST /planning-agent/run
```

## Dify Workflow 资产

当前主工作流文件是 `dify/Research Planning Agent.yml`。

重要边界：Dify Workflow 一次只处理一个 `hypothesis_evidence_package`，输出一个 `plan_result`；本地封装层负责多 hypothesis 循环调用和 `plans[]` 聚合。更多说明见 `dify/README.md`。

在 A/B 已经完成设计发散和评审选择后，Workflow C 使用快速终稿流水线：

```text
Start -> Normalize Evidence Context -> Generate Final Plan Fast -> Normalize Final Plan Contract -> End
```

`Normalize Evidence Context` 解析证据和约束；唯一的 LLM 节点优先使用 B 放入 `planning_constraints.selected_design` 的选中设计，一次扩展成完整计划；`Normalize Final Plan Contract` 确定性覆盖身份字段并检查最终契约。C-only 调用缺少 `selected_design` 时仍按原输入生成，因此 Start/End 输入输出和本地 CLI/API 调用方式不变。

会议汇报先看 `docs/Planning Agent产品理念与ABC工作流讲解.md`；工程拆分见 `docs/Planning Agent工作流拆分与前端流式对接说明.md`；系统流式接入见 `docs/Planning Agent系统更新与流式输出对接.md`。
Dify DSL 语法和协作护栏写在 `AGENTS.md`：后续修改 `dify/*.yml` 时必须保持节点 `id`、`data.type`、edge `sourceType/targetType`、`value_selector/variable_selector` 和 End 输出变量一致。不要在主 DSL 中使用手写 YAML anchor/alias；优先使用 Dify 官方导出的完整展开风格。
