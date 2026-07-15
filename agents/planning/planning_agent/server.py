from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from planning_agent.env import ensure_dotenv_loaded
from planning_agent.service import run_planning_agent


class PlanningAgentHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/planning-agent/run":
            self._send_json({"error": "not_found"}, status=404)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            request_data = json.loads(body)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": f"invalid_json: {exc}"}, status=400)
            return

        response = run_planning_agent(request_data)
        status = 200 if response["metadata"]["status"] != "failed" else 502
        self._send_json(response, status=status)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main(argv: list[str] | None = None) -> int:
    ensure_dotenv_loaded()
    parser = argparse.ArgumentParser(description="Serve the research planning agent API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer((args.host, args.port), PlanningAgentHandler)
    print(f"Planning agent server listening on http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
