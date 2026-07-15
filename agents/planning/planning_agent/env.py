from __future__ import annotations

import os
from pathlib import Path


_ENV_LOADED = False


def load_dotenv(path: str | Path | None = None) -> Path | None:
    """Load a small export-style .env file without overriding shell env vars."""
    global _ENV_LOADED
    env_path = Path(path) if path is not None else _find_env_file()
    if env_path is None or not env_path.exists():
        _ENV_LOADED = True
        return None

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        os.environ.setdefault(key, value)
    _ENV_LOADED = True
    return env_path


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


def _find_env_file() -> Path | None:
    candidates = [Path.cwd()]
    package_root = Path(__file__).resolve().parent.parent
    if package_root not in candidates:
        candidates.append(package_root)
    for base in candidates:
        env_path = base / ".env"
        if env_path.exists():
            return env_path
    return None


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
