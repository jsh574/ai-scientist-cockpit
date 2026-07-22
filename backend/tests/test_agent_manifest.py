from __future__ import annotations

import json
import unittest
from pathlib import Path

from backend.app.agent_protocol import AGENT_SPECS, STAGE_ORDER


class AgentManifestTests(unittest.TestCase):
    def test_manifest_drives_backend_agent_specs(self) -> None:
        manifest_path = Path(__file__).resolve().parents[2] / "agents" / "agent-manifest.v1.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        stages = manifest["stages"]

        self.assertEqual([item["stage"] for item in stages], list(STAGE_ORDER))
        for item in stages:
            spec = AGENT_SPECS[item["stage"]]
            self.assertEqual(spec.agent_id, item["agent_id"])
            self.assertEqual(spec.reads, tuple(item["reads"]))
            self.assertEqual(spec.writes, tuple(item["writes"]))
            self.assertTrue(item["aliases"])
            self.assertTrue(item["display_name"]["zh"])
            self.assertTrue(item["display_name"]["en"])
            self.assertTrue(item["progress_nodes"])


if __name__ == "__main__":
    unittest.main()
