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
    "status": "success"
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
- 五个 Agent 源码随主仓库提交；默认配置不包含机器相关的绝对路径。
- 需要模型的 Agent 共享 `ProjectLLMClient`，统一使用 `backend/.env` 中的模型、兼容地址、密钥、超时和 JSON 模式。
- 问题理解模块原有 `{status, meta, data}` 会被转换成标准信封。
- `research_object`、`key_concepts`、`sub_questions`、`search_keywords` 会在问题理解与知识整合之间双向适配。
- 任何异常都返回 `metadata.status=failed`，前端停止后续调度并展示 `self_review.issues`。
- Planning Agent 保留原有输入校验、假设排序、多方案聚合和引用 ID 护栏；总控用 `qwen3.7-max` 替代其原 Dify 工作流调用。
- Evidence Mapping Agent 保留原有规则引擎，通过字段适配兼容总控的 `source_literature_id`、`support_direction` 和 `strength_score`。
- 问题理解、知识整合、候选假设生成、证据梳理和研究计划调用真实 Agent；最终审核暂时沿用前端 mock。
