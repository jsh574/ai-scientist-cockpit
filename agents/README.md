# Integrated Agents

This directory contains the source snapshots required by the unified cockpit backend.
The application does not depend on sibling repositories or machine-specific absolute paths.

| Stage | Source directory | Runtime entry | Model mode |
|---|---|---|---|
| Question understanding | `problem_understanding/` | `problem_understanding.agent.ProblemUnderstandingAgent` | Shared Qwen client |
| Knowledge integration | `knowledge_integration/` | `knowledge_integration_agent.KnowledgeIntegrationAgent` | Shared Qwen client |
| Hypothesis generation | `hypothesis_generation/` | `HypothesisGenerationAgent` | Shared Qwen client |
| Evidence mapping | `evidence_mapping/` | `evidence_mapping.EvidenceMappingAgent` | Local rule engine |
| Research planning | `planning/` | `planning_agent.service.run_planning_agent` | Shared Qwen client |

The backend adapters in `backend/app/adapters.py` normalize the different native
contracts into the common `metadata / payload / self_review` response envelope.

## Updating a snapshot

1. Copy the teammate's source changes into the matching directory.
2. Never copy `.git`, `.venv`, `.env`, cache folders, test artifacts, or local logs.
3. Run the integrated adapter tests and the source Agent's own tests.
4. Confirm `GET /api/health` reports all five real stages as available.

API credentials belong only in the repository-level ignored `backend/.env` file.

## Tests

Install the development dependencies once:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
```

Then run the integrated contract tests from the repository root:

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_adapters -v
```

Agent-native tests that use package-level imports should be run from that Agent's
directory, for example `cd agents/planning` followed by
`..\..\.venv\Scripts\python.exe -m pytest -q`.
