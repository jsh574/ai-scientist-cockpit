from __future__ import annotations

import os
from pathlib import Path

_ENV_LOADED = False


def load_dotenv(path: str | Path | None = None) -> Path | None:
    """Load export-style env files without overriding higher-priority values."""
    global _ENV_LOADED
    env_paths = [Path(path)] if path is not None else _find_env_files()
    first_loaded: Path | None = None
    for env_path in env_paths:
        if not env_path.is_file():
            continue
        if first_loaded is None:
            first_loaded = env_path
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            parsed = _parse_env_line(raw_line)
            if parsed is None:
                continue
            key, value = parsed
            os.environ.setdefault(key, value)
    _ENV_LOADED = True
    return first_loaded


def ensure_dotenv_loaded() -> None:
    if os.getenv("PLANNING_AGENT_SKIP_DOTENV", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    if not _ENV_LOADED:
        load_dotenv()


def _find_env_files() -> list[Path]:
    package_root = Path(__file__).resolve().parent.parent
    project_root = package_root.parents[1]
    candidates = [
        project_root / ".env",
        package_root / ".env",
        project_root / "backend" / ".env",
        Path.cwd() / ".env",
    ]
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()
    if "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
        return None
    return key, _strip_inline_comment(_strip_quotes(value.strip()))


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _strip_inline_comment(value: str) -> str:
    if value.startswith(("'", '"')):
        return value
    marker = " #"
    if marker in value:
        return value.split(marker, 1)[0].rstrip()
    return value
