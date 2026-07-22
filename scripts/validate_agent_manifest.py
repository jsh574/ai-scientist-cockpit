from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.agent_protocol import AGENT_SPECS, STAGE_ORDER


MANIFEST_PATH = ROOT / "agents" / "agent-manifest.v1.json"
REGISTRY_PATH = ROOT / "agents" / "registry.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate() -> None:
    manifest = _load_json(MANIFEST_PATH)
    registry = _load_json(REGISTRY_PATH)
    stages = manifest.get("stages")
    if not isinstance(stages, list):
        raise AssertionError("agent-manifest.v1.json must contain a stages array")
    by_stage = {item["stage"]: item for item in stages}
    if list(by_stage) != list(STAGE_ORDER):
        raise AssertionError(f"Manifest stage order mismatch: {list(by_stage)}")

    registry_by_stage = {
        item["stage"]: item
        for item in registry.get("agents", [])
        if isinstance(item, dict)
    }
    for stage in STAGE_ORDER:
        item = by_stage[stage]
        spec = AGENT_SPECS[stage]
        if item["agent_id"] != spec.agent_id:
            raise AssertionError(f"{stage} agent_id mismatch")
        if tuple(item["reads"]) != spec.reads:
            raise AssertionError(f"{stage} reads mismatch")
        if tuple(item["writes"]) != spec.writes:
            raise AssertionError(f"{stage} writes mismatch")
        if not item.get("aliases"):
            raise AssertionError(f"{stage} must declare aliases")
        if not item.get("display_name"):
            raise AssertionError(f"{stage} must declare display_name")
        if stage in registry_by_stage:
            registry_item = registry_by_stage[stage]
            if registry_item.get("reads") != item["reads"]:
                raise AssertionError(f"{stage} registry reads drifted from manifest")
            if registry_item.get("writes") != item["writes"]:
                raise AssertionError(f"{stage} registry writes drifted from manifest")


if __name__ == "__main__":
    validate()
    print("Agent manifest is valid.")
