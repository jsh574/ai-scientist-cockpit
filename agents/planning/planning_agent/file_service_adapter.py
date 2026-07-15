from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class LocalArtifactStore:
    """Small HTTP-file-service stand-in for integration tests and demos."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put_artifact(
        self,
        task_id: str,
        name: str,
        content: str | bytes | dict[str, Any] | list[Any],
        mime_type: str = "application/json",
    ) -> dict[str, Any]:
        safe_name = _safe_name(name)
        artifact_id = f"{task_id}_{safe_name}"
        task_dir = self.root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = task_dir / safe_name
        payload = _serialize_content(content)
        artifact_path.write_text(payload, encoding="utf-8")
        metadata = {
            "artifact_id": artifact_id,
            "task_id": task_id,
            "name": safe_name,
            "uri": f"artifact://{task_id}/{safe_name}",
            "mime_type": mime_type,
            "size": len(payload.encode("utf-8")),
            "created_at": datetime.now(UTC).isoformat(),
            "path": str(artifact_path),
        }
        (task_dir / f"{safe_name}.metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return metadata

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        metadata_path = self._find_metadata(artifact_id)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        artifact_path = Path(metadata["path"])
        return {
            **metadata,
            "content": artifact_path.read_text(encoding="utf-8"),
        }

    def list_artifacts(self, task_id: str) -> list[dict[str, Any]]:
        task_dir = self.root / task_id
        if not task_dir.exists():
            return []
        artifacts = []
        for metadata_path in sorted(task_dir.glob("*.metadata.json")):
            artifacts.append(json.loads(metadata_path.read_text(encoding="utf-8")))
        return artifacts

    def _find_metadata(self, artifact_id: str) -> Path:
        for metadata_path in self.root.glob("*/*.metadata.json"):
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("artifact_id") == artifact_id:
                return metadata_path
        raise FileNotFoundError(f"Artifact not found: {artifact_id}")


def _serialize_content(content: str | bytes | dict[str, Any] | list[Any]) -> str:
    if isinstance(content, bytes):
        return content.decode("utf-8")
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, indent=2)


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
