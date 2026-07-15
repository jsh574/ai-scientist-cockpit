from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any

from .contracts import TaskEvent, utc_now

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
_SAFE_RELATIVE_PART = re.compile(r"^[^<>:\"|?*\x00-\x1f]+$")


class ArtifactError(RuntimeError):
    pass


class ArtifactService:
    """Task-scoped, path-safe filesystem storage used by REST and MCP."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @staticmethod
    def validate_id(value: str, kind: str = "identifier") -> str:
        if not _SAFE_ID.fullmatch(value):
            raise ArtifactError(f"Unsafe {kind}: {value!r}")
        return value

    def task_root(self, task_id: str, *, create: bool = False) -> Path:
        self.validate_id(task_id, "task_id")
        path = (self.root / task_id).resolve()
        if path.parent != self.root:
            raise ArtifactError("Task path escaped the artifact root")
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def _resolve(self, task_id: str, relative_path: str, *, create_parent: bool = False) -> Path:
        task_root = self.task_root(task_id, create=create_parent)
        relative = Path(relative_path.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ArtifactError("Artifact path must be task-relative")
        if not relative.parts or any(
            not _SAFE_RELATIVE_PART.fullmatch(part) for part in relative.parts
        ):
            raise ArtifactError("Artifact path contains an unsafe component")
        path = (task_root / relative).resolve()
        if task_root not in path.parents:
            raise ArtifactError("Artifact path escaped the task directory")
        if create_parent:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def write_json(self, task_id: str, relative_path: str, value: Any) -> Path:
        path = self._resolve(task_id, relative_path, create_parent=True)
        content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
        with self._lock:
            self._atomic_write(path, content)
        return path

    def read_json(self, task_id: str, relative_path: str) -> Any:
        path = self._resolve(task_id, relative_path)
        if not path.is_file():
            raise ArtifactError(f"Artifact does not exist: {relative_path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def read_text(self, task_id: str, relative_path: str, max_bytes: int = 1_000_000) -> str:
        path = self._resolve(task_id, relative_path)
        if not path.is_file():
            raise ArtifactError(f"Artifact does not exist: {relative_path}")
        if path.stat().st_size > max_bytes:
            raise ArtifactError(f"Artifact exceeds {max_bytes} bytes")
        return path.read_text(encoding="utf-8")

    def write_note(self, task_id: str, name: str, content: str) -> str:
        safe_name = Path(name).name
        if safe_name != name or not safe_name.endswith(".md"):
            raise ArtifactError("Notes must be a simple .md filename")
        path = self._resolve(task_id, f"notes/{safe_name}", create_parent=True)
        with self._lock:
            self._atomic_write(path, content.rstrip() + "\n")
        return path.relative_to(self.task_root(task_id)).as_posix()

    def create_task(self, context: dict[str, Any]) -> None:
        task_id = str(context["task_id"])
        task_root = self.task_root(task_id, create=True)
        if (task_root / "manifest.json").exists():
            raise ArtifactError(f"Task already exists: {task_id}")
        manifest = {
            "task_id": task_id,
            "mode": context.get("mode", "auto"),
            "status": "created",
            "current_stage": "created",
            "iteration": context.get("iteration", 1),
            "stage_status": {},
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        self.write_json(task_id, "manifest.json", manifest)
        self.write_json(task_id, "context/task_context.latest.json", context)
        self.append_event(
            TaskEvent(
                event_id="evt_000001",
                task_id=task_id,
                type="task_created",
                message="Task context created.",
            )
        )

    def task_exists(self, task_id: str) -> bool:
        try:
            return (self.task_root(task_id) / "manifest.json").is_file()
        except ArtifactError:
            return False

    def list_tasks(self) -> list[dict[str, Any]]:
        tasks = []
        for path in sorted(self.root.iterdir(), reverse=True):
            manifest = path / "manifest.json"
            if path.is_dir() and manifest.is_file():
                tasks.append(json.loads(manifest.read_text(encoding="utf-8")))
        return tasks

    def load_context(self, task_id: str) -> dict[str, Any]:
        value = self.read_json(task_id, "context/task_context.latest.json")
        if not isinstance(value, dict):
            raise ArtifactError("Stored task context is invalid")
        return value

    def save_context(self, task_id: str, context: dict[str, Any]) -> None:
        self.write_json(task_id, "context/task_context.latest.json", context)

    def update_manifest(self, task_id: str, **patch: Any) -> dict[str, Any]:
        manifest = self.read_json(task_id, "manifest.json")
        manifest.update(patch)
        manifest["updated_at"] = utc_now()
        self.write_json(task_id, "manifest.json", manifest)
        return manifest

    def set_stage_status(self, task_id: str, stage: str, status: str) -> None:
        self.validate_id(stage, "stage")
        manifest = self.read_json(task_id, "manifest.json")
        stage_status = dict(manifest.get("stage_status") or {})
        stage_status[stage] = status
        self.update_manifest(
            task_id,
            stage_status=stage_status,
            current_stage=stage,
            status=status,
        )

    def write_stage_input(self, task_id: str, stage: str, iteration: int, value: Any) -> Path:
        self.validate_id(stage, "stage")
        return self.write_json(task_id, f"stages/{stage}/i{iteration:03d}.input.json", value)

    def write_stage_output(self, task_id: str, stage: str, iteration: int, value: Any) -> Path:
        self.validate_id(stage, "stage")
        path = self.write_json(task_id, f"stages/{stage}/i{iteration:03d}.output.json", value)
        self.write_json(task_id, f"stages/{stage}/latest.output.json", value)
        return path

    def write_review(self, task_id: str, stage: str, iteration: int, value: Any) -> Path:
        self.validate_id(stage, "stage")
        path = self.write_json(task_id, f"reviews/{stage}.i{iteration:03d}.review.json", value)
        self.write_json(task_id, f"reviews/{stage}.latest.review.json", value)
        return path

    def latest_stage_output(self, task_id: str, stage: str) -> dict[str, Any]:
        value = self.read_json(task_id, f"stages/{stage}/latest.output.json")
        if not isinstance(value, dict):
            raise ArtifactError("Stored stage output is invalid")
        return value

    def append_event(self, event: TaskEvent) -> None:
        path = self._resolve(event.task_id, "events/trace.jsonl", create_parent=True)
        line = event.model_dump_json() + "\n"
        with self._lock, path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)

    def read_events(self, task_id: str) -> list[dict[str, Any]]:
        path = self._resolve(task_id, "events/trace.jsonl")
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def snapshot(
        self,
        task_id: str,
        context: dict[str, Any],
        *,
        stage: str,
        trigger: str,
        changed_fields: list[str],
    ) -> dict[str, Any]:
        versions = list(context.get("versions") or [])
        version_id = f"v{int(context.get('iteration') or 1):03d}-{len(versions) + 1:03d}"
        record = {
            "version_id": version_id,
            "iteration": int(context.get("iteration") or 1),
            "stage": stage,
            "trigger": trigger,
            "changed_fields": changed_fields,
            "summary": f"{stage}: {', '.join(changed_fields) or 'context'}",
            "artifact_path": f"versions/{version_id}/task_context.json",
            "created_at": utc_now(),
        }
        context["versions"] = [*versions, record]
        self.write_json(task_id, record["artifact_path"], context)
        self.write_json(task_id, f"versions/{version_id}/metadata.json", record)
        self.save_context(task_id, context)
        return record

    def list_versions(self, task_id: str) -> list[dict[str, Any]]:
        return list(self.load_context(task_id).get("versions") or [])

    @staticmethod
    def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, child in value.items():
                child_prefix = f"{prefix}.{key}" if prefix else str(key)
                result.update(ArtifactService._flatten(child, child_prefix))
            return result
        return {prefix: value}

    def version_diff(self, task_id: str, left: str, right: str) -> dict[str, Any]:
        self.validate_id(left, "version_id")
        self.validate_id(right, "version_id")
        left_value = self.read_json(task_id, f"versions/{left}/task_context.json")
        right_value = self.read_json(task_id, f"versions/{right}/task_context.json")
        left_flat = self._flatten(left_value)
        right_flat = self._flatten(right_value)
        keys = sorted(set(left_flat) | set(right_flat))
        changes = [
            {"path": key, "before": left_flat.get(key), "after": right_flat.get(key)}
            for key in keys
            if left_flat.get(key) != right_flat.get(key)
        ]
        return {"left": left, "right": right, "change_count": len(changes), "changes": changes}

    def list_artifacts(self, task_id: str) -> list[dict[str, Any]]:
        root = self.task_root(task_id)
        artifacts = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or "exports" in path.relative_to(root).parts:
                continue
            artifacts.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": path.stat().st_size,
                    "updated_at": path.stat().st_mtime,
                }
            )
        return artifacts

    def export_task(self, task_id: str) -> Path:
        root = self.task_root(task_id)
        destination = self._resolve(task_id, f"exports/{task_id}.zip", create_parent=True)
        with self._lock, zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob("*")):
                relative = path.relative_to(root)
                if path.is_file() and "exports" not in relative.parts:
                    archive.write(path, relative.as_posix())
        return destination
