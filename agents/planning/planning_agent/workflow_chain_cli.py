from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from planning_agent.env import ensure_dotenv_loaded
from planning_agent.sample_data import sample_planner_input, short_sample_planner_input
from planning_agent.workflow_chain import DEFAULT_VARIANTS, PlanningWorkflowChainRunner
from planning_agent.workflow_chain_report import write_html_report


def main(argv: list[str] | None = None) -> int:
    ensure_dotenv_loaded()
    parser = argparse.ArgumentParser(
        description="Test the modular Dify planning chain: A candidates -> B selector -> C plan."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--sample", action="store_true", help="Use the short smoke-test input.")
    source.add_argument(
        "--full-sample", action="store_true", help="Use the full specification sample input."
    )
    source.add_argument("--input", help="Path to a module-5 input JSON file.")
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--hypothesis-id", help="Test one hypothesis; defaults to local top-ranked."
    )
    target.add_argument(
        "--all-hypotheses",
        action="store_true",
        help="Run every input hypothesis through the complete A/B/C chain.",
    )
    parser.add_argument(
        "--variants",
        default=",".join(DEFAULT_VARIANTS),
        help="Comma-separated Workflow A variant modes.",
    )
    parser.add_argument(
        "--max-revisions",
        type=int,
        default=1,
        help="Maximum bounded A/B retries for decision=revise_once.",
    )
    parser.add_argument(
        "--max-parallel-hypotheses",
        type=int,
        default=1,
        help="Maximum hypotheses running A/B/C concurrently in batch mode.",
    )
    parser.add_argument("--output", help="JSON report path under samples/test-artifacts by default.")
    parser.add_argument("--html", help="HTML report path; defaults beside the JSON report.")
    parser.add_argument("--no-html", action="store_true", help="Do not write the HTML report.")
    parser.add_argument(
        "--print-targets",
        action="store_true",
        help="Print endpoint/configuration presence without exposing API keys.",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress structured progress messages on stderr."
    )
    args = parser.parse_args(argv)

    progress = None if args.quiet else _print_progress
    event_handler = None if args.quiet else _print_dify_event
    runner = PlanningWorkflowChainRunner.from_env(
        progress_handler=progress,
        event_handler=event_handler,
    )
    if args.print_targets:
        summary = runner.configuration_summary()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if all(item.get("configured") for item in summary) else 1

    if not (args.sample or args.full_sample or args.input):
        parser.error("Provide --sample, --full-sample, or --input.")
    data = _load_input(args)
    variants = tuple(item.strip() for item in args.variants.split(",") if item.strip())
    if not variants:
        parser.error("--variants must contain at least one value.")
    if len(set(variants)) != len(variants):
        parser.error("--variants must not contain duplicates.")
    if args.max_revisions < 0:
        parser.error("--max-revisions must be zero or greater.")
    if args.max_parallel_hypotheses < 1:
        parser.error("--max-parallel-hypotheses must be one or greater.")
    if not args.all_hypotheses and args.max_parallel_hypotheses != 1:
        parser.error("--max-parallel-hypotheses requires --all-hypotheses.")

    if args.all_hypotheses:
        report = runner.run_batch(
            data,
            variants=variants,
            max_revisions=args.max_revisions,
            max_parallel_hypotheses=args.max_parallel_hypotheses,
        )
    else:
        report = runner.run(
            data,
            hypothesis_id=args.hypothesis_id,
            variants=variants,
            max_revisions=args.max_revisions,
        )
    output_path = (
        Path(args.output)
        if args.output
        else default_report_path(batch=args.all_hypotheses)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"JSON report: {output_path.resolve()}")

    if not args.no_html:
        html_path = Path(args.html) if args.html else output_path.with_suffix(".html")
        write_html_report(report, html_path)
        print(f"HTML report: {html_path.resolve()}")

    if report["status"] == "success":
        return 0
    if report["status"] == "requires_action":
        return 2
    return 1


def default_report_path(
    now: datetime | None = None, batch: bool = False
) -> Path:
    current = now or datetime.now()
    stamp = current.strftime("%Y%m%d-%H%M%S")
    name = "planning-workflow-chain-batch" if batch else "planning-workflow-chain"
    return Path("samples/test-artifacts") / f"{name}-{stamp}.json"


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
    print(f"[planning-chain] {message}", file=sys.stderr, flush=True)


def _print_dify_event(workflow: str, event: dict[str, Any]) -> None:
    prefix = f"[dify:{workflow}]{_event_scope(event)}"
    event_name = str(event.get("event") or "event")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    if event_name == "text_chunk":
        text = data.get("text") or data.get("chunk") or ""
        length = len(text) if isinstance(text, str) else 0
        print(
            f"{prefix} text_chunk chars={length}",
            file=sys.stderr,
            flush=True,
        )
        return
    title = data.get("title") or data.get("node_title") or data.get("node_type") or ""
    status = data.get("status") or ""
    run_id = data.get("id") or event.get("workflow_run_id") or ""
    fields = [prefix, event_name]
    if title:
        fields.append(str(title))
    if status:
        fields.append(f"status={status}")
    if run_id and event_name in {"workflow_started", "workflow_finished", "workflow_failed"}:
        fields.append(f"run_id={run_id}")
    print(" ".join(fields), file=sys.stderr, flush=True)


def _event_scope(event: dict[str, Any]) -> str:
    context = event.get("planning_context")
    if not isinstance(context, dict):
        return ""
    fields = (
        ("hyp", context.get("hypothesis_id")),
        ("variant", context.get("variant_mode")),
        ("round", context.get("round")),
        ("attempt", context.get("attempt")),
        ("candidate", context.get("selected_candidate_id")),
    )
    return "".join(
        f"[{label}={_scope_value(value)}]"
        for label, value in fields
        if value not in (None, "")
    )


def _scope_value(value: Any) -> str:
    text = " ".join(str(value).split())
    return text.replace("[", "(").replace("]", ")")[:160]


if __name__ == "__main__":
    raise SystemExit(main())
