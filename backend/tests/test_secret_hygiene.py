from __future__ import annotations

import re
from pathlib import Path

SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{16,}"),
)
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".json", ".md", ".yml", ".yaml", ".ps1"}
IGNORED_PARTS = {".git", ".venv", "node_modules", "dist", "artifacts", "logs"}


def test_repository_contains_no_committable_model_credentials() -> None:
    root = Path(__file__).resolve().parents[2]
    offending_files: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in IGNORED_PARTS for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            offending_files.append(path.relative_to(root).as_posix())
    assert offending_files == [], f"Credential-like values found in: {offending_files}"


def test_environment_files_are_git_ignored() -> None:
    gitignore = Path(__file__).resolve().parents[2] / ".gitignore"
    rules = gitignore.read_text(encoding="utf-8")
    assert ".env" in rules
