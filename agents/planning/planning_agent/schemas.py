from __future__ import annotations

from typing import Any

STRING_ARRAY: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
OBJECT_ARRAY: dict[str, Any] = {"type": "array", "items": {"type": "object"}}

NAMED_ITEM: dict[str, Any] = {
    "type": "object",
    "required": ["name", "description"],
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
    },
}

DATASET_ITEM: dict[str, Any] = {
    "type": "object",
    "required": [
        "dataset_id",
        "name",
        "description",
        "usage",
        "required_fields",
        "access_status",
    ],
    "properties": {
        "dataset_id": {"type": "string"},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "usage": {"type": "string"},
        "required_fields": STRING_ARRAY,
        "access_status": {"type": "string"},
        "source_hint": {"type": "string"},
    },
}

METHOD_STEP: dict[str, Any] = {
    "type": "object",
    "required": ["step_id", "name", "description", "inputs", "outputs"],
    "properties": {
        "step_id": {"type": "string"},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "inputs": STRING_ARRAY,
        "outputs": STRING_ARRAY,
    },
}

VARIABLES: dict[str, Any] = {
    "type": "object",
    "required": ["independent", "dependent", "control"],
    "properties": {
        "independent": STRING_ARRAY,
        "dependent": STRING_ARRAY,
        "control": STRING_ARRAY,
    },
}

EXPERIMENT_ITEM: dict[str, Any] = {
    "type": "object",
    "required": [
        "objective",
        "design",
        "variables",
        "baselines",
        "metrics",
        "procedure",
        "ablation_or_sensitivity",
        "stopping_or_falsification",
    ],
    "properties": {
        "experiment_id": {"type": "string"},
        "objective": {"type": "string"},
        "design": {"type": "string"},
        "variables": VARIABLES,
        "baselines": {"type": "array", "items": NAMED_ITEM},
        "metrics": {"type": "array", "items": NAMED_ITEM},
        "procedure": STRING_ARRAY,
        "ablation_or_sensitivity": STRING_ARRAY,
        "stopping_or_falsification": STRING_ARRAY,
    },
}

LOGIC_CHAIN_ARRAY: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["claim", "evidence_ids", "source_ids"],
        "properties": {
            "claim": {"type": "string"},
            "evidence_ids": STRING_ARRAY,
            "source_ids": STRING_ARRAY,
        },
    },
}

REFERENCE_ARRAY: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["source_id", "title", "authors", "year", "doi", "url", "used_for"],
        "properties": {
            "source_id": {"type": "string", "minLength": 1},
            "title": {"type": "string"},
            "authors": STRING_ARRAY,
            "year": {"type": ["string", "integer", "number"]},
            "doi": {"type": "string"},
            "url": {"type": "string"},
            "used_for": STRING_ARRAY,
            "citation": {"type": "string"},
        },
    },
}

FEEDBACK_TASK_ARRAY: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "required": [
            "task_id",
            "task_type",
            "priority",
            "objective",
            "input_requirements",
            "expected_output",
        ],
        "properties": {
            "task_id": {"type": "string"},
            "task_type": {"type": "string"},
            "priority": {"enum": ["high", "medium", "low"]},
            "objective": {"type": "string", "minLength": 1},
            "input_requirements": STRING_ARRAY,
            "expected_output": {"type": "string"},
        },
    },
}

