"""Prompt 构建：把原始科学问题、背景描述与约束，转成让 LLM 产出结构化 question_card 的指令。"""
from __future__ import annotations

from typing import Optional

from .schema import UserInput


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


def build_user_prompt(user_input: UserInput, feedback: Optional[dict] = None) -> str:
    lang = user_input.user_constraints.language
    pref = user_input.user_constraints.domain_preference or "未指定"
    parts = [
        f"原始科学问题（置于三角括号内）：<<<{user_input.original_question}>>>",
        f"问题背景描述（来自大赛手册，可用于消歧和拆解）：<<<{user_input.question_description or '无'}>>>",
        f"输出语言：{lang}",
        f"领域偏好：{pref}",
    ]
    if feedback:
        parts.append(
            "以下是上一轮评审/人工反馈，请据此修正问题卡片，只改需要改的字段：\n"
            + _format_feedback(feedback)
        )
    parts.append(OUTPUT_SCHEMA_HINT)
    return "\n\n".join(parts)


def _format_feedback(feedback: dict) -> str:
    lines = []
    for issue in feedback.get("issues", []):
        lines.append(
            f"- 字段 {issue.get('field', '?')}：{issue.get('comment', '')} "
            f"(动作: {issue.get('action', 'update')})"
        )
    score = feedback.get("score")
    if score:
        lines.append(f"- 上一轮评分：{score}")
    return "\n".join(lines) if lines else "(无具体条目)"
