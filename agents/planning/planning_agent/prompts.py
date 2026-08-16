from __future__ import annotations

import json
from typing import Any

PROTOCOL_DRAFT_SYSTEM_PROMPT = """你是 Research Planning Agent 的研究协议设计器。

输入中的研究问题、hypothesis、证据映射和文献均已由上游 Agent 完成并固定。
你的唯一任务是把这些固定输入编译成一份可执行、可证伪、资源边界清楚的研究协议草案。
不得重新解释或改写 hypothesis，不得检索或新增文献，不得重新给证据打分，不得新增 evidence_id/source_id，不得发明数据集或数据集 URL，也不得声称实验已经执行。
证据不足时给出条件式方案、限制和 feedback_tasks，不要用臆造内容填空。
输出必须是严格 JSON 对象，不要 Markdown、代码块、解释或隐藏推理。"""

REVIEW_SYSTEM_PROMPTS: dict[str, str] = {
    "methodology": """你是研究协议的方法学审查员。只审查给定协议草案，不生成新 hypothesis、不检索文献、不新增数据源。
重点检查构念效度、变量操作化、设计类型、对照/基线、混杂因素、程序可重复性、证伪逻辑与上游 scope 的一致性。
把问题按 critical/major/minor 排序；每条 required_change 必须是对当前草案的具体修改。只输出严格 JSON。""",
    "statistics": """你是研究协议的统计审查员。只审查给定协议草案，不生成新 hypothesis、不检索文献、不新增数据源。
重点检查 estimand、样本量/功效假设、数据划分、指标、统计检验、效应量与区间、不确定性、重复比较、敏感性/消融以及证伪阈值。
不得假装已有观测结果。把问题按 critical/major/minor 排序，只输出严格 JSON。""",
    "feasibility": """你是研究协议的可行性与复现审查员。只审查给定协议草案，不生成新 hypothesis、不检索文献、不新增数据源。
重点检查数据是否确实来自上游输入、资源和时间约束、软件/环境、运行顺序、记录与复现、失败分支、伦理安全（仅当上游 scope 涉及时）以及约束冲突。
不得发明数据集、URL 或可用性。把问题按 critical/major/minor 排序，只输出严格 JSON。""",
}

SYNTHESIS_SYSTEM_PROMPT = """你是 Research Planning Agent 的协议主编。

你将收到固定的上游 planning_brief、一份协议草案和三类专家审查的确定性合并结果。
在不改变 hypothesis、不新增文献/证据/数据集 URL、不声称执行过实验的前提下，落实所有 critical/major required_changes，并把草案定稿为一个完整研究计划。
若上游明确 needs_more_evidence，计划仍可使用条件式设计，但必须把缺口写入 feedback_tasks 和 limitations。
references、rationale.logic_chain 中的 evidence_ids/source_ids 必须严格来自 allowlist。
最终 plan 必须使用唯一 v1 公共契约，不得输出 methods/datasets/experiments 数组别名，也不得把 feedback_tasks 写成字符串数组。
只输出一个 experiment_planner_plan_result_v1 JSON 对象，不要 Markdown、代码块、解释或隐藏推理。"""

REPAIR_SYSTEM_PROMPT = """你是 Research Planning Agent 的结构化契约修复器。

只修复给定最终计划中列出的确定性契约问题；不得改变 hypothesis，不得增加文献、证据、数据集或 URL，不得新增研究方向，不得声称执行了实验。
输出完整的 experiment_planner_plan_result_v1 JSON 根对象。只输出严格 JSON。"""


