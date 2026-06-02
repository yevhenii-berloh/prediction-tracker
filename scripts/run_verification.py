"""Стадія 3/3: прогін Verifier (2-call split, Flash Lite) на витягнутих клеймах →
verification_results.json + читабельний review.md для ручного рев'ю."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass

from prophet_checker.analysis.verifier import Verifier
from prophet_checker.llm.client import LLMClient

DEFAULT_MODEL = "gemini/gemini-3.1-flash-lite-preview"
RUN_DIR = PROJECT_ROOT / "scripts" / "outputs" / "pipeline_run"
DEFAULT_INPUT = RUN_DIR / "extracted_claims.json"
DEFAULT_OUTPUT = RUN_DIR / "verification_results.json"
DEFAULT_REPORT = RUN_DIR / "review.md"

PROVIDER_API_KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def build_verifier(model_id: str) -> Verifier:
    provider, model = model_id.split("/", 1)
    env_var = PROVIDER_API_KEY_ENV.get(provider)
    if not env_var:
        raise ValueError(f"Unknown provider {provider!r}")
    api_key = os.environ.get(env_var)
    if not api_key:
        raise RuntimeError(f"Missing API key for {provider!r}: set {env_var}")
    client = LLMClient(provider=provider, model=model, api_key=api_key, temperature=0.0)
    return Verifier(client)


def render_report(extractions: list[dict], today: str, model_id: str) -> str:
    total_claims = sum(len(e["claims"]) for e in extractions)
    verified = sum(
        1 for e in extractions for c in e["claims"]
        if c.get("verification") and "error" not in c["verification"]
    )
    errors = total_claims - verified

    lines = [
        "# Pipeline Review — extractor + verifier",
        "",
        f"**today:** {today} · **model:** {model_id} · **posts:** {len(extractions)} · "
        f"**claims:** {total_claims} (verified {verified}, errors {errors})",
        "",
    ]
    for e in extractions:
        lines.append("---")
        lines.append("")
        lines.append(f"## {e['post_id']} — {e['post_published_at']}")
        lines.append("")
        lines.append("```text")
        lines.append(e["post_text"])
        lines.append("```")
        lines.append("")
        if not e["claims"]:
            lines.append("_(прогнозів не виявлено)_")
            lines.append("")
            continue
        for j, c in enumerate(e["claims"], 1):
            v = c.get("verification") or {}
            if "error" in v:
                lines.append(f"**Claim {j}** — ⚠️ ERROR: {v['error']}")
            else:
                lines.append(
                    f"**Claim {j}** — `{v.get('status')}` "
                    f"(conf {v.get('confidence')}) · strength `{v.get('prediction_strength')}` "
                    f"· value `{v.get('prediction_value')}`"
                )
            lines.append(f"- claim: {c['claim_text']}")
            lines.append(f"- situation: {c['situation']}")
            lines.append(
                f"- dates: made {c['prediction_date']} → target {c['target_date'] or '—'}"
            )
            if "error" not in v:
                lines.append(f"- reasoning: {v.get('reasoning')}")
                lines.append(f"- evidence: {v.get('evidence') or '—'}")
                if v.get("retry_after"):
                    lines.append(f"- retry_after: {v['retry_after']}")
            lines.append("")
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--today", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    parser.add_argument("--sleep", type=float, default=0.0)
    args = parser.parse_args()

    data = json.load(open(args.input))
    extractions = data["extractions"]
    verifier = build_verifier(args.model)

    total = sum(len(e["claims"]) for e in extractions)
    done = 0
    errors = 0
    for e in extractions:
        for c in e["claims"]:
            done += 1
            try:
                c["verification"] = await verifier.verify(
                    claim=c["claim_text"],
                    situation=c["situation"],
                    prediction_date=c["prediction_date"],
                    target_date=c["target_date"],
                    today=args.today,
                )
            except Exception as ex:
                errors += 1
                c["verification"] = {"error": f"{type(ex).__name__}: {ex}"}
            print(f"  [{done}/{total}] {e['post_id']} claim → "
                  f"{c['verification'].get('status', 'ERROR')}", flush=True)
            if args.sleep > 0:
                await asyncio.sleep(args.sleep)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump({
            "model": args.model,
            "today": args.today,
            "claims_total": total,
            "errors": errors,
            "extractions": extractions,
        }, f, ensure_ascii=False, indent=2)

    report = render_report(extractions, args.today, args.model)
    with open(args.report, "w") as f:
        f.write(report)

    print(f"\nclaims: {total}  verified: {total - errors}  errors: {errors}")
    print(f"results → {args.output}")
    print(f"review  → {args.report}")


if __name__ == "__main__":
    asyncio.run(main())
