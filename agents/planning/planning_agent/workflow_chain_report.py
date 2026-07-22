from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def write_html_report(report: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_html_report(report), encoding="utf-8")
    return target


def render_html_report(report: dict[str, Any]) -> str:
    if isinstance(report.get("hypothesis_runs"), list):
        return _render_batch_html_report(report)
    status = str(report.get("status") or "unknown")
    stages = report.get("stages") if isinstance(report.get("stages"), list) else []
    intermediate = report.get("intermediate_results")
    if not isinstance(intermediate, dict):
        intermediate = {}
    candidate_rounds = intermediate.get("candidate_rounds")
    selection_rounds = intermediate.get("selection_rounds")
    candidate_rounds = candidate_rounds if isinstance(candidate_rounds, list) else []
    selection_rounds = selection_rounds if isinstance(selection_rounds, list) else []

    stage_html = "".join(_render_stage(stage) for stage in stages if isinstance(stage, dict))
    candidates_html = "".join(
        _render_candidate_round(item) for item in candidate_rounds if isinstance(item, dict)
    )
    selection_html = "".join(
        _render_selection_round(item) for item in selection_rounds if isinstance(item, dict)
    )
    final_html = _json_panel("Final plan", report.get("final_result"), open_panel=True)
    errors = report.get("errors") if isinstance(report.get("errors"), list) else []
    errors_html = "".join(f"<li>{_escape(item)}</li>" for item in errors)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Planning workflow chain test</title>
  <style>
    :root {{ color-scheme: light; --ink: #17202a; --muted: #5e6b75; --line: #d7dde2;
      --surface: #ffffff; --canvas: #f4f6f7; --success: #176b45; --warn: #9a5b00;
      --danger: #9f2d2d; --accent: #176f8a; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--canvas); color: var(--ink); font-family: Inter, Segoe UI,
      system-ui, sans-serif; font-size: 14px; line-height: 1.5; letter-spacing: 0; }}
    header {{ background: #18242b; color: #fff; border-bottom: 4px solid #33a474; }}
    .wrap {{ width: min(1180px, calc(100% - 32px)); margin: 0 auto; }}
    header .wrap {{ padding: 28px 0 24px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; font-weight: 680; letter-spacing: 0; }}
    h2 {{ margin: 0 0 14px; font-size: 18px; letter-spacing: 0; }}
    h3 {{ margin: 0; font-size: 15px; letter-spacing: 0; }}
    p {{ margin: 0; }}
    main {{ padding: 24px 0 48px; }}
    section {{ padding: 22px 0; border-bottom: 1px solid var(--line); }}
    .summary {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 1px;
      background: var(--line); border: 1px solid var(--line); }}
    .metric {{ min-width: 0; padding: 14px; background: var(--surface); }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }}
    .metric strong {{ display: block; overflow-wrap: anywhere; font-size: 14px; }}
    .badge {{ display: inline-flex; align-items: center; min-height: 24px; padding: 2px 8px;
      border-radius: 4px; border: 1px solid currentColor; font-weight: 650; font-size: 12px; }}
    .success {{ color: var(--success); }} .requires_action, .partial_success {{ color: var(--warn); }}
    .failed {{ color: var(--danger); }} .running, .unknown {{ color: var(--accent); }}
    .timeline {{ display: grid; gap: 8px; }}
    .stage {{ display: grid; grid-template-columns: 190px 120px 110px 1fr; align-items: center;
      gap: 12px; min-height: 50px; padding: 10px 12px; background: var(--surface);
      border-left: 4px solid var(--accent); border-top: 1px solid var(--line);
      border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); }}
    .stage small {{ color: var(--muted); overflow-wrap: anywhere; }}
    .grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
    .candidate {{ min-width: 0; padding: 14px; background: var(--surface); border: 1px solid var(--line);
      border-top: 4px solid #d28c36; }}
    .candidate dl {{ display: grid; grid-template-columns: 90px 1fr; gap: 6px 10px; margin: 12px 0 0; }}
    dt {{ color: var(--muted); }} dd {{ margin: 0; overflow-wrap: anywhere; }}
    details {{ margin-top: 10px; background: var(--surface); border: 1px solid var(--line); }}
    summary {{ cursor: pointer; padding: 10px 12px; font-weight: 650; color: var(--accent); }}
    pre {{ margin: 0; padding: 14px; overflow: auto; max-height: 620px; background: #11191e;
      color: #dce7ec; font: 12px/1.55 Consolas, ui-monospace, monospace; white-space: pre-wrap;
      overflow-wrap: anywhere; }}
    .errors {{ color: var(--danger); background: #fff6f5; border: 1px solid #e8b9b4;
      padding: 12px 16px 12px 32px; }}
    .empty {{ color: var(--muted); font-style: italic; }}
    @media (max-width: 850px) {{ .summary, .grid {{ grid-template-columns: 1fr 1fr; }}
      .stage {{ grid-template-columns: 1fr 1fr; }} }}
    @media (max-width: 560px) {{ .wrap {{ width: min(100% - 20px, 1180px); }}
      .summary, .grid, .stage {{ grid-template-columns: 1fr; }} h1 {{ font-size: 23px; }} }}
  </style>
</head>
<body>
  <header><div class="wrap">
    <h1>Planning workflow chain test</h1>
    <p>A candidate generation / B selection / C final plan</p>
  </div></header>
  <main class="wrap">
    <section aria-labelledby="summary-heading">
      <h2 id="summary-heading">Run summary</h2>
      <div class="summary">
        {_metric("Status", f'<span class="badge {_status_class(status)}">{_escape(status)}</span>')}
        {_metric("Task", _escape(report.get("task_id")))}
        {_metric("Hypothesis", _escape(report.get("hypothesis_id")))}
        {_metric("Decision", _escape(report.get("decision") or "not reached"))}
        {_metric("Duration", _escape(f"{report.get('duration_seconds', 0)} s"))}
      </div>
    </section>
    <section aria-labelledby="stages-heading">
      <h2 id="stages-heading">Stage timeline</h2>
      <div class="timeline">{stage_html or '<p class="empty">No completed stages.</p>'}</div>
    </section>
    <section aria-labelledby="candidates-heading">
      <h2 id="candidates-heading">Workflow A candidates</h2>
      {candidates_html or '<p class="empty">No candidate output.</p>'}
    </section>
    <section aria-labelledby="selection-heading">
      <h2 id="selection-heading">Workflow B selection</h2>
      {selection_html or '<p class="empty">Selection was not reached.</p>'}
    </section>
    <section aria-labelledby="final-heading">
      <h2 id="final-heading">Workflow C result</h2>
      {final_html}
    </section>
    <section aria-labelledby="errors-heading">
      <h2 id="errors-heading">Errors and next action</h2>
      <p><strong>Next action:</strong> {_escape(report.get("next_action") or "not set")}</p>
      {f'<ul class="errors">{errors_html}</ul>' if errors else '<p class="empty">No errors recorded.</p>'}
    </section>
    <section aria-labelledby="raw-heading">
      <h2 id="raw-heading">Raw integration report</h2>
      {_json_panel("Full JSON", report)}
    </section>
  </main>
</body>
</html>
"""


def _render_batch_html_report(report: dict[str, Any]) -> str:
    status = str(report.get("status") or "unknown")
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    runs = report.get("hypothesis_runs")
    runs = runs if isinstance(runs, list) else []
    rows = "".join(
        "<tr>"
        f"<td>{_escape(run.get('hypothesis_id'))}</td>"
        f'<td><span class="badge {_status_class(str(run.get("status") or "unknown"))}">'
        f"{_escape(run.get('status') or 'unknown')}</span></td>"
        f"<td>{_escape(run.get('decision') or 'not reached')}</td>"
        f"<td>{_escape(run.get('next_action') or 'not set')}</td>"
        f"<td>{_escape(run.get('duration_seconds', 0))} s</td>"
        "</tr>"
        for run in runs
        if isinstance(run, dict)
    )
    details = "".join(
        "<details>"
        f"<summary>{_escape(run.get('hypothesis_id'))}: "
        f"{_escape(run.get('status') or 'unknown')}</summary>"
        f"<pre>{html.escape(json.dumps(run, ensure_ascii=False, indent=2))}</pre>"
        "</details>"
        for run in runs
        if isinstance(run, dict)
    )
    errors = report.get("errors") if isinstance(report.get("errors"), list) else []
    errors_html = "".join(f"<li>{_escape(item)}</li>" for item in errors)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Planning workflow batch chain test</title>
  <style>
    :root {{ color-scheme: light; --ink: #17202a; --muted: #5e6b75; --line: #d7dde2;
      --surface: #fff; --canvas: #f4f6f7; --success: #176b45; --warn: #9a5b00;
      --danger: #9f2d2d; --accent: #176f8a; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--canvas); color: var(--ink);
      font: 14px/1.5 Inter, Segoe UI, system-ui, sans-serif; letter-spacing: 0; }}
    header {{ padding: 26px max(20px, calc((100% - 1180px) / 2)); background: #18242b;
      color: #fff; border-bottom: 4px solid #33a474; }}
    h1 {{ margin: 0 0 6px; font-size: 27px; }} h2 {{ margin: 0 0 12px; font-size: 18px; }}
    main {{ width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 22px 0 48px; }}
    section {{ padding: 20px 0; border-bottom: 1px solid var(--line); }}
    .summary {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 1px;
      background: var(--line); border: 1px solid var(--line); }}
    .metric {{ padding: 13px; background: var(--surface); }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; }}
    .metric strong {{ display: block; margin-top: 4px; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--surface); }}
    th, td {{ padding: 10px; border: 1px solid var(--line); text-align: left; }}
    th {{ color: var(--muted); font-size: 12px; }}
    .badge {{ display: inline-block; padding: 2px 8px; border: 1px solid currentColor;
      border-radius: 4px; font-weight: 650; font-size: 12px; }}
    .success {{ color: var(--success); }} .requires_action, .partial_success {{ color: var(--warn); }}
    .failed {{ color: var(--danger); }} .unknown, .running {{ color: var(--accent); }}
    details {{ margin-top: 10px; background: var(--surface); border: 1px solid var(--line); }}
    summary {{ cursor: pointer; padding: 10px 12px; font-weight: 650; color: var(--accent); }}
    pre {{ margin: 0; padding: 14px; max-height: 720px; overflow: auto; background: #11191e;
      color: #dce7ec; font: 12px/1.55 Consolas, monospace; white-space: pre-wrap;
      overflow-wrap: anywhere; }}
    .errors {{ color: var(--danger); }}
    @media (max-width: 720px) {{ .summary {{ grid-template-columns: 1fr 1fr; }}
      main {{ width: min(100% - 20px, 1180px); }} .table-wrap {{ overflow-x: auto; }} }}
  </style>
</head>
<body>
  <header>
    <h1>Planning workflow batch chain test</h1>
    <p>Every selected hypothesis runs through A candidates, B selection, and C planning.</p>
  </header>
  <main>
    <section>
      <h2>Batch summary</h2>
      <div class="summary">
        {_metric("Status", f'<span class="badge {_status_class(status)}">{_escape(status)}</span>')}
        {_metric("Total", _escape(summary.get("total", 0)))}
        {_metric("Success", _escape(summary.get("success", 0)))}
        {_metric("Requires action", _escape(summary.get("requires_action", 0)))}
        {_metric("Failed", _escape(summary.get("failed", 0)))}
      </div>
    </section>
    <section>
      <h2>Hypothesis runs</h2>
      <div class="table-wrap"><table>
        <thead><tr><th>Hypothesis</th><th>Status</th><th>B decision</th>
          <th>Next action</th><th>Duration</th></tr></thead>
        <tbody>{rows}</tbody>
      </table></div>
    </section>
    <section>
      <h2>Complete A/B/C subreports</h2>
      {details or "<p>No hypothesis reports.</p>"}
    </section>
    <section>
      <h2>Errors</h2>
      {f'<ul class="errors">{errors_html}</ul>' if errors else "<p>No errors recorded.</p>"}
    </section>
  </main>
</body>
</html>
"""


def _render_stage(stage: dict[str, Any]) -> str:
    status = str(stage.get("status") or "unknown")
    run_id = stage.get("workflow_run_id") or "multiple parallel runs"
    return (
        '<article class="stage">'
        f"<h3>{_escape(stage.get('stage_id'))}</h3>"
        f'<span class="badge {_status_class(status)}">{_escape(status)}</span>'
        f"<span>{_escape(stage.get('duration_seconds', 0))} s</span>"
        f"<small>{_escape(run_id)}</small>"
        "</article>"
    )


def _render_candidate_round(item: dict[str, Any]) -> str:
    candidates = item.get("candidates") if isinstance(item.get("candidates"), list) else []
    cards = "".join(_render_candidate(value) for value in candidates if isinstance(value, dict))
    cards_or_empty = cards or '<p class="empty">No valid candidate.</p>'
    return (
        f"<h3>Round {_escape(item.get('round'))}</h3>"
        f'<div class="grid">{cards_or_empty}</div>'
        + _json_panel(f"Round {item.get('round')} guardrail reports", item.get("guardrail_reports"))
    )


def _render_candidate(candidate: dict[str, Any]) -> str:
    return f"""<article class="candidate">
      <h3>{_escape(candidate.get("variant_mode") or "candidate")}</h3>
      <dl>
        <dt>Candidate ID</dt><dd>{_escape(candidate.get("candidate_id"))}</dd>
        <dt>Status</dt><dd>{_escape(candidate.get("status"))}</dd>
        <dt>Design type</dt><dd>{_escape(candidate.get("design_type"))}</dd>
        <dt>Objective</dt><dd>{_escape(candidate.get("planning_objective"))}</dd>
      </dl>
      {_json_panel("Candidate JSON", candidate)}
    </article>"""


def _render_selection_round(item: dict[str, Any]) -> str:
    selection = item.get("design_selection")
    decision = selection.get("decision") if isinstance(selection, dict) else "unknown"
    return (
        f'<p><span class="badge {_status_class(str(decision))}">Round '
        f"{_escape(item.get('round'))}: {_escape(decision)}</span></p>"
        + _json_panel("Design selection", selection, open_panel=True)
        + _json_panel("Selected design", item.get("selected_design"))
        + _json_panel("Selection guardrail", item.get("selection_guardrail_report"))
    )


def _json_panel(title: str, value: Any, open_panel: bool = False) -> str:
    if value is None:
        return '<p class="empty">No output.</p>'
    rendered = html.escape(json.dumps(value, ensure_ascii=False, indent=2))
    open_attr = " open" if open_panel else ""
    return f"<details{open_attr}><summary>{_escape(title)}</summary><pre>{rendered}</pre></details>"


def _metric(label: str, value_html: str) -> str:
    return f'<div class="metric"><span>{_escape(label)}</span><strong>{value_html}</strong></div>'


def _status_class(status: str) -> str:
    allowed = {"success", "requires_action", "partial_success", "failed", "running"}
    return status if status in allowed else "unknown"


def _escape(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))
