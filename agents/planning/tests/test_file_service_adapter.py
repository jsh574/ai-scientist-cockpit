import json
from pathlib import Path

from planning_agent.file_service_adapter import LocalArtifactStore


def test_local_artifact_store_put_get_and_list():
    store_root = Path("samples/test-artifacts")
    store = LocalArtifactStore(store_root)
    content = {"hello": "world"}

    artifact = store.put_artifact(
        task_id="task_demo_001",
        name="result.json",
        content=content,
        mime_type="application/json",
    )

    assert artifact["artifact_id"].startswith("task_demo_001_")
    assert artifact["uri"].startswith("artifact://task_demo_001/")
    assert artifact["mime_type"] == "application/json"
    loaded = store.get_artifact(artifact["artifact_id"])
    assert json.loads(loaded["content"]) == content
    assert artifact["artifact_id"] in [
        item["artifact_id"] for item in store.list_artifacts("task_demo_001")
    ]
