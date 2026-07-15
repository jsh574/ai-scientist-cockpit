# Planning Agent Engineering Guide

This file documents the local contract for maintaining the Planning Agent. The
repository-level Agent protocol remains authoritative for integrated runs.

## Dify Workflow DSL 规则

- Keep the real node kind in `data.type`; outer graph nodes normally remain
  `type: custom`.
- Every edge must keep `sourceType` and `targetType` aligned with the connected
  nodes' `data.type` values.
- Use array selectors such as `value_selector: [node_id, field]` and
  `variable_selector: [node_id, field]` for cross-node values.
- The workflow accepts one `hypothesis_evidence_package` per invocation. The
  Python wrapper owns multi-hypothesis selection and aggregation.
- The end node must expose the result as `plan_result`.
- Do not hand-write a YAML anchor/alias in the primary exported workflow. Keep
  the fully expanded format produced by Dify for import compatibility.

## Runtime Contract

- Preserve the Agent's validation, ranking, aggregation, and traceability
  guards when adapting its transport.
- Integrated runs receive credentials, model, and base URL from the root
  environment. Never commit `.env` or API keys.
- Run `python -m pytest` from this directory after changing the Agent.
