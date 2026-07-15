# Source Alignment

EurekaLoop is intentionally aligned with the three local planning documents.

## Data Contract

From `数据规范_v0.1.md`:

- `task_context` remains the central object owned by the controller.
- Each Agent reads only the sliced fields it needs.
- Every Agent output uses the unified `metadata / payload / self_review` shell.
- The controller validates a module output before merging its `payload` into `task_context`.
- The UI exposes the important output objects: `question_card`, `literature_cards`, `evidence_cards`, `knowledge_gaps`, `hypothesis_cards`, `evidence_map`, `research_plan`, and `final_review`.

## Orchestrator And Frontend Design

From `总控层与前端设计方案v0.1.md`:

- The side state tree represents the explicit workflow state machine without occupying the main work area.
- Clicking a branch node opens the full visual state tree for demo and debugging.
- Review Gate is now inline inside the related Agent message, so approval and revision happen where the output is read.
- Revision feedback is recorded as a user message and reruns the corresponding module.
- The conversation thread becomes the main Stage Inspector: every module output is visible in order, with JSON details available on demand.
- The thin message index rail supports jumping between question, module outputs, revisions, and final output.
- The project sidebar keeps separate frontend sessions so each project can preserve its own conversation, current stage, drafts, files, and Review Gate state.
- The full visual state tree now separates the six main workflow stages from each stage's writable artifacts and output-derived detail nodes.
- State-tree branches now show concrete previews derived from each Agent `payload`, such as core questions, literature titles, hypothesis statements, evidence strength, research-plan methods, and final review scores.

## Competition Alignment

From `赛题文档.md`:

- The demo highlights a complete scientific loop from question understanding to final research plan review.
- It shows representative input and output for every module, which can be reused in the final PPT/PDF.
- It demonstrates human feedback and rerun behavior rather than only a one-shot generated answer.
- It keeps the future API, artifact, event stream, and deployment path explicit for later implementation.
- Backend-facing interactions are tracked in `docs/backend-integration-guide.md` and should be updated whenever project, review, file, or state-tree behavior changes.
