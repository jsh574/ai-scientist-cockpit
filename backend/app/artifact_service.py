from __future__ import annotations

import json
import hashlib
import os
import re
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from uuid import uuid4

from .contracts import TaskEvent, utc_now

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
_SAFE_RELATIVE_PART = re.compile(r"^[^<>:\"|?*\x00-\x1f]+$")
_ALLOWED_ATTACHMENT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
}
_TEXT_ATTACHMENT_EXTENSIONS = {".txt", ".md", ".csv", ".json"}
_LEGACY_OFFICE_EXTENSIONS = {".doc", ".ppt", ".xls"}
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}")


def allowed_attachment_extensions() -> list[str]:
    return sorted(_ALLOWED_ATTACHMENT_EXTENSIONS)


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

    @staticmethod
    def _xml_text(content: bytes) -> str:
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError:
            return ""
        parts = [
            node.text.strip()
            for node in root.iter()
            if node.text and node.text.strip()
        ]
        return "\n".join(parts)

    @staticmethod
    def _chunks(text: str, *, chunk_size: int = 1800) -> list[dict[str, Any]]:
        compact = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not compact:
            return []
        return [
            {"chunk_id": f"chunk_{index + 1:03d}", "text": compact[start:start + chunk_size]}
            for index, start in enumerate(range(0, len(compact), chunk_size))
        ]

    @staticmethod
    def _search_terms(text: str) -> set[str]:
        return {
            token.lower()
            for token in _TOKEN_PATTERN.findall(text)
            if len(token.strip()) >= 2
        }

    @staticmethod
    def _chunk_score(query_terms: set[str], chunk_text: str) -> float:
        chunk_terms = ArtifactService._search_terms(chunk_text)
        if not chunk_terms:
            return 0.0
        if not query_terms:
            return 0.01
        overlap = query_terms & chunk_terms
        if not overlap:
            return 0.0
        density = len(overlap) / max(1, len(query_terms))
        coverage = len(overlap) / max(1, len(chunk_terms))
        return round(density + coverage, 6)

    @staticmethod
    def _parse_text_attachment(content: bytes, extension: str) -> tuple[str, dict[str, Any]]:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ArtifactError("Attachment must be UTF-8 text") from exc
        parsed: dict[str, Any] = {
            "metadata": {"parser": "utf8_text", "file_type": extension.lstrip(".")},
            "sections": [{"section_id": "text", "title": "Text", "text": text}],
            "pages": [],
            "tables": [],
            "images": [],
            "chunks": ArtifactService._chunks(text),
        }
        return text, parsed

    @staticmethod
    def _parse_docx(content: bytes) -> tuple[str, dict[str, Any]]:
        with tempfile.SpooledTemporaryFile() as handle:
            handle.write(content)
            handle.seek(0)
            with zipfile.ZipFile(handle) as archive:
                text = ArtifactService._xml_text(archive.read("word/document.xml"))
        parsed = {
            "metadata": {"parser": "zip_xml", "file_type": "docx"},
            "sections": [{"section_id": "document", "title": "Document", "text": text}],
            "pages": [],
            "tables": [],
            "images": [],
            "chunks": ArtifactService._chunks(text),
        }
        return text, parsed

    @staticmethod
    def _parse_pptx(content: bytes) -> tuple[str, dict[str, Any]]:
        with tempfile.SpooledTemporaryFile() as handle:
            handle.write(content)
            handle.seek(0)
            with zipfile.ZipFile(handle) as archive:
                slide_names = sorted(
                    name
                    for name in archive.namelist()
                    if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
                )
                pages = []
                texts = []
                for index, name in enumerate(slide_names, start=1):
                    slide_text = ArtifactService._xml_text(archive.read(name))
                    texts.append(slide_text)
                    pages.append({"page": index, "text": slide_text})
        text = "\n\n".join(part for part in texts if part)
        parsed = {
            "metadata": {"parser": "zip_xml", "file_type": "pptx", "slide_count": len(pages)},
            "sections": [{"section_id": "slides", "title": "Slides", "text": text}],
            "pages": pages,
            "tables": [],
            "images": [],
            "chunks": ArtifactService._chunks(text),
        }
        return text, parsed

    @staticmethod
    def _parse_xlsx(content: bytes) -> tuple[str, dict[str, Any]]:
        with tempfile.SpooledTemporaryFile() as handle:
            handle.write(content)
            handle.seek(0)
            with zipfile.ZipFile(handle) as archive:
                shared_strings: list[str] = []
                if "xl/sharedStrings.xml" in archive.namelist():
                    shared_strings = ArtifactService._xml_text(
                        archive.read("xl/sharedStrings.xml")
                    ).splitlines()
                sheet_names = sorted(
                    name
                    for name in archive.namelist()
                    if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
                )
                tables = []
                texts = []
                for index, name in enumerate(sheet_names, start=1):
                    raw = ArtifactService._xml_text(archive.read(name)).splitlines()
                    values = [
                        shared_strings[int(value)]
                        if value.isdigit() and int(value) < len(shared_strings)
                        else value
                        for value in raw
                    ]
                    sheet_text = "\n".join(value for value in values if value)
                    texts.append(sheet_text)
                    tables.append({"table_id": f"sheet_{index}", "name": Path(name).stem, "text": sheet_text})
        text = "\n\n".join(part for part in texts if part)
        parsed = {
            "metadata": {"parser": "zip_xml", "file_type": "xlsx", "sheet_count": len(tables)},
            "sections": [{"section_id": "workbook", "title": "Workbook", "text": text}],
            "pages": [],
            "tables": tables,
            "images": [],
            "chunks": ArtifactService._chunks(text),
        }
        return text, parsed

    @staticmethod
    def _parse_pdf(content: bytes) -> tuple[str, dict[str, Any]]:
        text = ""
        pages: list[dict[str, Any]] = []
        try:
            from pypdf import PdfReader  # type: ignore

            with tempfile.SpooledTemporaryFile() as handle:
                handle.write(content)
                handle.seek(0)
                reader = PdfReader(handle)
                for index, page in enumerate(reader.pages, start=1):
                    page_text = page.extract_text() or ""
                    pages.append({"page": index, "text": page_text})
                text = "\n\n".join(page["text"] for page in pages if page["text"])
            parser = "pypdf"
        except Exception:
            parser = "pdf_byte_fallback"
            decoded = content.decode("latin-1", errors="ignore")
            text = "\n".join(re.findall(r"\(([^()]{1,200})\)", decoded))
        parsed = {
            "metadata": {"parser": parser, "file_type": "pdf", "page_count": len(pages)},
            "sections": [{"section_id": "pdf_text", "title": "PDF text", "text": text}],
            "pages": pages,
            "tables": [],
            "images": [],
            "chunks": ArtifactService._chunks(text),
        }
        return text, parsed

    @staticmethod
    def _parse_attachment(
        content: bytes, extension: str
    ) -> tuple[str, dict[str, Any], str | None]:
        try:
            if extension in _TEXT_ATTACHMENT_EXTENSIONS:
                text, parsed = ArtifactService._parse_text_attachment(content, extension)
            elif extension == ".docx":
                text, parsed = ArtifactService._parse_docx(content)
            elif extension == ".pptx":
                text, parsed = ArtifactService._parse_pptx(content)
            elif extension == ".xlsx":
                text, parsed = ArtifactService._parse_xlsx(content)
            elif extension == ".pdf":
                text, parsed = ArtifactService._parse_pdf(content)
            else:
                raise ArtifactError(f"Unsupported attachment parser for {extension}")
            if not text.strip():
                return text, parsed, "No extractable text was found"
            return text, parsed, None
        except ArtifactError:
            raise
        except Exception as exc:
            raise ArtifactError(f"Attachment parse failed: {type(exc).__name__}: {exc}") from exc

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

    def search_attachment_chunks(
        self,
        task_id: str,
        query: str,
        *,
        stage: str | None = None,
        limit: int = 6,
        max_chunk_chars: int = 1400,
    ) -> list[dict[str, Any]]:
        query_terms = self._search_terms(query)
        candidates: list[dict[str, Any]] = []
        for attachment in self.list_attachments(task_id):
            if attachment.get("parse_status") != "completed":
                continue
            parsed_path = str(attachment.get("parsed_path") or "")
            if not parsed_path:
                continue
            try:
                parsed = self.read_json(task_id, parsed_path)
            except ArtifactError:
                continue
            chunks = parsed.get("chunks") if isinstance(parsed, dict) else []
            if not isinstance(chunks, list):
                continue
            for index, chunk in enumerate(chunks):
                if not isinstance(chunk, dict):
                    continue
                text = str(chunk.get("text") or "").strip()
                if not text:
                    continue
                score = self._chunk_score(query_terms, text)
                attachment_id = str(attachment.get("attachment_id") or attachment.get("file_id") or "")
                candidates.append(
                    {
                        "citation_id": f"{attachment_id}:{chunk.get('chunk_id') or index + 1}",
                        "attachment_id": attachment_id,
                        "file_id": attachment.get("file_id") or attachment_id,
                        "name": attachment.get("name"),
                        "file_type": attachment.get("file_type"),
                        "parsed_path": parsed_path,
                        "chunk_id": chunk.get("chunk_id") or f"chunk_{index + 1:03d}",
                        "chunk_index": index,
                        "source_index": chunk.get("source_index"),
                        "source_type": chunk.get("source_type"),
                        "source_path": chunk.get("source_path"),
                        "page": chunk.get("page"),
                        "section": chunk.get("section"),
                        "table_index": chunk.get("table_index"),
                        "stage": stage,
                        "score": score,
                        "text": text[:max_chunk_chars],
                    }
                )
        candidates.sort(
            key=lambda item: (
                -float(item.get("score") or 0),
                int(item.get("chunk_index") or 0),
                str(item.get("name") or ""),
            )
        )
        selected = [item for item in candidates if float(item.get("score") or 0) > 0][:limit]
        if not selected:
            selected = candidates[:limit]
        return selected

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
        if extension in _LEGACY_OFFICE_EXTENSIONS:
            raise ArtifactError(
                f"Legacy Office format {extension} is not supported. Convert it to the matching .docx, .pptx, or .xlsx file."
            )
        if extension not in _ALLOWED_ATTACHMENT_EXTENSIONS:
            allowed = ", ".join(sorted(_ALLOWED_ATTACHMENT_EXTENSIONS))
            raise ArtifactError(f"Unsupported attachment type. Allowed: {allowed}")
        text, parsed, parse_error = self._parse_attachment(content, extension)

        attachment_id = f"att_{uuid4().hex[:12]}"
        stored_name = f"{attachment_id}_{safe_name}"
        relative_path = f"attachments/{stored_name}"
        parsed_relative_path = f"attachments/parsed/{attachment_id}.parsed.json"
        path = self._resolve(task_id, relative_path, create_parent=True)
        with self._lock:
            self._atomic_write_bytes(path, content)
        self.write_json(task_id, parsed_relative_path, parsed)

        item = {
            "attachment_id": attachment_id,
            "file_id": attachment_id,
            "name": safe_name,
            "path": relative_path,
            "parsed_path": parsed_relative_path,
            "media_type": media_type or "text/plain",
            "file_type": extension.lstrip("."),
            "size": len(content),
            "hash": hashlib.sha256(content).hexdigest(),
            "text_excerpt": text[:context_char_limit],
            "created_at": utc_now(),
            "message_id": message_id,
            "upload_status": "completed",
            "parse_status": "failed" if parse_error else "completed",
            "parse_error": parse_error,
            "chunk_count": len(parsed.get("chunks") or []),
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
            f"[{attachment['name']}]\n{attachment.get('text_excerpt') or ''}" for attachment in attachments
            if attachment.get("text_excerpt")
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
