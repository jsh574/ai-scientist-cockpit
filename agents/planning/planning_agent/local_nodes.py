from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from planning_agent.schemas import (
    FINAL_PLAN_SCHEMA,
    PLAN_REQUIRED,
    PROTOCOL_DRAFT_SCHEMA,
    PROTOCOL_REVIEW_SCHEMA,
    REVIEW_ROLES,
    schema_issues,
)

_SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2}
_ROLE_ORDER = {role: index for index, role in enumerate(REVIEW_ROLES)}


def compile_planning_brief(
    question_card: Any,
    hypothesis_id: str,
    hypothesis_evidence_package: Any,
    planning_constraints: Any = None,
    user_constraints: Any = None,
    iteration_feedback: str = "",
) -> dict[str, Any]:
    """Compile supplied upstream outputs into one bounded protocol-design brief."""
    question = _mapping(question_card)
    package = _mapping(hypothesis_evidence_package)
    constraints = _mapping(planning_constraints)
    subset = _mapping(package.get("evidence_subset"))
    evidence_rows = [
        {
            "direction": direction,
            "evidence_id": str(item.get("evidence_id") or ""),
            "claim": item.get("claim", ""),
            "summary": item.get("summary", ""),
            "source_literature_id": item.get("source_literature_id", ""),
            "support_direction": item.get("support_direction", direction),
            "strength_score": item.get("strength_score", 0),
        }
        for key, direction in (
            ("supporting_evidence", "supporting"),
            ("opposing_evidence", "opposing"),
            ("uncertain_evidence", "uncertain"),
        )
        for item in _list(subset.get(key))
        if isinstance(item, Mapping) and item.get("evidence_id")
    ]
    literature = [
        {
            "literature_id": str(item.get("literature_id") or ""),
            "title": item.get("title", ""),
            "authors": _list(item.get("authors")),
            "year": item.get("year", ""),
            "doi": item.get("doi", ""),
            "url": item.get("url", ""),
            "main_findings": _list(item.get("main_findings")),
        }
        for item in _list(package.get("source_literature"))
        if isinstance(item, Mapping) and item.get("literature_id")
    ]
    review = _mapping(package.get("evidence_review"))
    return {
        "boundary": {
            "task": "Compile an executable research protocol for the fixed hypothesis.",
            "out_of_scope": [
                "retrieve literature",
                "generate or rewrite hypotheses",
                "re-score upstream evidence",
                "invent datasets or dataset URLs",
                "execute experiments or claim observed results",
            ],
        },
        "question": {
            "question_id": question.get("question_id", ""),
            "core_question": question.get("core_question", ""),
            "research_object": question.get("research_object", {}),
            "key_variables": question.get("key_variables", []),
            "sub_questions": question.get("sub_questions", []),
            "research_scope": question.get("research_scope", {}),
            "domain": question.get("domain", []),
        },
        "hypothesis": {
            "hypothesis_id": hypothesis_id or package.get("hypothesis_id", ""),
            "statement": package.get("hypothesis", ""),
            "rationale": package.get("rationale", ""),
            "target_variables": package.get("target_variables", []),
            "expected_observation": package.get("expected_observation", ""),
            "validation_idea": package.get("validation_idea", ""),
            "scores": package.get("scores", {}),
        },
        "upstream_evidence": {
            "evidence_rows": evidence_rows,
            "evidence_summary": package.get("evidence_summary", {}),
            "source_literature": literature,
            "knowledge_gaps": package.get("knowledge_gaps", []),
            "limitations": package.get("limitations", []),
            "needs_more_evidence": bool(package.get("needs_more_evidence")),
            "detailed_review": {
                "evidence_bindings": review.get("evidence_bindings", []),
                "conflict_pairs": review.get("conflict_pairs", []),
                "gaps": review.get("gaps", []),
                "verdict": review.get("verdict", {}),
            },
        },
        "constraints": {
            "planning": constraints,
            "user": _mapping(user_constraints),
            "iteration_feedback": str(iteration_feedback or ""),
        },
        "guardrails": {
            "allowed_evidence_ids": [row["evidence_id"] for row in evidence_rows],
            "allowed_source_ids": [row["literature_id"] for row in literature],
            "forbidden_actions": constraints.get("forbidden_actions", []),
            "allowed_validation_types": constraints.get("allowed_validation_types", []),
        },
    }


