from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from .contracts import TaskEvent, utc_now

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
_SAFE_RELATIVE_PART = re.compile(r"^[^<>:\"|?*\x00-\x1f]+$")
_ALLOWED_ATTACHMENT_EXTENSIONS = {".txt", ".md", ".csv", ".json"}


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
    def _replace_with_retry(source: str, destination: Path) -> None:
        delay = 0.02
        for attempt in range(8):
            try:
                os.replace(source, destination)
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(delay)
                delay *= 2

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            ArtifactService._replace_with_retry(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @staticmethod
    def _atomic_write_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            ArtifactService._replace_with_retry(temp_name, path)
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
            "title": str(
                ((context.get("user_input") or {}).get("original_question") or task_id)
            )[:120],
            "mode": context.get("mode", "auto"),
            "status": "created",
            "current_stage": "created",
            "iteration": context.get("iteration", 1),
            "archived": False,
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

    def list_tasks(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        tasks = []
        for path in sorted(self.root.iterdir(), reverse=True):
            manifest = path / "manifest.json"
            if path.is_dir() and manifest.is_file():
                item = json.loads(manifest.read_text(encoding="utf-8"))
                if include_archived or not item.get("archived", False):
                    tasks.append(item)
        tasks.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return tasks

    def set_archived(self, task_id: str, archived: bool) -> dict[str, Any]:
        return self.update_manifest(
            task_id,
            archived=archived,
            archived_at=utc_now() if archived else None,
        )

    def list_attachments(self, task_id: str) -> list[dict[str, Any]]:
        try:
            value = self.read_json(task_id, "attachments/index.json")
        except ArtifactError:
            return []
        return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def add_attachment(
        self,
        task_id: str,
        filename: str,
        content: bytes,
        media_type: str | None,
        *,
        context_char_limit: int,
        message_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        safe_name = Path(filename).name
        if safe_name != filename or not safe_name:
            raise ArtifactError("Attachment filename must not contain a path")
        extension = Path(safe_name).suffix.lower()
        if extension not in _ALLOWED_ATTACHMENT_EXTENSIONS:
            allowed = ", ".join(sorted(_ALLOWED_ATTACHMENT_EXTENSIONS))
            raise ArtifactError(f"Unsupported attachment type. Allowed: {allowed}")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ArtifactError("Attachment must be UTF-8 text") from exc

        attachment_id = f"att_{uuid4().hex[:12]}"
        stored_name = f"{attachment_id}_{safe_name}"
        relative_path = f"attachments/{stored_name}"
        path = self._resolve(task_id, relative_path, create_parent=True)
        with self._lock:
            self._atomic_write_bytes(path, content)

        item = {
            "attachment_id": attachment_id,
            "name": safe_name,
            "path": relative_path,
            "media_type": media_type or "text/plain",
            "size": len(content),
            "text_excerpt": text[:context_char_limit],
            "created_at": utc_now(),
            "message_id": message_id,
            "upload_status": "completed",
            "parse_status": "completed",
        }
        attachments = [*self.list_attachments(task_id), item]
        self.write_json(task_id, "attachments/index.json", attachments)

        context = self.load_context(task_id)
        user_input = dict(context.get("user_input") or {})
        base_description = str(
            user_input.get("base_question_description")
            or user_input.get("question_description")
            or ""
        ).strip()
        user_input["base_question_description"] = base_description
        user_input["attachments"] = [
            {key: value for key, value in attachment.items() if key != "text_excerpt"}
            for attachment in attachments
        ]
        if message_id:
            extensions = dict(context.get("extensions") or {})
            message_attachments = dict(extensions.get("message_attachments") or {})
            bound = list(message_attachments.get(message_id) or [])
            bound.append({key: value for key, value in item.items() if key != "text_excerpt"})
            message_attachments[message_id] = bound
            extensions["message_attachments"] = message_attachments
            context["extensions"] = extensions
        attachment_context = "\n\n".join(
            f"[{attachment['name']}]\n{attachment['text_excerpt']}" for attachment in attachments
        )[:context_char_limit]
        user_input["question_description"] = "\n\n".join(
            part
            for part in (
                base_description,
                f"[附件背景材料]\n{attachment_context}" if attachment_context else "",
            )
            if part
        )
        context["user_input"] = user_input
        self.save_context(task_id, context)
        self.update_manifest(task_id, attachment_count=len(attachments))
        self.append_event(
            TaskEvent(
                event_id=f"evt_{uuid4().hex[:12]}",
                task_id=task_id,
                type="attachment_uploaded",
                message=f"Attachment uploaded: {safe_name}",
                data={
                    "attachment_id": attachment_id,
                    "message_id": message_id,
                    "path": relative_path,
                },
            )
        )
        return item, context

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

    def begin_node_run(
        self,
        task_id: str,
        stage: str,
        iteration: int,
        stage_input: dict[str, Any],
    ) -> str:
        self.validate_id(stage, "stage")
        node_run_id = f"node_{uuid4().hex[:12]}"
        manifest = self.read_json(task_id, "manifest.json")
        self.write_json(
            task_id,
            f"stages/{stage}/runs/{node_run_id}/metadata.json",
            {
                "schema_version": "node_run_v1",
                "node_run_id": node_run_id,
                "workflow_run_id": manifest.get("active_run_id"),
                "task_id": task_id,
                "node_id": stage,
                "stage": stage,
                "iteration": iteration,
                "status": "running",
                "started_at": utc_now(),
                "finished_at": None,
            },
        )
        self.write_json(
            task_id,
            f"stages/{stage}/runs/{node_run_id}/input.json",
            stage_input,
        )
        return node_run_id

    def finish_node_run(
        self,
        task_id: str,
        stage: str,
        node_run_id: str,
        *,
        status: str,
        output: dict[str, Any] | None = None,
        review: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        self.validate_id(stage, "stage")
        self.validate_id(node_run_id, "node_run_id")
        metadata_path = f"stages/{stage}/runs/{node_run_id}/metadata.json"
        metadata = self.read_json(task_id, metadata_path)
        metadata.update(
            status=status,
            error=error,
            finished_at=utc_now(),
        )
        self.write_json(task_id, metadata_path, metadata)
        if output is not None:
            self.write_json(
                task_id,
                f"stages/{stage}/runs/{node_run_id}/output.json",
                output,
            )
        if review is not None:
            self.write_json(
                task_id,
                f"stages/{stage}/runs/{node_run_id}/review.json",
                review,
            )
        return metadata

    def list_node_runs(self, task_id: str, stage: str) -> list[dict[str, Any]]:
        self.validate_id(stage, "stage")
        root = self._resolve(task_id, f"stages/{stage}/runs")
        if not root.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for path in root.glob("node_*/metadata.json"):
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                records.append(value)
        return sorted(
            records,
            key=lambda item: str(item.get("started_at") or ""),
            reverse=True,
        )

    def get_node_run(
        self, task_id: str, stage: str, node_run_id: str
    ) -> dict[str, Any]:
        self.validate_id(stage, "stage")
        self.validate_id(node_run_id, "node_run_id")
        base = f"stages/{stage}/runs/{node_run_id}"
        result = {
            "metadata": self.read_json(task_id, f"{base}/metadata.json"),
            "input": self.read_json(task_id, f"{base}/input.json"),
            "output": None,
            "review": None,
        }
        for key in ("output", "review"):
            path = self._resolve(task_id, f"{base}/{key}.json")
            if path.is_file():
                result[key] = json.loads(path.read_text(encoding="utf-8"))
        return result

    def list_stage_history(
        self, task_id: str, stages: tuple[str, ...] | list[str]
    ) -> list[dict[str, Any]]:
        stage_order = {stage: index for index, stage in enumerate(stages)}
        history: list[dict[str, Any]] = []
        for stage in stages:
            seen_iterations: set[int] = set()
            for metadata in self.list_node_runs(task_id, stage):
                iteration = int(metadata.get("iteration") or 1)
                if iteration in seen_iterations:
                    continue
                detail = self.get_node_run(
                    task_id, stage, str(metadata["node_run_id"])
                )
                if detail.get("output") is None:
                    continue
                seen_iterations.add(iteration)
                history.append(detail)
        return sorted(
            history,
            key=lambda item: (
                int((item.get("metadata") or {}).get("iteration") or 1),
                stage_order.get(str((item.get("metadata") or {}).get("stage")), 999),
            ),
        )

    def node_run_diff(
        self, task_id: str, stage: str, left: str, right: str
    ) -> dict[str, Any]:
        left_value = self.get_node_run(task_id, stage, left).get("output") or {}
        right_value = self.get_node_run(task_id, stage, right).get("output") or {}
        left_flat = self._flatten(left_value)
        right_flat = self._flatten(right_value)
        keys = sorted(set(left_flat) | set(right_flat))
        changes = [
            {"path": key, "before": left_flat.get(key), "after": right_flat.get(key)}
            for key in keys
            if left_flat.get(key) != right_flat.get(key)
        ]
        return {"left": left, "right": right, "change_count": len(changes), "changes": changes}

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