PLAN_PROPERTIES: dict[str, Any] = {
    "problem_statement": {"type": "string"},
    "rationale": {
        "type": "object",
        "required": ["text", "logic_chain"],
        "properties": {
            "text": {"type": "string"},
            "logic_chain": LOGIC_CHAIN_ARRAY,
        },
    },
    "technical_details": {
        "type": "object",
        "required": [
            "required_methods",
            "candidate_models_or_algorithms",
            "statistical_tests",
            "software_stack",
            "reproducibility_settings",
        ],
        "properties": {
            "required_methods": STRING_ARRAY,
            "candidate_models_or_algorithms": STRING_ARRAY,
            "statistical_tests": STRING_ARRAY,
            "software_stack": STRING_ARRAY,
            "reproducibility_settings": STRING_ARRAY,
        },
    },
    "datasets": {
        "type": "object",
        "required": ["source", "target"],
        "properties": {
            "source": {"type": "array", "items": DATASET_ITEM},
            "target": {"type": "array", "items": DATASET_ITEM},
        },
    },
    "paper_title": {"type": "string"},
    "paper_abstract": {"type": "string"},
    "methods": {
        "type": "object",
        "required": ["overall_design", "steps"],
        "properties": {
            "overall_design": {"type": "string"},
            "steps": {"type": "array", "items": METHOD_STEP},
        },
    },
    "experiments": {
        "type": "object",
        "required": [
            "items",
            "main_experiment",
            "baselines",
            "metrics",
            "procedure",
            "ablation_or_sensitivity_analysis",
            "stopping_or_falsification",
        ],
        "properties": {
            "items": {"type": "array", "items": EXPERIMENT_ITEM},
            "main_experiment": {
                "type": "object",
                "required": [
                    "objective",
                    "independent_variables",
                    "dependent_variables",
                    "control_variables",
                ],
                "properties": {
                    "objective": {"type": "string"},
                    "independent_variables": STRING_ARRAY,
                    "dependent_variables": STRING_ARRAY,
                    "control_variables": STRING_ARRAY,
                },
            },
            "baselines": {"type": "array", "items": NAMED_ITEM},
            "metrics": {"type": "array", "items": NAMED_ITEM},
            "procedure": STRING_ARRAY,
            "ablation_or_sensitivity_analysis": STRING_ARRAY,
            "stopping_or_falsification": STRING_ARRAY,
        },
    },
    "results": {
        "type": "object",
        "required": [
            "result_type",
            "expected_findings",
            "uncertainty_reporting",
            "feasibility_check",
            "falsification_criteria",
        ],
        "properties": {
            "result_type": {"type": "string"},
            "expected_findings": STRING_ARRAY,
            "uncertainty_reporting": STRING_ARRAY,
            "feasibility_check": {"type": "string"},
            "falsification_criteria": STRING_ARRAY,
        },
    },
    "references": REFERENCE_ARRAY,
    "feedback_tasks": FEEDBACK_TASK_ARRAY,
    "limitations": STRING_ARRAY,
}
PLAN_REQUIRED = list(PLAN_PROPERTIES)
PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": PLAN_REQUIRED,
    "properties": PLAN_PROPERTIES,
}

PROTOCOL_DRAFT_SCHEMA: dict[str, Any] = {
    "$id": "planning_protocol_draft_v1",
    "type": "object",
    "required": [
        "schema_version",
        "hypothesis_id",
        "status",
        "protocol",
        "assumptions",
        "unresolved_gaps",
    ],
    "properties": {
        "schema_version": {"const": "planning_protocol_draft_v1"},
        "hypothesis_id": {"type": "string", "minLength": 1},
        "status": {"enum": ["success", "partial_success"]},
        "protocol": PLAN_SCHEMA,
        "assumptions": STRING_ARRAY,
        "unresolved_gaps": STRING_ARRAY,
    },
}

REVIEW_ROLES = ("methodology", "statistics", "feasibility")
PROTOCOL_REVIEW_SCHEMA: dict[str, Any] = {
    "$id": "planning_protocol_review_v1",
    "type": "object",
    "required": [
        "schema_version",
        "hypothesis_id",
        "review_role",
        "verdict",
        "summary",
        "strengths",
        "issues",
    ],
    "properties": {
        "schema_version": {"const": "planning_protocol_review_v1"},
        "hypothesis_id": {"type": "string", "minLength": 1},
        "review_role": {"enum": list(REVIEW_ROLES)},
        "verdict": {"enum": ["pass", "revise", "blocked"]},
        "summary": {"type": "string"},
        "strengths": STRING_ARRAY,
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "issue_id",
                    "severity",
                    "category",
                    "description",
                    "required_change",
                    "evidence_ids",
                    "source_ids",
                ],
                "properties": {
                    "issue_id": {"type": "string"},
                    "severity": {"enum": ["critical", "major", "minor"]},
                    "category": {"type": "string"},
                    "description": {"type": "string"},
                    "required_change": {"type": "string"},
                    "evidence_ids": STRING_ARRAY,
                    "source_ids": STRING_ARRAY,
                },
            },
        },
    },
}

FINAL_PLAN_SCHEMA: dict[str, Any] = {
    "$id": "experiment_planner_plan_result_v1",
    "type": "object",
    "required": [
        "schema_version",
        "agent_name",
        "task_id",
        "iteration",
        "hypothesis_id",
        "status",
        "error_message",
        "plan",
    ],
    "properties": {
        "schema_version": {"const": "experiment_planner_plan_result_v1"},
        "agent_name": {"const": "ExperimentPlannerAgent"},
        "task_id": {"type": "string"},
        "iteration": {"type": "integer"},
        "hypothesis_id": {"type": "string"},
        "status": {"enum": ["success", "partial_success", "failed"]},
        "error_message": {"type": "string"},
        "plan": PLAN_SCHEMA,
    },
}


def schema_issues(instance: Any, schema: dict[str, Any]) -> list[str]:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - packaging regression guard
        raise RuntimeError("jsonschema is required by the Planning Agent") from exc
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path))
    return [
        f"{'.'.join(str(value) for value in error.absolute_path) or '<root>'}: {error.message}"
        for error in errors
    ]
