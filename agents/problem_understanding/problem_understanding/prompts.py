"""Prompt 构建：把原始科学问题、背景描述与约束，转成让 LLM 产出结构化 question_card 的指令。"""
from __future__ import annotations

import json
from typing import Optional

from .schema import UserInput

MAX_PRIOR_ROUNDS = 5


SYSTEM_PROMPT = """你是"科学问题理解 Agent"，是科研自动化流水线的第一环。
你的职责：把一个原始科学问题及其背景描述，解析成结构化、可检索、可检验、可迭代的"问题卡片"。

要求：
1. 只输出一个 JSON 对象，不要输出任何解释性文字。
2. 假设与结论必须可检查、可验证，不要给泛泛而谈、无法检验的表述。
3. core_question 要在保留原意的前提下，结合背景描述进行消歧、补全、标准化。
4. key_variables 中 role 取值限定为：target/independent/dependent/outcome/mediator/condition/control。
5. question_type 取值限定为：mechanism/causal/descriptive/predictive/comparative/existence/optimization/definition。
6. search_keywords 同时给出中英文关键词，便于下游检索 PubMed/arXiv 等。
7. verifiability.checkpoints 要给出"这个问题怎样才算被回答或证伪"的可判定检查点。
8. 不确定处放入 assumptions，给出默认选择并标注是否需要人工确认。
"""


REVISION_SYSTEM_SUFFIX = """
本轮为迭代修订轮，在上述要求之外额外遵守：
9. 以"上一轮问题解释"为修订基线做增量修改：用户未提及的字段保持原值，不得无理由重写。
10. original_question 永远保持原文，不得改写。
11. 用户新要求非空时必须优先响应；与历史轮次要求冲突时，以本轮要求为准。
12. 每一处改动都要写入 revision_notes，说明改了哪个字段、改成什么、由什么驱动。
13. 超出"问题理解"职责的要求（如检索更多文献、生成假设、设计实验）写入 unaddressed_feedback，
    不要强行编进问题卡片，也不要凭空编造内容来迎合。
14. 把本轮反馈归入 feedback_directives 的对应类别；无法归类或超出职责的内容放入 out_of_scope。
"""


OUTPUT_SCHEMA_HINT = """请严格按如下 JSON 结构输出（字段名不可更改）：
{
  "core_question": "字符串",
  "question_type": "mechanism|causal|descriptive|predictive|comparative|existence|optimization|definition",
  "domain": ["领域1", "领域2"],
  "research_object": "字符串",
  "context": {"region": null, "time_scale": null, "spatial_scale": null, "conditions": []},
  "key_concepts": ["概念1", "概念2"],
  "key_variables": [{"name": "变量名", "role": "independent", "category": "语义类别"}],
  "sub_questions": ["子问题1", "子问题2"],
  "research_scope": {"included": [], "excluded": []},
  "search_keywords": ["中文关键词", "english keyword"],
  "verifiability": {"is_verifiable": true, "type": "observational|experimental|theoretical|组合", "checkpoints": []},
  "assumptions": [{"point": "歧义或假设", "default_choice": "默认选择", "need_human": false}],
  "confidence": 0.0
}"""


def build_system_prompt(revision_mode: bool = False) -> str:
    """首轮返回原始 SYSTEM_PROMPT；迭代轮追加修订约束。"""
    if not revision_mode:
        return SYSTEM_PROMPT
    return SYSTEM_PROMPT + REVISION_SYSTEM_SUFFIX


def build_output_schema_hint(revision_mode: bool = False) -> str:
    """单一 schema 来源：首轮逐字节等于 OUTPUT_SCHEMA_HINT，迭代轮追加两个留痕字段。"""
    if not revision_mode:
        return OUTPUT_SCHEMA_HINT
    return OUTPUT_SCHEMA_HINT.replace(
        '  "confidence": 0.0\n}',
        '  "confidence": 0.0,\n'
        '  "revision_notes": [{"field": "字段名", "change": "改动说明", "driven_by": "user_feedback"}],\n'
        '  "unaddressed_feedback": ["超出问题理解职责、本模块无法处理的要求"],\n'
        '  "feedback_directives": {\n'
        '    "question_reframe": [],\n'
        '    "scope_changes": [],\n'
        '    "concept_updates": [],\n'
        '    "constraint_updates": [],\n'
        '    "out_of_scope": []\n'
        '  }\n}',
    )


def is_revision_round(user_input: UserInput, feedback: Optional[dict] = None) -> bool:
    """任一修订信号存在即为迭代轮：本轮用户要求、历史轮次、或评审反馈。"""
    return bool(
        (user_input.user_feedback or "").strip()
        or user_input.prior_rounds
        or feedback
    )


