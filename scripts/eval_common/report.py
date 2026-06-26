from __future__ import annotations

from pathlib import Path

from eval_common.models import EvalReport


def write_report(report: EvalReport, out_dir: Path) -> None:
    """Persist both report.json (full per-item) and report.md (human summary)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(_render_md(report), encoding="utf-8")


def _render_md(report: EvalReport) -> str:
    m = report.metadata
    lines = [
        f"# {m.eval_name} — eval report",
        "",
        f"- created: {m.created_at}",
        f"- cases: {m.n_cases}",
        f"- judge: {m.judge_id or '—'}",
        "",
        "## Metrics",
        "",
        "```json",
        report.metrics.model_dump_json(indent=2),
        "```",
        "",
        f"_{len(report.runs)} per-item runs persisted in report.json._",
    ]
    return "\n".join(lines)
