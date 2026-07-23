from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from planning_agent.env import ensure_dotenv_loaded
from planning_agent.sample_data import sample_planner_input, short_sample_planner_input
from planning_agent.service import run_planning_agent
from planning_agent.workflow_chain import PlanningWorkflowChainRunner


def main(argv: list[str] | None = None) -> int:
    ensure_dotenv_loaded()
    parser = argparse.ArgumentParser(description="Run the research planning agent.")
    parser.add_argument("--input", help="Path to module-5 input JSON.")
    parser.add_argument(
        "--sample", action="store_true", help="Use short built-in smoke-test input."
    )
    parser.add_argument(
        "--full-sample", action="store_true", help="Use full built-in spec-coverage input."
    )
    parser.add_argument(
        "--output",
        help="Path to write response JSON. Defaults to samples/output/planning_responseMM_DD-HH_MM.json.",
    )
    parser.add_argument(
        "--show-progress",
        action="store_true",
        help="Print local and Dify streaming progress to stderr.",
    )
    parser.add_argument(
        "--print-targets",
        action="store_true",
        help="Print all configured A/B/C Dify targets without sending requests.",
    )
    args = parser.parse_args(argv)

    if args.print_targets:
        targets = PlanningWorkflowChainRunner.from_env().configuration_summary()
        print(json.dumps(targets, ensure_ascii=False, indent=2))
        return 0 if all(item.get("configured") for item in targets) else 1

    if args.sample and args.full_sample:
        parser.error("Use only one of --sample or --full-sample.")
    if args.sample:
        data = short_sample_planner_input()
    elif args.full_sample:
        data = sample_planner_input()
    elif args.input:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    else:
        parser.error("Provide --sample, --full-sample, or --input.")

    progress_enabled = args.show_progress or _env_bool("DIFY_SHOW_PROGRESS", False)
    response = run_planning_agent(
        data,
        progress_handler=_print_progress if progress_enabled else None,
    )
    rendered = json.dumps(response, ensure_ascii=False, indent=2)
    output_path = Path(args.output) if args.output else timestamped_response_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    if not args.output:
        print(rendered)
    return 0 if response["metadata"]["status"] != "failed" else 1


def timestamped_response_path(now: datetime | None = None) -> Path:
    current = now or datetime.now()
    return Path("samples/output") / f"planning_response{current.strftime('%m_%d-%H_%M')}.json"


def _print_progress(message: str) -> None:
    print(f"[planning-agent] {message}", file=sys.stderr, flush=True)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


if __name__ == "__main__":
    sys.exit(main())
