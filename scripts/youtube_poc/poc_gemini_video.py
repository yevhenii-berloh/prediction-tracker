"""PoC-B — feed the YouTube URL straight to Gemini, no yt-dlp / ffmpeg / Whisper.

Head-to-head against PoC-A (yt-dlp -> Groq whisper -> chunks -> extractor), same video.
Answers three questions PoC-A cannot:
  1. Does Gemini find comparable predictions from VIDEO input? (PoC-A: 11 predictions, 6/22 chunks)
  2. Are the timestamps it emits real? (PoC-A gets them mechanically from whisper verbose_json)
  3. What does it actually cost vs PoC-A's measured $0.053/hour end-to-end?

Fair-experiment note: reuses the project's own EXTRACTION_SYSTEM and parse_extraction_response,
so what differs from PoC-A is the INPUT MODALITY (video vs text), not the prompt contract.

Run (extraction, the head-to-head):
    cd ~/Brain/prediction-tracker
    uv run --with google-genai python scripts/youtube_poc/poc_gemini_video.py \
        --url "https://www.youtube.com/watch?v=dtAbQYffGhg"

Run (transcript mode — does a ~2h transcript survive the output-token limit?):
    ... --mode transcript
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"  # project's production extraction model
LITELLM_MODEL = "gemini/" + DEFAULT_MODEL  # same model, LiteLLM naming, for cost lookup
CONTEXT_LIMIT = 1_000_000  # approximate; used to warn before a doomed default-res request
TOKENS_PER_SEC = {"low": 100, "default": 300}  # per Gemini video-understanding docs

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent / "artifacts"
CHUNKS_FILE = OUT_DIR / "poc_chunks.md"  # PoC-A artifact — ground truth for timestamp checking

# PoC-A baseline, for the printed comparison
POC_A = {"predictions": 11, "chunks_with": 6, "chunks_total": 22, "usd_per_hour": 0.053}


# ---- Prompts (schema mirrors the project's EXTRACTION_TEMPLATE + a timestamp field) ----

VIDEO_EXTRACTION_PROMPT = """Analyze this video segment by {person_name} (published on {published_date}).
Extract all predictions — statements about future events that can later be verified.

Go through the WHOLE segment from start to finish. Do not stop early.

For each prediction, extract:
- claim_text: a SELF-CONTAINED reconstruction of the prediction, written in
  Ukrainian (translate if the speech is in Russian). One complete, grammatical,
  falsifiable sentence — explicit subject + predicate + timeframe when known,
  stating the AUTHOR'S OWN forecast with its correct polarity.
- timestamp: when it is said, as MM:SS measured from the START OF THIS SEGMENT
  (the segment begins at 00:00 for this purpose)
