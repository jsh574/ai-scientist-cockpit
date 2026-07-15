# 真实 Agent 接入说明

当前系统通过 `backend/app/adapters.py` 接入项目内 `agents/` 目录中的五个模块：

|阶段|源码位置|总控写入|
|---|---|---|
|问题理解|`agents/problem_understanding`|`question_card`|
|知识整合|`agents/knowledge_integration`|`literature_cards`、`evidence_cards`、`knowledge_gaps`|
|候选假设生成|`agents/hypothesis_generation`|`hypothesis_cards`|
|证据梳理|`agents/evidence_mapping`|`evidence_map`|
|研究计划|`agents/planning`|`research_plan`|

五个模块都由网关包装成统一响应：

```json
{
  "metadata": {
    "task_id": "task_001",
    "agent_id": "question_understanding_agent",
    "stage": "question_understanding",
    "iteration": 1,
    "status": "success",
    "trace_id": "trace_001",
    "duration_ms": 1200
  },
  "payload": {},
  "self_review": {
    "passed": true,
    "overall_score": 0.8,
    "threshold": 0.75,
    "dimension_scores": {},
    "issues": [],
    "suggestions": []
  }
}
```

## 配置

1. 使用 Python 3.10 或更高版本创建虚拟环境。
2. 安装 `backend/requirements.txt`。
3. 将 `backend/.env.example` 复制为 `backend/.env` 并填写密钥；Agent 路径已有项目内默认值。
4. 密钥只能放在环境变量或未提交的 `.env` 中，不能写回 Agent 源码。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

前端另开一个终端：

```powershell
npm install
npm run dev
```

接口文档：`http://127.0.0.1:8000/docs`。健康检查：`GET /api/health`。

## 适配规则

- `task_context` 是总控唯一状态源；Agent 只能获得本阶段需要的字段。
- `agents/registry.json` 是机器可读注册表，`backend/app/agent_protocol.py` 是运行时契约；两者变更必须同步并补测试。
- Agent 只能返回本阶段声明的 payload 字段，Review Gate 会拒绝越权写入。
- 五个 Agent 源码随主仓库提交；默认配置不包含机器相关的绝对路径。
- 需要模型的 Agent 共享 `ProjectLLMClient`，统一使用 `backend/.env` 中的模型、兼容地址、密钥、超时和 JSON 模式。
- 问题理解模块原有 `{status, meta, data}` 会被转换成标准信封。
- `research_object`、`key_concepts`、`sub_questions`、`search_keywords` 会在问题理解与知识整合之间双向适配。
- 任何异常都返回 `metadata.status=failed`，前端停止后续调度并展示 `self_review.issues`。
- Planning Agent 保留原有输入校验、假设排序、多方案聚合和引用 ID 护栏；总控用 `qwen3.7-max` 替代其原 Dify 工作流调用。
- Evidence Mapping Agent 保留原有规则引擎，通过字段适配兼容总控的 `source_literature_id`、`support_direction` 和 `strength_score`。
- 问题理解、知识整合、候选假设生成、证据梳理和研究计划调用真实 Agent；最终审核由后端 Orchestrator Review Gate 根据完整上下文生成。

## 新增 Agent 检查表

1. 在 `agents/registry.json` 和 `AGENT_SPECS` 声明 stage、reads、writes。
2. 入口接受 `task_context` 切片和可选 feedback，不直接修改全局上下文。
3. 返回统一响应并完成 self-review。
4. 所有引用使用上游真实 ID，不生成不存在的 evidence/literature ID。
5. 异常转换为 `metadata.status=failed`，不得吞掉异常后返回成功。
6. 增加适配、追溯和失败路径测试。
