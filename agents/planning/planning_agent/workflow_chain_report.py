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
    runs = report.get("hypothesis_runs")
    if not isinstance(runs, list):
        runs = [report]
    body = "".join(_render_run(item) for item in runs if isinstance(item, dict))
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Planning protocol compiler report</title>
<style>
body{{font:14px/1.55 system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 20px;color:#172033;background:#f5f7fb}}
h1,h2,h3{{line-height:1.2}} article,section,details{{background:white;border:1px solid #dbe1ea;border-radius:10px;padding:16px;margin:14px 0}}
.success{{color:#087443}} .partial_success{{color:#956000}} .failed{{color:#b42318}}
table{{border-collapse:collapse;width:100%}} th,td{{text-align:left;border-bottom:1px solid #e6eaf0;padding:8px}}
pre{{white-space:pre-wrap;word-break:break-word;background:#f7f8fa;padding:12px;border-radius:8px}}
</style></head><body>
<h1>Planning protocol compiler</h1>
<p>Status: <strong class="{_esc(report.get('status'))}">{_esc(report.get('status'))}</strong> · mode: {_esc(report.get('workflow_mode'))}</p>
{body}</body></html>"""


def _render_run(run: dict[str, Any]) -> str:
    stages = run.get("stages") if isinstance(run.get("stages"), list) else []
    rows = "".join(
        "<tr>"
        f"<td>{_esc(stage.get('name'))}</td>"
        f"<td class=\"{_esc(stage.get('status'))}\">{_esc(stage.get('status'))}</td>"
        f"<td>{_esc(stage.get('review_role'))}</td>"
        f"<td>{_esc(stage.get('total_tokens'))}</td>"
        f"<td>{_esc('; '.join(str(value) for value in stage.get('issues', [])))}</td>"
        "</tr>"
        for stage in stages
        if isinstance(stage, dict)
    )
    return f"""<article>
<h2>{_esc(run.get('hypothesis_id'))}</h2>
<p>Status: <strong class="{_esc(run.get('status'))}">{_esc(run.get('status'))}</strong> · calls: {_esc(run.get('model_call_count'))} · tokens: {_esc(run.get('total_tokens'))}</p>
<section><h3>Protocol stages</h3><table><thead><tr><th>Stage</th><th>Status</th><th>Role</th><th>Tokens</th><th>Issues</th></tr></thead><tbody>{rows}</tbody></table></section>
{_json_panel('Planning brief and reviews', run.get('intermediate_results'))}
{_json_panel('Final plan', run.get('final_result'), True)}
</article>"""


def _json_panel(title: str, value: Any, open_panel: bool = False) -> str:
    rendered = html.escape(json.dumps(value, ensure_ascii=False, indent=2))
    opened = " open" if open_panel else ""
    return f"<details{opened}><summary>{html.escape(title)}</summary><pre>{rendered}</pre></details>"


def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)