def planning_brief_issues(brief: Any) -> list[str]:
    context = _mapping(brief)
    hypothesis = _mapping(context.get("hypothesis"))
    evidence = _mapping(context.get("upstream_evidence"))
    issues: list[str] = []
    if not str(hypothesis.get("hypothesis_id") or "").strip():
        issues.append("hypothesis_id is missing")
    if not str(hypothesis.get("statement") or "").strip():
        issues.append("hypothesis statement is missing")
    if not _list(evidence.get("evidence_rows")):
        issues.append("no usable upstream evidence is bound to the hypothesis")
    return issues


def guard_protocol_draft(
    payload: Any, brief: Any, hypothesis_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = _mapping(payload)
    normalized: list[str] = []
    if result.get("schema_version") != "planning_protocol_draft_v1":
        normalized.append("schema_version")
    result["schema_version"] = "planning_protocol_draft_v1"
    if result.get("hypothesis_id") != hypothesis_id:
        normalized.append("hypothesis_id")
    result["hypothesis_id"] = hypothesis_id
    protocol = _mapping(result.get("protocol")) or _mapping(result.get("plan"))
    if "plan" in result and "protocol" not in result:
        normalized.append("protocol<-plan")
    normalized.extend(_normalize_protocol_collections(protocol, brief))
    for field in PLAN_REQUIRED:
        if field not in protocol:
            protocol[field] = _plan_default(field)
            normalized.append(f"protocol.{field}")
    result["protocol"] = protocol
    result.pop("plan", None)
    result["assumptions"] = _string_list(result.get("assumptions"))
    result["unresolved_gaps"] = _string_list(result.get("unresolved_gaps"))
    cleanup_issues = _clean_intermediate_traceability(protocol, brief)
    quality_issues = _protocol_quality_issues(protocol)
    result["status"] = "partial_success" if cleanup_issues or quality_issues else "success"
    validation = schema_issues(result, PROTOCOL_DRAFT_SCHEMA)
    issues = [*cleanup_issues, *quality_issues, *validation]
    if validation:
        result["status"] = "partial_success"
    return result, {
        "passed": not validation and bool(protocol),
        "issues": issues,
        "normalized_fields": normalized,
    }


def guard_protocol_review(
    payload: Any, brief: Any, hypothesis_id: str, review_role: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = _mapping(payload)
    result["schema_version"] = "planning_protocol_review_v1"
    result["hypothesis_id"] = hypothesis_id
    result["review_role"] = review_role
    result["summary"] = str(result.get("summary") or "")
    result["strengths"] = _string_list(result.get("strengths"))
    allowed_evidence, allowed_sources = _allowlists(brief)
    normalized_issues: list[dict[str, Any]] = []
    removed_refs = 0
    for index, raw in enumerate(_list(result.get("issues")), start=1):
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        severity = str(item.get("severity") or "major").lower()
        item["severity"] = severity if severity in _SEVERITY_ORDER else "major"
        item["issue_id"] = str(item.get("issue_id") or f"{review_role}_{index}")
        item["category"] = str(item.get("category") or review_role)
        item["description"] = str(item.get("description") or "")
        item["required_change"] = str(item.get("required_change") or "")
        before_evidence = _string_list(item.get("evidence_ids"))
        before_sources = _string_list(item.get("source_ids"))
        item["evidence_ids"] = [value for value in before_evidence if value in allowed_evidence]
        item["source_ids"] = [value for value in before_sources if value in allowed_sources]
        removed_refs += len(before_evidence) - len(item["evidence_ids"])
        removed_refs += len(before_sources) - len(item["source_ids"])
        normalized_issues.append(item)
    result["issues"] = normalized_issues
    requested_verdict = str(result.get("verdict") or "").lower()
    if requested_verdict not in {"pass", "revise", "blocked"}:
        requested_verdict = "revise" if normalized_issues else "pass"
    result["verdict"] = requested_verdict
    validation = schema_issues(result, PROTOCOL_REVIEW_SCHEMA)
    issues = list(validation)
    if removed_refs:
        issues.append(f"removed {removed_refs} review traceability IDs outside the allowlist")
    return result, {"passed": not validation, "issues": issues}


def merge_protocol_reviews(
    reviews: list[dict[str, Any]], failed_roles: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Deterministically prioritize and deduplicate specialist feedback."""
    deduplicated: dict[str, dict[str, Any]] = {}
    for review in reviews:
        role = str(review.get("review_role") or "unknown")
        for item in _list(review.get("issues")):
            if not isinstance(item, Mapping):
                continue
            row = dict(item)
            row["review_role"] = role
            key = "|".join(
                (
                    str(row.get("category") or "").strip().lower(),
                    _canonical_text(row.get("description")),
                    _canonical_text(row.get("required_change")),
                )
            )
            previous = deduplicated.get(key)
            if previous is None or _SEVERITY_ORDER.get(
                str(row.get("severity")), 9
            ) < _SEVERITY_ORDER.get(str(previous.get("severity")), 9):
                deduplicated[key] = row
    issues = sorted(
        deduplicated.values(),
        key=lambda item: (
            _SEVERITY_ORDER.get(str(item.get("severity")), 9),
            _ROLE_ORDER.get(str(item.get("review_role")), 9),
            str(item.get("issue_id") or ""),
        ),
    )
    failures = dict(failed_roles or {})
    return {
        "reviewed_roles": [str(review.get("review_role")) for review in reviews],
        "failed_roles": sorted(failures),
        "review_failures": failures,
        "critical_issues": [item for item in issues if item.get("severity") == "critical"],
        "required_changes": [
            item for item in issues if item.get("severity") in {"critical", "major"}
        ],
        "optional_improvements": [
            item for item in issues if item.get("severity") == "minor"
        ],
        "review_summaries": [
            {
                "review_role": review.get("review_role"),
                "verdict": review.get("verdict"),
                "summary": review.get("summary"),
                "strengths": review.get("strengths", []),
            }
            for review in reviews
        ],
    }


def guard_final_plan(
    plan_payload: Any,
    brief: Any,
    task_id: str,
    iteration: int,
    hypothesis_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = _mapping(plan_payload)
    normalized: list[str] = []
    for field, expected in {
        "schema_version": "experiment_planner_plan_result_v1",
        "agent_name": "ExperimentPlannerAgent",
        "task_id": task_id,
        "iteration": iteration,
        "hypothesis_id": hypothesis_id,
    }.items():
        if result.get(field) != expected:
            normalized.append(field)
        result[field] = expected
    plan = _mapping(result.get("plan"))
    if not plan:
        alias = _mapping(result.get("plan_result"))
        plan = _mapping(alias.get("plan")) or alias
        if plan:
            normalized.append("plan<-plan_result")
    result["plan"] = plan
    raw_dataset_urls = _dataset_urls(plan)
    normalized.extend(_normalize_protocol_collections(plan, brief))
    requested_status = str(result.get("status") or "success")
    result["status"] = (
        requested_status
        if requested_status in {"success", "partial_success", "failed"}
        else "success"
    )
    result["error_message"] = str(result.get("error_message") or "")
    issues: list[str] = []
    nonrepairable: list[str] = []
    if not plan:
        issues.append("plan must be a non-empty object")
    else:
        issues.extend(_protocol_quality_issues(plan))
        allowed_evidence, allowed_sources = _allowlists(brief)
        referenced_evidence, referenced_sources = _referenced_ids(plan)
        unknown_evidence = sorted(referenced_evidence - allowed_evidence)
        unknown_sources = sorted(referenced_sources - allowed_sources)
        if unknown_evidence:
            message = "unknown evidence_ids: " + ", ".join(unknown_evidence)
            issues.append(message)
            nonrepairable.append(message)
        if unknown_sources:
            message = "unknown source_ids: " + ", ".join(unknown_sources)
            issues.append(message)
            nonrepairable.append(message)
        if raw_dataset_urls or _dataset_urls(plan):
            message = "dataset URLs are not allowed because none were supplied upstream"
            issues.append(message)
            nonrepairable.append(message)
    validation = schema_issues(result, FINAL_PLAN_SCHEMA)
    issues.extend(validation)
    if issues:
        result["status"] = "failed"
        result["error_message"] = "; ".join(_unique_strings(issues))
    else:
        result["status"] = (
            result["status"] if result["status"] in {"success", "partial_success"} else "success"
        )
    return result, {
        "passed": not issues,
        "issues": _unique_strings(issues),
        "repairable": bool(issues) and not nonrepairable,
        "nonrepairable_issues": nonrepairable,
        "normalized_identity_fields": normalized,
    }


def compact_for_synthesis(value: Any, max_chars: int) -> Any:
    """Keep a deterministic JSON object under the model context budget."""
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) <= max_chars:
        return value
    if isinstance(value, Mapping):
        compacted = dict(value)
        evidence = _mapping(compacted.get("upstream_evidence"))
        evidence.pop("detailed_review", None)
        evidence["evidence_rows"] = [
            {
                **dict(row),
                "claim": _clip_text(row.get("claim"), 600),
                "summary": _clip_text(row.get("summary"), 600),
            }
            for row in _list(evidence.get("evidence_rows"))
            if isinstance(row, Mapping)
        ]
        evidence["source_literature"] = [
            {
                "literature_id": row.get("literature_id", ""),
                "title": _clip_text(row.get("title"), 400),
                "authors": row.get("authors", []),
                "year": row.get("year", ""),
                "doi": row.get("doi", ""),
                "url": row.get("url", ""),
            }
            for row in _list(evidence.get("source_literature"))
            if isinstance(row, Mapping)
        ]
        evidence["knowledge_gaps"] = [
            _clip_text(
                row.get("description", row) if isinstance(row, Mapping) else row,
                400,
            )
            for row in _list(evidence.get("knowledge_gaps"))[:8]
        ]
        compacted["upstream_evidence"] = evidence
        encoded = json.dumps(compacted, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) <= max_chars:
            return compacted
        # Preserve scope, identity and allowlists even under an unusually small
        # budget; reduce prose rather than returning an opaque JSON substring.
        question = _mapping(compacted.get("question"))
        minimal = {
            "context_compacted": True,
            "boundary": compacted.get("boundary", {}),
            "question": {
                "question_id": question.get("question_id", ""),
                "core_question": _clip_text(question.get("core_question"), 800),
                "research_object": question.get("research_object", {}),
                "key_variables": question.get("key_variables", []),
                "research_scope": question.get("research_scope", {}),
            },
            "hypothesis": compacted.get("hypothesis", {}),
            "upstream_evidence": evidence,
            "constraints": compacted.get("constraints", {}),
            "guardrails": compacted.get("guardrails", {}),
        }
        return minimal
    return {"context_compacted": True, "value": _clip_text(value, max_chars)}


def _clean_intermediate_traceability(plan: dict[str, Any], brief: Any) -> list[str]:
    allowed_evidence, allowed_sources = _allowlists(brief)
    removed = 0
    rationale = _mapping(plan.get("rationale"))
    for item in _list(rationale.get("logic_chain")):
        if not isinstance(item, dict):
            continue
        evidence_ids = _string_list(item.get("evidence_ids"))
        source_ids = _string_list(item.get("source_ids"))
        item["evidence_ids"] = [value for value in evidence_ids if value in allowed_evidence]
        item["source_ids"] = [value for value in source_ids if value in allowed_sources]
        removed += len(evidence_ids) - len(item["evidence_ids"])
        removed += len(source_ids) - len(item["source_ids"])
    references: list[Any] = []
    for item in _list(plan.get("references")):
        if isinstance(item, Mapping) and str(item.get("source_id") or "") in allowed_sources:
            references.append(dict(item))
        else:
            removed += 1
    plan["references"] = references
    return [f"removed {removed} draft traceability entries outside the allowlist"] if removed else []


def _protocol_quality_issues(plan: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    if not plan:
        return ["protocol is empty"]
    for field in ("problem_statement", "rationale", "technical_details", "results"):
        if not plan.get(field):
            issues.append(f"plan.{field} must be non-empty")
    methods = _mapping(plan.get("methods"))
    experiments = _mapping(plan.get("experiments"))
    if not _list(methods.get("steps")):
        issues.append("plan.methods.steps must contain at least one item")
    if not _list(experiments.get("items")):
        issues.append("plan.experiments.items must contain at least one item")
    return issues


def _referenced_ids(plan: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    evidence_ids: set[str] = set()
    source_ids: set[str] = set()
    rationale = _mapping(plan.get("rationale"))
    for item in _list(rationale.get("logic_chain")):
        if isinstance(item, Mapping):
            evidence_ids.update(_string_list(item.get("evidence_ids")))
            source_ids.update(_string_list(item.get("source_ids")))
    for item in _list(plan.get("references")):
        if isinstance(item, Mapping) and item.get("source_id"):
            source_ids.add(str(item["source_id"]))
    return evidence_ids, source_ids


def _dataset_urls(plan: Mapping[str, Any]) -> list[str]:
    urls: list[str] = []
    datasets_value = plan.get("datasets")
    if isinstance(datasets_value, list):
        rows = datasets_value
    else:
        datasets = _mapping(datasets_value)
        rows = [
            *_list(datasets.get("source")),
            *_list(datasets.get("target")),
            *_list(datasets.get("data_requirements")),
        ]
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        for key in ("url", "dataset_url", "download_url"):
            value = str(item.get(key) or "").strip()
            if value:
                urls.append(value)
    return urls


def _allowlists(brief: Any) -> tuple[set[str], set[str]]:
    guardrails = _mapping(_mapping(brief).get("guardrails"))
    return set(_string_list(guardrails.get("allowed_evidence_ids"))), set(
        _string_list(guardrails.get("allowed_source_ids"))
    )


def _plan_default(field: str) -> Any:
    if field in {
        "rationale",
        "technical_details",
        "datasets",
        "methods",
        "experiments",
        "results",
    }:
        return {}
    if field in {
        "references",
        "feedback_tasks",
        "limitations",
    }:
        return []
    return ""


def _normalize_protocol_collections(plan: dict[str, Any], brief: Any = None) -> list[str]:
    """Normalize Qwen/legacy variants into the documented public v1 plan shape."""
    normalized: list[str] = []
    allowed_evidence, allowed_sources = _allowlists(brief)
    plan["problem_statement"] = str(plan.get("problem_statement") or "")
    plan["paper_title"] = str(plan.get("paper_title") or "")
    plan["paper_abstract"] = str(plan.get("paper_abstract") or "")

    rationale_value = plan.get("rationale")
    rationale_row = _mapping(rationale_value)
    if isinstance(rationale_value, str):
        rationale_row = {"text": rationale_value}
        normalized.append("plan.rationale<string->object>")
    logic_chain = _list(rationale_row.get("logic_chain"))
    for index, link in enumerate(logic_chain):
        if isinstance(link, str):
            value = link.strip()
            if value in allowed_evidence:
                logic_chain[index] = {
                    "claim": "",
                    "evidence_ids": [value],
                    "source_ids": [],
                }
            elif value in allowed_sources:
                logic_chain[index] = {
                    "claim": "",
                    "evidence_ids": [],
                    "source_ids": [value],
                }
            else:
                logic_chain[index] = {
                    "claim": value,
                    "evidence_ids": [],
                    "source_ids": [],
                }
            normalized.append(f"plan.rationale.logic_chain.{index}<string->object>")
        elif isinstance(link, Mapping):
            row = dict(link)
            row["claim"] = str(row.get("claim") or row.get("text") or "")
            row["evidence_ids"] = _string_list(row.get("evidence_ids"))
            row["source_ids"] = _string_list(row.get("source_ids"))
            logic_chain[index] = row
    plan["rationale"] = {
        "text": str(rationale_row.get("text") or rationale_row.get("summary") or ""),
        "logic_chain": logic_chain,
    }

    technical = _mapping(plan.get("technical_details"))
    plan["technical_details"] = {
        "required_methods": _first_string_list(
            technical.get("required_methods"), technical.get("methods_required")
        ),
        "candidate_models_or_algorithms": _first_string_list(
            technical.get("candidate_models_or_algorithms"),
            technical.get("candidate_algorithms"),
            technical.get("models_or_algorithms"),
        ),
        "statistical_tests": _string_list(technical.get("statistical_tests")),
        "software_stack": _first_string_list(
            technical.get("software_stack"), technical.get("software_environment")
        ),
        "reproducibility_settings": _string_list(
            technical.get("reproducibility_settings")
        ),
    }

    datasets_value = plan.get("datasets")
    datasets_object = _mapping(datasets_value)
    source_values = _list(datasets_object.get("source"))
    target_values = _list(datasets_object.get("target"))
    requirements = _list(datasets_object.get("data_requirements"))
    if isinstance(datasets_value, list):
        source_values = []
        target_values = []
        for item in datasets_value:
            row = _mapping(item)
            role = str(row.get("role") or row.get("dataset_role") or "").lower()
            (target_values if role == "target" else source_values).append(item)
        normalized.append("plan.datasets<array->object>")
    elif requirements and not source_values:
        source_values = requirements
        normalized.append("plan.datasets.data_requirements->source")
    plan["datasets"] = {
        "source": [_normalize_dataset(item, index, "source") for index, item in enumerate(source_values)],
        "target": [_normalize_dataset(item, index, "target") for index, item in enumerate(target_values)],
    }

    methods_value = plan.get("methods")
    methods_object = _mapping(methods_value)
    method_values = _list(methods_value) if isinstance(methods_value, list) else (
        _list(methods_object.get("steps")) or _list(methods_object.get("method_steps"))
    )
    if isinstance(methods_value, list):
        normalized.append("plan.methods<array->object>")
    plan["methods"] = {
        "overall_design": str(methods_object.get("overall_design") or methods_object.get("design") or ""),
        "steps": [_normalize_method_step(item, index) for index, item in enumerate(method_values)],
    }

    experiments_value = plan.get("experiments")
    experiments_object = _mapping(experiments_value)
    experiment_values = _list(experiments_value) if isinstance(experiments_value, list) else (
        _list(experiments_object.get("items"))
        or _list(experiments_object.get("experiments"))
    )
    if not experiment_values and experiments_object:
        main = _mapping(experiments_object.get("main_experiment"))
        if main:
            experiment_values = [{**experiments_object, **main}]
        elif experiments_object.get("objective"):
            experiment_values = [experiments_object]
    if isinstance(experiments_value, list):
        normalized.append("plan.experiments<array->object>")
    experiment_items = [
        _normalize_experiment(item, index) for index, item in enumerate(experiment_values)
    ]
    main_source = _mapping(experiments_object.get("main_experiment"))
    if main_source:
        main_item = _normalize_experiment(main_source, 0)
    elif experiment_items:
        main_item = experiment_items[0]
    else:
        main_item = _normalize_experiment({}, 0)
    baselines = _dedupe_named_items([
        *_named_items(experiments_object.get("baselines")),
        *(item for row in experiment_items for item in row["baselines"]),
    ])
    metrics = _dedupe_named_items([
        *_named_items(experiments_object.get("metrics")),
        *(item for row in experiment_items for item in row["metrics"]),
    ])
    procedure = _unique_strings([
        *_string_list(experiments_object.get("procedure")),
        *(item for row in experiment_items for item in row["procedure"]),
    ])
    ablation = _unique_strings([
        *_first_string_list(
            experiments_object.get("ablation_or_sensitivity_analysis"),
            experiments_object.get("ablation_or_sensitivity"),
        ),
        *(item for row in experiment_items for item in row["ablation_or_sensitivity"]),
    ])
    stopping = _unique_strings([
        *_first_string_list(
            experiments_object.get("stopping_or_falsification"),
            experiments_object.get("falsification_criteria"),
        ),
        *(item for row in experiment_items for item in row["stopping_or_falsification"]),
    ])
    plan["experiments"] = {
        "items": experiment_items,
        "main_experiment": {
            "objective": main_item["objective"],
            "independent_variables": main_item["variables"]["independent"],
            "dependent_variables": main_item["variables"]["dependent"],
            "control_variables": main_item["variables"]["control"],
        },
        "baselines": baselines,
        "metrics": metrics,
        "procedure": procedure,
        "ablation_or_sensitivity_analysis": ablation,
        "stopping_or_falsification": stopping,
    }
    if not plan["methods"]["overall_design"] and experiment_items:
        plan["methods"]["overall_design"] = str(
            experiment_items[0].get("design") or experiment_items[0].get("objective") or ""
        )

    results = _mapping(plan.get("results"))
    plan["results"] = {
        "result_type": str(results.get("result_type") or "expected_or_feasibility_result"),
        "expected_findings": _first_descriptive_list(
            results.get("expected_findings"), results.get("expected_results")
        ),
        "uncertainty_reporting": _descriptive_strings(results.get("uncertainty_reporting")),
        "feasibility_check": str(results.get("feasibility_check") or ""),
        "falsification_criteria": _first_string_list(
            results.get("falsification_criteria"),
            results.get("stopping_or_falsification"),
            stopping,
        ),
    }

    literature_by_id = {
        str(item.get("literature_id") or ""): dict(item)
        for item in _list(_mapping(_mapping(brief).get("upstream_evidence")).get("source_literature"))
        if isinstance(item, Mapping) and item.get("literature_id")
    }
    references = _list(plan.get("references"))
    for index, reference in enumerate(references):
        if isinstance(reference, str):
            source_id = reference.strip()
            row: dict[str, Any] = {"source_id": source_id}
            normalized.append(f"plan.references.{index}<string->object>")
        else:
            row = _mapping(reference)
        if not row.get("source_id") and row.get("literature_id"):
            row["source_id"] = row["literature_id"]
            normalized.append(f"plan.references.{index}.source_id<-literature_id")
        source_id = str(row.get("source_id") or "")
        upstream = literature_by_id.get(source_id, {})
        references[index] = {
            "source_id": source_id,
            "title": str(row.get("title") or upstream.get("title") or ""),
            "authors": _first_string_list(row.get("authors"), upstream.get("authors")),
            "year": row.get("year") or upstream.get("year") or "",
            "doi": str(row.get("doi") or upstream.get("doi") or ""),
            "url": str(row.get("url") or upstream.get("url") or ""),
            "used_for": _string_list(row.get("used_for")),
            "citation": str(row.get("citation") or ""),
        }
    plan["references"] = references

    feedback_tasks = _list(plan.get("feedback_tasks"))
    plan["feedback_tasks"] = [
        _normalize_feedback_task(item, index) for index, item in enumerate(feedback_tasks)
    ]
    plan["limitations"] = _string_list(plan.get("limitations"))
    return normalized


def _normalize_dataset(value: Any, index: int, role: str) -> dict[str, Any]:
    if isinstance(value, str):
        row: dict[str, Any] = {"name": value}
    else:
        row = _mapping(value)
    description = str(row.get("description") or "")
    return {
        "dataset_id": str(row.get("dataset_id") or row.get("id") or f"{role}_{index + 1}"),
        "name": str(row.get("name") or row.get("dataset_name") or description),
        "description": description,
        "usage": str(row.get("usage") or row.get("purpose") or ""),
        "required_fields": _first_string_list(row.get("required_fields"), row.get("fields")),
        "access_status": str(row.get("access_status") or row.get("status") or "unknown"),
        "source_hint": str(row.get("source_hint") or ""),
    }


def _normalize_method_step(value: Any, index: int) -> dict[str, Any]:
    if isinstance(value, str):
        row: dict[str, Any] = {"description": value}
    else:
        row = _mapping(value)
    step_id = str(row.get("step_id") or row.get("id") or f"M{index + 1}")
    return {
        "step_id": step_id,
        "name": str(row.get("name") or row.get("action") or step_id),
        "description": str(row.get("description") or row.get("action") or ""),
        "inputs": _first_string_list(row.get("inputs"), row.get("input")),
        "outputs": _first_string_list(row.get("outputs"), row.get("output")),
    }


def _normalize_experiment(value: Any, index: int) -> dict[str, Any]:
    row = _mapping(value)
    variables = _mapping(row.get("variables"))
    return {
        "experiment_id": str(row.get("experiment_id") or row.get("id") or f"E{index + 1}"),
        "objective": str(row.get("objective") or ""),
        "design": str(row.get("design") or ""),
        "variables": {
            "independent": _first_string_list(
                variables.get("independent"),
                row.get("independent_variables"),
                row.get("interventions"),
            ),
            "dependent": _first_string_list(
                variables.get("dependent"),
                row.get("dependent_variables"),
                row.get("outcomes"),
            ),
            "control": _first_string_list(
                variables.get("control"),
                row.get("control_variables"),
                row.get("controls"),
            ),
        },
        "baselines": _named_items(row.get("baselines")),
        "metrics": _named_items(row.get("metrics")),
        "procedure": _string_list(row.get("procedure")),
        "ablation_or_sensitivity": _first_string_list(
            row.get("ablation_or_sensitivity"),
            row.get("ablation_or_sensitivity_analysis"),
        ),
        "stopping_or_falsification": _first_string_list(
            row.get("stopping_or_falsification"), row.get("falsification_criteria")
        ),
    }


def _named_items(value: Any) -> list[dict[str, str]]:
    values = [value] if isinstance(value, (str, Mapping)) else _list(value)
    result: list[dict[str, str]] = []
    for index, item in enumerate(values):
        if isinstance(item, str):
            name, description = item.strip(), ""
        else:
            row = _mapping(item)
            name = str(
                row.get("name")
                or row.get("metric")
                or row.get("title")
                or row.get("description")
                or f"Item {index + 1}"
            )
            description = str(row.get("description") or row.get("definition") or "")
        if name:
            result.append({"name": name, "description": description})
    return result


def _dedupe_named_items(values: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in values:
        key = (item.get("name", ""), item.get("description", ""))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _normalize_feedback_task(value: Any, index: int) -> dict[str, Any]:
    if isinstance(value, str):
        row: dict[str, Any] = {"objective": value}
    else:
        row = _mapping(value)
    priority = str(row.get("priority") or "medium").lower()
    if priority not in {"high", "medium", "low"}:
        priority = "medium"
    return {
        "task_id": str(row.get("task_id") or f"feedback_{index + 1}"),
        "task_type": str(row.get("task_type") or "other"),
        "priority": priority,
        "objective": str(
            row.get("objective") or row.get("description") or row.get("expected_output") or ""
        ),
        "input_requirements": _string_list(row.get("input_requirements")),
        "expected_output": str(row.get("expected_output") or ""),
    }


def _canonical_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _clip_text(value: Any, max_chars: int) -> str:
    text = str(value or "")
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def _unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return [str(item).strip() for item in _list(value) if isinstance(item, str) and item.strip()]


def _first_string_list(*values: Any) -> list[str]:
    for value in values:
        result = _string_list(value)
        if result:
            return result
    return []


def _descriptive_strings(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        result: list[str] = []
        for key, item in value.items():
            if isinstance(item, str):
                text = item.strip()
            else:
                text = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            if text:
                result.append(f"{key}: {text}")
        return result
    return _string_list(value)


def _first_descriptive_list(*values: Any) -> list[str]:
    for value in values:
        result = _descriptive_strings(value)
        if result:
            return result
    return []