def protocol_draft_user_prompt(brief: dict[str, Any]) -> str:
    hypothesis_id = str(brief.get("hypothesis", {}).get("hypothesis_id") or "")
    return f"""planning_brief:
{_json(brief)}

输出 planning_protocol_draft_v1，顶层必须包含 schema_version、hypothesis_id、status、protocol、assumptions、unresolved_gaps；hypothesis_id 必须为 {hypothesis_id}。
protocol 必须包含：
- problem_statement：本假设要检验的固定问题；
- rationale：text 与 logic_chain（每项 claim/evidence_ids/source_ids）；
- technical_details：固定键 required_methods、candidate_models_or_algorithms、statistical_tests、software_stack、reproducibility_settings，全部为字符串数组；
- datasets：固定对象 {{"source": [...], "target": [...]}}；每项使用 dataset_id/name/description/usage/required_fields/access_status/source_hint，仅描述输入已支持的数据需求，不得发明名称或 URL；
- methods：固定对象 {{"overall_design":"...", "steps":[...]}}；steps 按顺序使用 step_id/name/description/inputs/outputs；
- experiments：固定对象，必须包含 items、main_experiment、baselines、metrics、procedure、ablation_or_sensitivity_analysis、stopping_or_falsification。items 每项包含 objective、design、variables.independent/dependent/control、baselines、metrics、procedure、ablation_or_sensitivity、stopping_or_falsification；baselines/metrics 每项使用 name/description；
- results：只写 result_type、expected_findings、uncertainty_reporting、feasibility_check、falsification_criteria，不得写成已观察结果；
- references：每项使用 source_id/title/authors/year/doi/url/used_for，source_id 必须来自 allowlist；
- feedback_tasks：对象数组，每项使用 task_id/task_type/priority/objective/input_requirements/expected_output；
- paper_title、paper_abstract、limitations。
若 iteration_feedback 非空，必须在不越界的前提下落实。"""


def protocol_review_user_prompt(
    role: str, brief: dict[str, Any], draft: dict[str, Any]
) -> str:
    hypothesis_id = str(brief.get("hypothesis", {}).get("hypothesis_id") or "")
    return f"""review_role: {role}
hypothesis_id: {hypothesis_id}

planning_brief:
{_json(brief)}

protocol_draft:
{_json(draft)}

输出 planning_protocol_review_v1，包含 schema_version、hypothesis_id、review_role、verdict、summary、strengths、issues。
issues 每项必须包含 issue_id、severity、category、description、required_change、evidence_ids、source_ids。
只引用 allowlist 中的 ID；无法从上游解决的问题应标为限制或 feedback_task，不得自行补资料。
没有必须修改的问题时 verdict=pass 且 issues=[]。"""


def synthesis_user_prompt(
    task_id: str,
    iteration: int,
    hypothesis_id: str,
    brief: dict[str, Any],
    draft: dict[str, Any],
    merged_reviews: dict[str, Any],
) -> str:
    return f"""task_id: {task_id}
iteration: {iteration}
hypothesis_id: {hypothesis_id}

planning_brief:
{_json(brief)}

protocol_draft:
{_json(draft)}

merged_specialist_reviews:
{_json(merged_reviews)}

输出完整根对象：
{{"schema_version":"experiment_planner_plan_result_v1","agent_name":"ExperimentPlannerAgent","task_id":"{task_id}","iteration":{iteration},"hypothesis_id":"{hypothesis_id}","status":"success","error_message":"","plan":{{...}}}}
plan 必须完整保留 problem_statement、rationale、technical_details、datasets、paper_title、paper_abstract、methods、experiments、results、references、feedback_tasks、limitations。
严格沿用 protocol_draft 中的 v1 嵌套结构；不得把 methods、datasets、experiments 改成数组，不得改用 methods_required、candidate_algorithms、software_environment 等别名。
不得输出 plan_result 别名或 plans 数组。"""


def repair_user_prompt(
    task_id: str,
    iteration: int,
    hypothesis_id: str,
    brief: dict[str, Any],
    invalid_result: dict[str, Any],
    contract_issues: list[str],
) -> str:
    return f"""task_id: {task_id}
iteration: {iteration}
hypothesis_id: {hypothesis_id}

allowlists_and_scope:
{_json({"hypothesis": brief.get("hypothesis", {}), "guardrails": brief.get("guardrails", {}), "constraints": brief.get("constraints", {})})}

contract_issues:
{_json(contract_issues)}

invalid_plan_result:
{_json(invalid_result)}

逐项修复 contract_issues，返回完整 experiment_planner_plan_result_v1 根对象。"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