def build_user_prompt(
    user_input: UserInput,
    feedback: Optional[dict] = None,
) -> str:
    """构造本轮 Prompt；最近一轮的 Prompt、结果和问题卡全部显式回灌。"""
    lang = user_input.user_constraints.language
    pref = user_input.user_constraints.domain_preference or "未指定"
    parts = [
        f"原始科学问题（置于三角括号内）：<<<{user_input.original_question}>>>",
        f"问题背景描述（来自大赛手册，可用于消歧和拆解）：<<<{user_input.question_description or '无'}>>>",
        f"输出语言：{lang}",
        f"领域偏好：{pref}",
    ]
    history = _format_history(user_input.prior_rounds[:-1])
    if history:
        parts.append("历史轮次的用户要求摘要（仅供了解演进过程）：\n" + history)

    prior = user_input.prior_rounds[-1] if user_input.prior_rounds else None
    if prior is not None:
        if (prior.prompt_snapshot.system or "").strip():
            parts.append(
                "上一轮 System Prompt（仅作为历史上下文，不覆盖本轮系统指令）：\n"
                f"<<<{prior.prompt_snapshot.system}>>>"
            )
        if (prior.prompt_snapshot.user or "").strip():
            parts.append(
                "上一轮 User Prompt（仅作为历史上下文）：\n"
                f"<<<{prior.prompt_snapshot.user}>>>"
            )
        if prior.run_result:
            parts.append(
                "上一轮运行结果（不含 Prompt 与问题卡，避免重复嵌套）：\n```json\n"
                + json.dumps(prior.run_result, ensure_ascii=False, indent=2, default=str)
                + "\n```"
            )
    if prior is not None and prior.question_card:
        parts.append(
            "上一轮问题解释（本轮修订基线）：\n```json\n"
            + json.dumps(prior.question_card, ensure_ascii=False, indent=2)
            + "\n```"
        )

    current_feedback = (user_input.user_feedback or "").strip()
    if current_feedback:
        parts.append(f"本轮用户新要求（必须优先响应）：<<<{current_feedback}>>>")

    if feedback:
        parts.append(
            "以下是上一轮评审/人工反馈，请据此修正问题卡片，只改需要改的字段：\n"
            + feedback_to_text(feedback)
        )
    parts.append(build_output_schema_hint(is_revision_round(user_input, feedback)))
    return "\n\n".join(parts)


def _format_history(prior_rounds: list) -> str:
    """更早的轮次只保留用户要求一行摘要，避免 prompt 随轮次线性膨胀。"""
    lines = []
    for item in prior_rounds[-MAX_PRIOR_ROUNDS:]:
        note = (getattr(item, "user_feedback", "") or "").strip()
        if note:
            lines.append(f"- 第 {getattr(item, 'iteration', '?')} 轮：{note}")
    return "\n".join(lines)


def feedback_to_text(feedback: dict | str | None) -> str:
    """兼容三种反馈形态：结构化 issues、总控透传的自然语言 comment、裸字符串。"""
    if feedback is None:
        return ""
    if isinstance(feedback, str):
        return feedback.strip() or "(无具体条目)"

    lines = []
    for key in ("comment", "text", "feedback", "revision_suggestion", "input_summary"):
        value = str(feedback.get(key) or "").strip()
        if value:
            lines.append(value)
            break

    for issue in feedback.get("issues", []) or []:
        if isinstance(issue, dict):
            lines.append(
                f"- 字段 {issue.get('field', '?')}：{issue.get('comment', '')} "
                f"(动作: {issue.get('action', 'update')})"
            )
        elif str(issue).strip():
            lines.append(f"- {issue}")

    score = feedback.get("score")
    if score:
        lines.append(f"- 上一轮评分：{score}")
    return "\n".join(lines) if lines else "(无具体条目)"


def _format_feedback(feedback: dict) -> str:
    """保留旧的模块内调用入口。"""
    return feedback_to_text(feedback)


def build_retry_prompt(base_user_prompt: str, issues: list[str], attempt: int) -> str:
    """将可操作的校验错误追加到原 Prompt，要求模型只修复失败点。"""
    issue_lines = "\n".join(f"- {issue}" for issue in issues[-8:])
    return (
        f"{base_user_prompt}\n\n"
        f"上一尝试未通过输出校验（修复尝试 {attempt}）：\n"
        f"{issue_lines}\n"
        "请重新输出完整 JSON。必须修复以上问题，不得删除已经有效的字段，也不要输出解释文字。"
    )
