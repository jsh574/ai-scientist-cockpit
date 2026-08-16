from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from planning_agent.env import ensure_dotenv_loaded
from planning_agent.sample_data import sample_planner_input, short_sample_planner_input
from planning_agent.workflow_chain import PlanningProtocolRunner
from planning_agent.workflow_chain_report import write_html_report


def main(argv: list[str] | None = None) -> int:
    ensure_dotenv_loaded()
    parser = argparse.ArgumentParser(
        description="Test the local Planning protocol compiler and specialist reviews."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--sample", action="store_true", help="Use the short smoke-test input.")
    source.add_argument("--full-sample", action="store_true", help="Use the full sample input.")
    source.add_argument("--input", help="Path to a module-5 input JSON file.")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--hypothesis-id", help="Run one hypothesis; defaults to the first package.")
    target.add_argument("--all-hypotheses", action="store_true", help="Run all input hypotheses.")
    parser.add_argument("--max-parallel-hypotheses", type=int, default=1)
    parser.add_argument("--max-parallel-calls", type=int, default=1)
    parser.add_argument("--output", help="JSON report path under samples/test-artifacts by default.")
    parser.add_argument("--html", help="HTML report path; defaults beside the JSON report.")
    parser.add_argument("--no-html", action="store_true")
    parser.add_argument("--print-runtime", action="store_true")
    parser.add_argument("--print-targets", action="store_true", help="Deprecated alias for --print-runtime.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    progress = None if args.quiet else _print_progress
    event_handler = None if args.quiet else _print_execution_event
    runner = PlanningProtocolRunner.from_env(
        progress_handler=progress, event_handler=event_handler
    )
    if args.print_runtime or args.print_targets:
        summary = runner.configuration_summary()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if all(item.get("configured") for item in summary if not item.get("optional")) else 1

    if not (args.sample or args.full_sample or args.input):
        parser.error("Provide --sample, --full-sample, or --input.")
    if args.max_parallel_hypotheses < 1 or args.max_parallel_calls < 1:
        parser.error("parallel limits must be one or greater")
    if not args.all_hypotheses and args.max_parallel_hypotheses != 1:
        parser.error("--max-parallel-hypotheses requires --all-hypotheses")
    data = _load_input(args)
    if args.all_hypotheses:
        report = runner.run_batch(
            data,
            max_parallel_hypotheses=args.max_parallel_hypotheses,
            max_parallel_calls=args.max_parallel_calls,
        )
    else:
        report = runner.run(data, hypothesis_id=args.hypothesis_id)

    output_path = Path(args.output) if args.output else default_report_path(args.all_hypotheses)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON report: {output_path.resolve()}")
    if not args.no_html:
        html_path = Path(args.html) if args.html else output_path.with_suffix(".html")
        write_html_report(report, html_path)
        print(f"HTML report: {html_path.resolve()}")
    return 0 if report.get("status") in {"success", "partial_success"} else 1


def default_report_path(batch: bool = False, now: datetime | None = None) -> Path:
    current = now or datetime.now()
    suffix = "batch" if batch else "single"
    return Path("samples/test-artifacts") / f"planning-protocol-{suffix}-{current:%Y%m%d-%H%M%S}.json"


def _load_input(args: argparse.Namespace) -> dict[str, Any]:
    if args.sample:
        return short_sample_planner_input()
    if args.full_sample:
        return sample_planner_input()
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Input JSON root must be an object.")
    return data


def _print_progress(message: str) -> None:
    print(f"[planning-protocol] {message}", file=sys.stderr, flush=True)


def _print_execution_event(stage: str, event: dict[str, Any]) -> None:
    scope = [str(event.get("hypothesis_id") or "")]
    if event.get("review_role"):
        scope.append(str(event["review_role"]))
    metrics = []
    if isinstance(event.get("output_chars"), int):
        metrics.append(f"chars={event['output_chars']}")
    if isinstance(event.get("total_tokens"), int):
        metrics.append(f"tokens={event['total_tokens']}")
    print(
        " ".join(
            [f"[planning:{stage}:{'/'.join(filter(None, scope))}]", str(event.get("event") or "event"), *metrics]
        ),
        file=sys.stderr,
        flush=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