- prediction_date: when the prediction was made (YYYY-MM-DD)
- target_date: when the predicted event should happen (YYYY-MM-DD or null if unclear)
- topic: category (e.g., "війна", "економіка", "політика", "міжнародні відносини")
- situation: 1-2 sentences (in the speech's language) summarizing the circumstances
  the author was responding to. Your summary, NOT a verbatim quote.

Respond with JSON:
{{"predictions": [{{"claim_text": "...", "timestamp": "MM:SS", "prediction_date": "...", "target_date": "...", "topic": "...", "situation": "..."}}]}}

If no predictions found, respond: {{"predictions": []}}"""

TRANSCRIPT_PROMPT = """Transcribe this entire video verbatim, from start to finish.
Prefix each paragraph with its timestamp in [MM:SS] format.
Do not summarize, do not skip sections, do not stop early. Output the transcript only."""


# ---- Timestamp ground-truth check against the PoC-A whisper transcript ----


@dataclass
class WhisperChunk:
    start: float
    text: str


def load_whisper_chunks() -> list[WhisperChunk]:
    """Parse PoC-A's poc_chunks.md — the mechanically-timestamped whisper output."""
    if not CHUNKS_FILE.exists():
        return []

    chunks: list[WhisperChunk] = []
    current: float | None = None
    buffer: list[str] = []

    for line in CHUNKS_FILE.read_text(encoding="utf-8").splitlines():
        header = re.match(r"^## Chunk \d+\s+—\s+.*[?&]t=(\d+)s", line)
        if not header:
            if current is not None and line.strip():
                buffer.append(line.strip())
            continue
        if current is not None:
            chunks.append(WhisperChunk(start=current, text=" ".join(buffer)))
        current = float(header.group(1))
        buffer = []

    if current is not None:
        chunks.append(WhisperChunk(start=current, text=" ".join(buffer)))
    return chunks


def parse_timestamp(value: str) -> float | None:
    if not value:
        return None
    parts = value.strip().split(":")
    if not all(p.strip().isdigit() for p in parts):
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def whisper_context(chunks: list[WhisperChunk], ts: float, width: int = 260) -> str:
    """Return the whisper text around ts, so a human can see whether the claim is really there."""
    if not chunks:
        return "(no PoC-A chunks available)"

    idx = 0
    for i, chunk in enumerate(chunks):
        if chunk.start <= ts:
            idx = i
    chunk = chunks[idx]

    end = chunks[idx + 1].start if idx + 1 < len(chunks) else chunk.start + 300.0
    span = max(end - chunk.start, 1.0)
    ratio = min(max((ts - chunk.start) / span, 0.0), 1.0)
    centre = int(len(chunk.text) * ratio)
    lo = max(centre - width, 0)
    return "…" + chunk.text[lo : centre + width] + "…"


# ---- Gemini call ----


def build_client():
    from google import genai

    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def build_config(media_res: str, genai_types):
    if media_res != "low":
        return None
    return genai_types.GenerateContentConfig(
        media_resolution=genai_types.MediaResolution.MEDIA_RESOLUTION_LOW
    )


def call_gemini(
    url: str,
    prompt: str,
    model: str,
    media_res: str,
    system: str | None,
    window: tuple[float, float] | None = None,
):
    """One request. `window` clips the video via video_metadata offsets — required for
    long videos: the full 1.79h request returns 500 INTERNAL, 60-min clips succeed."""
    from google.genai import types

    client = build_client()
    video_metadata = None
    if window is not None:
        video_metadata = types.VideoMetadata(
            start_offset=f"{int(window[0])}s", end_offset=f"{int(window[1])}s"
        )
    parts = [
        types.Part(file_data=types.FileData(file_uri=url), video_metadata=video_metadata),
        types.Part(text=prompt),
    ]
    config = build_config(media_res, types)
    if system is not None:
        config = config or types.GenerateContentConfig()
        config.system_instruction = system

    return client.models.generate_content(
        model=model,
        contents=types.Content(parts=parts, role="user"),
        config=config,
    )


def build_windows(duration_s: float, clip_s: float) -> list[tuple[float, float]]:
    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < duration_s:
        windows.append((start, min(start + clip_s, duration_s)))
        start += clip_s
    return windows


# ---- Cost ----


def tokens_of(usage) -> tuple[int, int]:
    prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
    out_tokens = getattr(usage, "candidates_token_count", 0) or 0
    return prompt_tokens, out_tokens


def report_cost(prompt_tokens: int, out_tokens: int, model: str, duration_hours: float) -> None:
    total = prompt_tokens + out_tokens

    print("\n===== COST =====")
    print(f"tokens: prompt={prompt_tokens:,} output={out_tokens:,} total={total:,}")

    usd = _lookup_cost(model, prompt_tokens, out_tokens)
    if usd is None:
        print("cost: LiteLLM has no price entry for this model — tokens shown above")
        return

    print(f"cost this video: ${usd:.4f}")
    if duration_hours > 0:
        per_hour = usd / duration_hours
        print(f"normalized: ${per_hour:.4f} / hour of video")
        print(f"PoC-A (yt-dlp+Groq+extract): ${POC_A['usd_per_hour']:.4f} / hour")
        ratio = per_hour / POC_A["usd_per_hour"] if POC_A["usd_per_hour"] else 0
        print(f"  -> Gemini-direct is {ratio:.1f}x PoC-A")
        for h in (30, 60, 135):
            print(f"  {h:>4} h/mo -> ${per_hour * h:6.2f}/mo  (PoC-A: ${POC_A['usd_per_hour'] * h:5.2f})")


def _lookup_cost(model: str, prompt_tokens: int, out_tokens: int) -> float | None:
    try:
        import litellm

        prompt_cost, completion_cost = litellm.cost_per_token(
            model=LITELLM_MODEL if model == DEFAULT_MODEL else f"gemini/{model}",
            prompt_tokens=prompt_tokens,
            completion_tokens=out_tokens,
        )
        return prompt_cost + completion_cost
    except Exception:
        return None


# ---- Modes ----


def extract_window(args, window, prompt, system, parse) -> tuple[list[dict], int, int]:
    """One clip -> its predictions with timestamps shifted to absolute video time."""
    start, end = window
    resp = call_gemini(args.url, prompt, args.model, args.media_res, system, window=window)
    text = resp.text or ""
    (OUT_DIR / f"poc_gemini_raw_{int(start)}.json").write_text(text, encoding="utf-8")

    prompt_tokens, out_tokens = tokens_of(resp.usage_metadata)
    predictions = []
    for pred in parse(text):
        relative = parse_timestamp(str(pred.get("timestamp", "")))
        pred["_abs_ts"] = None if relative is None else relative + start
        predictions.append(pred)
    print(f"      clip {int(start):>5}-{int(end):<5}s: {len(predictions):>2} predictions, "
          f"{prompt_tokens:,} in / {out_tokens:,} out")
    return predictions, prompt_tokens, out_tokens


def run_extract(args, duration_s: float) -> None:
    from prophet_checker.llm.prompts import get_extraction_system, parse_extraction_response

    prompt = VIDEO_EXTRACTION_PROMPT.format(
        person_name=args.person, published_date=args.published
    )
    system = get_extraction_system()
    windows = build_windows(duration_s, args.clip_seconds)
    print(f"[gemini] extracting  model={args.model} media_res={args.media_res} "
          f"clips={len(windows)}x{int(args.clip_seconds)}s")

    predictions: list[dict] = []
    total_in = 0
    total_out = 0
    for window in windows:
        got, tin, tout = extract_window(args, window, prompt, system, parse_extraction_response)
        predictions.extend(got)
        total_in += tin
        total_out += tout

    if not predictions:
        print("  ! parsed 0 predictions — raw responses saved to poc_gemini_raw_*.json")
        report_cost(total_in, total_out, args.model, duration_s / 3600)
        return

    chunks = load_whisper_chunks()
    print(f"\n===== PREDICTIONS: {len(predictions)} (PoC-A found {POC_A['predictions']}) =====\n")

    verified = 0
    for i, pred in enumerate(predictions, 1):
        claim = pred.get("claim_text", "")
        raw_ts = str(pred.get("timestamp", ""))
        ts = pred.get("_abs_ts")
        print(f"{i}. [{raw_ts}] {claim}")
        if ts is None:
            print("     timestamp: UNPARSEABLE")
            continue
        if ts > duration_s > 0:
            print(f"     ⚠ timestamp {raw_ts} is BEYOND video length ({duration_s:.0f}s) — hallucinated")
            continue
        verified += 1
        print(f"     link: https://www.youtube.com/watch?v={args.video_id}&t={int(ts)}s")
        print(f"     whisper@{int(ts)}s: {whisper_context(chunks, ts)}")
        print()

    print(f"timestamps within video bounds: {verified}/{len(predictions)}")
    print("(read the whisper@ lines above — does each claim actually appear there?)")
    report_cost(total_in, total_out, args.model, duration_s / 3600)


def run_transcript(args, duration_s: float) -> None:
    print(f"[gemini] requesting timestamped transcript  (model={args.model}, media_res={args.media_res}, "
          f"first {int(args.clip_seconds)}s)")
    resp = call_gemini(args.url, TRANSCRIPT_PROMPT, args.model, args.media_res, None,
                       window=(0.0, min(args.clip_seconds, duration_s)))

    text = resp.text or ""
    out = OUT_DIR / "poc_gemini_transcript.txt"
    out.write_text(text, encoding="utf-8")

    words = len(text.split())
    covered_s = min(args.clip_seconds, duration_s)
    expected = 16372 * covered_s / duration_s  # whisper words, prorated to the clip
    print(f"  transcript: {words:,} words / {len(text):,} chars -> {out.name}")
    print(f"  whisper baseline for the same {int(covered_s)}s: ~{expected:,.0f} words")
    if expected > 0:
        coverage = 100.0 * words / expected
        print(f"  coverage vs whisper: {coverage:.0f}%"
              + ("  ⚠ TRUNCATED — output-token limit bites" if coverage < 80 else ""))
    report_cost(*tokens_of(resp.usage_metadata), args.model, covered_s / 3600)


# ---- Entry ----


def warn_context(duration_s: float, media_res: str) -> None:
    rate = TOKENS_PER_SEC.get(media_res, 300)
    projected = duration_s * rate
    print(f"  projected video tokens: {projected:,.0f} ({rate} tok/s at media_res={media_res})")
    if projected > CONTEXT_LIMIT:
        print(f"  ⚠ exceeds ~{CONTEXT_LIMIT:,} context — request will likely fail; use --media-res low",
              file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="PoC-B: YouTube URL straight into Gemini")
    parser.add_argument("--url", required=True)
    parser.add_argument("--mode", choices=["extract", "transcript"], default="extract")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--media-res", choices=["low", "default"], default="low")
    parser.add_argument("--clip-seconds", type=float, default=3600,
                        help="video window per request; full-length requests 500 on long videos")
    parser.add_argument("--duration", type=float, default=6442, help="video seconds (from PoC-A)")
    parser.add_argument("--person", default="Олексій Арестович")
    parser.add_argument("--published", default="2022-08-10")
    parser.add_argument("--video-id", default="dtAbQYffGhg")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env")
    except Exception:
        pass
    if not os.environ.get("GEMINI_API_KEY"):
        raise SystemExit("missing GEMINI_API_KEY")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    warn_context(args.duration, args.media_res)

    if args.mode == "extract":
        run_extract(args, args.duration)
        return
    run_transcript(args, args.duration)


if __name__ == "__main__":
    main()
