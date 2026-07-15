from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AGENTS_ROOT = PROJECT_ROOT / "agents"


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(PROJECT_ROOT / ".env")
_load_env_file(PROJECT_ROOT / "backend" / ".env")


def _resolve_project_path(value: str | None, default: Path) -> Path:
    path = Path(value) if value else default
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True)
class Settings:
    problem_agent_root: Path
    knowledge_agent_root: Path
    hypothesis_agent_file: Path
    evidence_agent_root: Path
    planning_agent_root: Path
    artifacts_root: Path
    review_threshold: float
    max_iterations: int
    cors_origins: tuple[str, ...]

    @classmethod
    def from_env(cls) -> Settings:
        agents_root = _resolve_project_path(os.getenv("AGENTS_ROOT"), DEFAULT_AGENTS_ROOT)
        return cls(
            problem_agent_root=_resolve_project_path(
                os.getenv("PROBLEM_AGENT_ROOT"), agents_root / "problem_understanding"
            ),
            knowledge_agent_root=_resolve_project_path(
                os.getenv("KNOWLEDGE_AGENT_ROOT"), agents_root / "knowledge_integration"
            ),
            hypothesis_agent_file=_resolve_project_path(
                os.getenv("HYPOTHESIS_AGENT_FILE"),
                agents_root / "hypothesis_generation" / "hypothesis_generation_agent.py",
            ),
            evidence_agent_root=_resolve_project_path(
                os.getenv("EVIDENCE_AGENT_ROOT"), agents_root / "evidence_mapping"
            ),
            planning_agent_root=_resolve_project_path(
                os.getenv("PLANNING_AGENT_ROOT"), agents_root / "planning"
            ),
            artifacts_root=_resolve_project_path(
                os.getenv("ARTIFACTS_ROOT"), PROJECT_ROOT / "artifacts" / "tasks"
            ),
            review_threshold=float(os.getenv("REVIEW_GATE_THRESHOLD", "0.75")),
            max_iterations=int(os.getenv("MAX_WORKFLOW_ITERATIONS", "3")),
            cors_origins=tuple(
                origin.strip()
                for origin in os.getenv(
                    "CORS_ALLOWED_ORIGINS",
                    "http://localhost:5173,http://127.0.0.1:5173",
                ).split(",")
                if origin.strip()
            ),
        )

    def source_status(self) -> dict[str, dict[str, object]]:
        return {
            "question_understanding": {
                "path": str(self.problem_agent_root),
                "available": self.problem_agent_root.is_dir(),
            },
            "knowledge_integration": {
                "path": str(self.knowledge_agent_root),
                "available": self.knowledge_agent_root.is_dir(),
                "credential_configured": bool(
                    os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY")
                ),
            },
            "hypothesis_generation": {
                "path": str(self.hypothesis_agent_file),
                "available": self.hypothesis_agent_file.is_file(),
                "credential_configured": bool(os.getenv("DASHSCOPE_API_KEY")),
            },
            "evidence_mapping": {
                "path": str(self.evidence_agent_root),
                "available": (self.evidence_agent_root / "src" / "evidence_mapping").is_dir(),
                "mode": "rule_engine",
            },
            "research_planning": {
                "path": str(self.planning_agent_root),
                "available": self.planning_agent_root.is_dir(),
                "credential_configured": bool(
                    os.getenv("DASHSCOPE_API_KEY")
                    or os.getenv("QWEN_API_KEY")
                    or os.getenv("LLM_API_KEY")
                ),
            },
            "artifact_service": {
                "path": str(self.artifacts_root),
                "available": self.artifacts_root.parent.exists(),
                "mode": "filesystem_mcp",
            },
        }
