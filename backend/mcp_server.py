from __future__ import annotations

import os
from typing import Any

from backend.app.artifact_service import ArtifactService
from backend.app.settings import Settings


def build_server(artifact_service: ArtifactService | None = None):
    """Build the official MCP SDK server without importing it in REST-only deployments."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "MCP support is not installed. Install backend/requirements.txt first."
        ) from exc

    service = artifact_service or ArtifactService(Settings.from_env().artifacts_root)
    server = FastMCP(
        "EurekaLoop Artifact Service",
        instructions=(
            "Task-scoped scientific artifact access. Paths are always relative to one "
            "task and traversal outside the artifact root is rejected."
        ),
    )

    @server.tool()
    def list_tasks() -> list[dict[str, Any]]:
        """List task manifests available to the orchestrator."""
        return service.list_tasks()

    @server.tool()
    def get_task_context(task_id: str) -> dict[str, Any]:
        """Read the latest canonical task_context for one task."""
        return service.load_context(task_id)

    @server.tool()
    def list_task_artifacts(task_id: str) -> list[dict[str, Any]]:
        """List task-relative artifacts and their sizes."""
        return service.list_artifacts(task_id)

    @server.tool()
    def read_task_artifact(task_id: str, path: str) -> str:
        """Read a UTF-8 task artifact up to 1 MB; arbitrary host paths are forbidden."""
        return service.read_text(task_id, path)

    @server.tool()
    def write_task_note(task_id: str, filename: str, content: str) -> str:
        """Write a reviewer note under notes/. Only simple Markdown filenames are allowed."""
        return service.write_note(task_id, filename, content)

    @server.tool()
    def compare_task_versions(task_id: str, left: str, right: str) -> dict[str, Any]:
        """Return field-level changes between two task_context snapshots."""
        return service.version_diff(task_id, left, right)

    @server.tool()
    def export_task_bundle(task_id: str) -> str:
        """Create a ZIP submission bundle and return its task-relative path."""
        path = service.export_task(task_id)
        return path.relative_to(service.task_root(task_id)).as_posix()

    return server


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    build_server().run(transport=transport)


if __name__ == "__main__":
    main()
