"""YouTube ingestion PoC — fetch -> transcribe -> segment -> (optional) extract + cost.

Throwaway de-risking script for prediction-tracker. Does NOT touch orchestrator/DB.
Validates three risks before building a real Source:
  1. yt-dlp fetches + downloads audio from this Mac (residential IP) without an anti-bot ban.
  2. Groq whisper-large-v3-turbo gives clean-enough ru/uk transcript (punctuation, sentences).
  3. What % of ~750-word segments actually contain predictions (the whole justification).
Plus: real end-to-end cost per hour of video, extrapolated.

Run — Phase A (fetch + transcribe + segment, then eyeball quality):
    cd ~/Brain/prediction-tracker
    uv run --with yt-dlp --with groq python scripts/youtube_poc/poc_youtube.py \
        --url "https://www.youtube.com/watch?v=dtAbQYffGhg" --lang auto

Run — Phase B (adds prediction density + full cost breakdown):
    ... same command ... --extract

Self-test the pure segmenter (no network, no --with needed):
    uv run python scripts/youtube_poc/poc_youtube.py --selftest
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---- Constants (easy to update as pricing / models change) --------------------

GROQ_MODEL = "whisper-large-v3-turbo"
GROQ_USD_PER_HOUR = 0.04  # whisper-large-v3-turbo, audio-hour billed (2026)
EXTRACT_MODEL = "gemini/gemini-3.1-flash-lite-preview"  # prod extraction model
# Gemini native-audio transcription equivalent: 25 audio-tokens/s * $0.30 / 1M input tokens.
GEMINI_AUDIO_USD_PER_HOUR = 25 * 3600 / 1_000_000 * 0.30  # ~= $0.027/hr
MAX_WORDS = 750  # ~5 min of speech — enough context for LLM extraction
GROQ_MAX_MB = 24  # stay under Groq single-request file limit; chunking is out of PoC scope

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent / "artifacts"


# ---- Pure core: segmentation (unit-tested) ------------------------------------


@dataclass
class Chunk:
    start: float  # seconds into the video where this chunk begins
    text: str


def segment_transcript(segments: list[dict], max_words: int = MAX_WORDS) -> list[Chunk]:
    """Group timed whisper segments into ~max_words chunks, preserving sentence
    boundaries (whisper segments are already clause/sentence units) and carrying
    the start timestamp of each chunk's first segment.
    """
    chunks: list[Chunk] = []
    if not segments:
        return chunks

    buffer: list[str] = []
    start: float | None = None
    words = 0

    for seg in segments:
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        if start is None:
            start = float(seg.get("start", 0.0))
        buffer.append(text)
        words += len(text.split())
        if words >= max_words:
            chunks.append(Chunk(start=start, text=" ".join(buffer)))
            buffer = []
            start = None
            words = 0

    if buffer and start is not None:
        chunks.append(Chunk(start=start, text=" ".join(buffer)))
    return chunks


def deeplink(video_id: str, start: float) -> str:
    return f"https://www.youtube.com/watch?v={video_id}&t={int(start)}s"


# ---- Cost metering LLM (mirrors prophet_checker LLMClient.complete) ------------


@dataclass
class MeteredLLM:
    """Same .complete() contract as prophet_checker.llm.LLMClient, but records
    LiteLLM cost + token usage so the real PredictionExtractor can run unchanged."""

    model: str
    api_key: str
    total_cost: float = 0.0
    calls: int = 0
    in_tokens: int = 0
    out_tokens: int = 0
    cost_available: bool = True

    async def complete(self, prompt: str, system: str | None = None) -> str:
        import litellm

        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = await litellm.acompletion(
            model=self.model, messages=messages, api_key=self.api_key, num_retries=3
        )
        self.calls += 1
        self._record_cost(litellm, resp)
        self._record_tokens(resp)
        return resp.choices[0].message.content

    def _record_cost(self, litellm, resp) -> None:
        try:
            self.total_cost += litellm.completion_cost(completion_response=resp) or 0.0
        except Exception:
            self.cost_available = False

    def _record_tokens(self, resp) -> None:
        usage = getattr(resp, "usage", None)
        if not usage:
            return
        self.in_tokens += getattr(usage, "prompt_tokens", 0) or 0
        self.out_tokens += getattr(usage, "completion_tokens", 0) or 0


# ---- I/O steps (verified by eyeball, not mocked) ------------------------------


@dataclass
class VideoMeta:
    video_id: str
    title: str
    upload_date: str  # YYYYMMDD from yt-dlp
    duration: int  # seconds (yt-dlp metadata)


def download_audio(url: str, workdir: Path) -> tuple[Path, VideoMeta]:
    from yt_dlp import YoutubeDL

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(workdir / "%(id)s.%(ext)s"),
        "noplaylist": True,  # the given URL carries &list=... — force single video
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "opus", "preferredquality": "0"}
        ],
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    video_id = info["id"]
    src = workdir / f"{video_id}.opus"
    if not src.exists():
        matches = list(workdir.glob(f"{video_id}.*"))
        if not matches:
            raise RuntimeError(f"yt-dlp produced no audio file for {video_id}")
        src = matches[0]

    meta = VideoMeta(
        video_id=video_id,
        title=info.get("title", ""),
        upload_date=info.get("upload_date", ""),
        duration=int(info.get("duration") or 0),
    )
    return src, meta


def transcode_for_asr(src: Path, dst: Path) -> Path:
    """Downsample to 16 kHz mono Opus — tiny file, ASR-friendly, keeps us under
    the Groq single-request size limit for a normal-length efir."""
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "24k",
        str(dst),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dst


async def transcribe(audio_path: Path, lang: str) -> tuple[list[dict], str, float]:
    from groq import AsyncGroq

    size_mb = audio_path.stat().st_size / 1_000_000
    if size_mb > GROQ_MAX_MB:
        print(
            f"  ! audio is {size_mb:.1f} MB (> {GROQ_MAX_MB} MB Groq limit) — "
            f"chunking is out of PoC scope; transcript may be truncated/rejected",
            file=sys.stderr,
        )

    client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
    with open(audio_path, "rb") as fh:
        payload = fh.read()

    kwargs: dict = {
        "file": (audio_path.name, payload),
        "model": GROQ_MODEL,
        "response_format": "verbose_json",
        "timestamp_granularities": ["segment"],
    }
    if lang != "auto":
        kwargs["language"] = lang

    resp = await client.audio.transcriptions.create(**kwargs)
    segments = _segments_as_dicts(resp)
    duration = _resp_duration(resp, segments)
    return segments, getattr(resp, "text", ""), duration


def _segments_as_dicts(resp) -> list[dict]:
    raw = getattr(resp, "segments", None) or []
    out: list[dict] = []
    for seg in raw:
        if isinstance(seg, dict):
            out.append(seg)
            continue
        out.append(
            {
                "start": getattr(seg, "start", 0.0),
                "end": getattr(seg, "end", 0.0),
                "text": getattr(seg, "text", ""),
            }
        )
    return out


def _resp_duration(resp, segments: list[dict]) -> float:
    duration = getattr(resp, "duration", None)
    if duration:
        return float(duration)
    if segments:
        return float(segments[-1].get("end", 0.0))
    return 0.0


# ---- Phase B: prediction density + extraction cost ----------------------------


@dataclass
class DensityResult:
    chunks_total: int = 0
    chunks_with_predictions: int = 0
    predictions_total: int = 0
    examples: list[str] = field(default_factory=list)

    @property
    def density_pct(self) -> float:
        if self.chunks_total == 0:
            return 0.0
        return 100.0 * self.chunks_with_predictions / self.chunks_total


async def run_extraction(chunks: list[Chunk], meta: VideoMeta, llm: MeteredLLM) -> DensityResult:
    from prophet_checker.analysis.extractor import PredictionExtractor

    extractor = PredictionExtractor(llm)
    published = _iso_date(meta.upload_date)
    result = DensityResult(chunks_total=len(chunks))

    for idx, chunk in enumerate(chunks):
        outcome = await extractor.extract(
            text=chunk.text,
            person_id="poc",
            document_id=f"yt:{meta.video_id}:{idx}",
            person_name=meta.title,
            published_date=published,
        )
        if not outcome.predictions:
            continue
        result.chunks_with_predictions += 1
        result.predictions_total += len(outcome.predictions)
        if len(result.examples) < 5:
            result.examples.append(f"[{deeplink(meta.video_id, chunk.start)}] {outcome.predictions[0].claim_text}")
    return result


def _iso_date(yyyymmdd: str) -> str:
    if len(yyyymmdd) == 8 and yyyymmdd.isdigit():
        return f"{yyyymmdd[0:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
    return "1970-01-01"


# ---- Reporting ----------------------------------------------------------------


def write_transcript_files(meta: VideoMeta, full_text: str, chunks: list[Chunk]) -> None:
    (OUT_DIR / "poc_transcript_full.txt").write_text(full_text, encoding="utf-8")

    lines = [f"# {meta.title}", f"video: {meta.video_id} | uploaded: {meta.upload_date}", ""]
    for idx, chunk in enumerate(chunks):
        lines.append(f"## Chunk {idx}  —  {deeplink(meta.video_id, chunk.start)}")
        lines.append(chunk.text)
        lines.append("")
    (OUT_DIR / "poc_chunks.md").write_text("\n".join(lines), encoding="utf-8")


def print_cost(meta: VideoMeta, audio_seconds: float, density: DensityResult | None, llm: MeteredLLM | None) -> None:
    hours = audio_seconds / 3600 if audio_seconds else 0.0
    groq_cost = hours * GROQ_USD_PER_HOUR
    gemini_audio_cost = hours * GEMINI_AUDIO_USD_PER_HOUR

    print("\n===== COST =====")
    print(f"audio: {audio_seconds:.0f}s ({hours:.2f} h)")
    print(f"transcription (Groq {GROQ_MODEL}):  ${groq_cost:.4f}")
    print(f"  vs Gemini native-audio (analytical): ${gemini_audio_cost:.4f}")

    if density is None or llm is None:
        print("extraction: (skipped — run with --extract)")
        _print_per_hour(hours, groq_cost, 0.0, extract_known=False)
        return

    extract_cost = llm.total_cost
    note = "" if llm.cost_available else "  (LiteLLM cost unavailable for this model — shows $0)"
    print(f"extraction (LiteLLM {EXTRACT_MODEL}): ${extract_cost:.4f}{note}")
    print(f"  calls={llm.calls} in_tokens={llm.in_tokens} out_tokens={llm.out_tokens}")
    _print_per_hour(hours, groq_cost, extract_cost, extract_known=True)


def _print_per_hour(hours: float, groq_cost: float, extract_cost: float, extract_known: bool) -> None:
    if hours <= 0:
        return
    per_hour = (groq_cost + extract_cost) / hours
    label = "transcribe+extract" if extract_known else "transcribe only"
    print(f"\nnormalized: ${per_hour:.4f} / hour of video ({label})")
    print("illustrative monthly projection (plug in your own volume):")
    for h in (30, 60, 135):
        print(f"  {h:>4} h/mo  ->  ${per_hour * h:6.2f}/mo")


# ---- Orchestration ------------------------------------------------------------


async def run(url: str, lang: str, do_extract: bool, workdir: Path) -> None:
    print(f"[1/4] downloading audio  ({url})")
    src, meta = download_audio(url, workdir)
    print(f"      {meta.title!r}  id={meta.video_id}  uploaded={meta.upload_date}  dur={meta.duration}s")

    print("[2/4] transcoding to 16 kHz mono opus")
    asr_path = transcode_for_asr(src, workdir / f"{meta.video_id}.16k.ogg")
    print(f"      {asr_path.stat().st_size / 1_000_000:.1f} MB")

    print(f"[3/4] transcribing via Groq {GROQ_MODEL}  (lang={lang})")
    segments, full_text, audio_seconds = await transcribe(asr_path, lang)
    chunks = segment_transcript(segments)
    print(f"      {len(segments)} whisper-segments -> {len(chunks)} chunks (~{MAX_WORDS} words each)")

    write_transcript_files(meta, full_text, chunks)
    print(f"      wrote poc_transcript_full.txt + poc_chunks.md to {OUT_DIR}")

    if not do_extract:
        print("[4/4] extraction skipped (Phase A). Eyeball the transcript, then re-run with --extract.")
        print_cost(meta, audio_seconds, density=None, llm=None)
        return

    print(f"[4/4] extracting predictions via {EXTRACT_MODEL}  ({len(chunks)} chunks)")
    llm = MeteredLLM(model=EXTRACT_MODEL, api_key=os.environ["GEMINI_API_KEY"])
    density = await run_extraction(chunks, meta, llm)
    print(f"      density: {density.chunks_with_predictions}/{density.chunks_total} chunks "
          f"({density.density_pct:.1f}%) contain predictions; {density.predictions_total} predictions total")
    for ex in density.examples:
        print(f"        • {ex}")
    print_cost(meta, audio_seconds, density, llm)


def load_env(repo_root: Path) -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(repo_root / ".env")
    except Exception:
        pass
    for key in ("GROQ_API_KEY", "GEMINI_API_KEY"):
        if not os.environ.get(key):
            raise SystemExit(f"missing {key} — run from the prediction-tracker repo (.env not loaded)")


# ---- Self-test for the pure segmenter -----------------------------------------


def _selftest() -> None:
    # splits at the word budget, preserving whole (sentence) segments
    segs = [{"start": 0.0, "text": "a b c."}, {"start": 5.0, "text": "d e f."}, {"start": 9.0, "text": "g h."}]
    chunks = segment_transcript(segs, max_words=3)
    assert len(chunks) == 3, chunks  # each 2-3-word segment hits the budget on its own
    assert chunks[0].start == 0.0 and chunks[1].start == 5.0 and chunks[2].start == 9.0

    # accumulates under budget into one chunk carrying the first start
    chunks = segment_transcript(segs, max_words=100)
    assert len(chunks) == 1
    assert chunks[0].start == 0.0
    assert chunks[0].text == "a b c. d e f. g h."

    # empty / blank input
    assert segment_transcript([]) == []
    assert segment_transcript([{"start": 1.0, "text": "   "}]) == []

    print("selftest OK")


def main() -> None:
    parser = argparse.ArgumentParser(description="YouTube ingestion PoC")
    parser.add_argument("--url", help="YouTube video URL")
    parser.add_argument("--lang", default="auto", help="auto | ru | uk (forced language for Whisper)")
    parser.add_argument("--extract", action="store_true", help="Phase B: run prediction extraction + cost")
    parser.add_argument("--selftest", action="store_true", help="run the pure-segmenter self-test and exit")
    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return
    if not args.url:
        raise SystemExit("--url is required (or use --selftest)")

    load_env(REPO_ROOT)
    workdir = OUT_DIR / "poc_work"
    workdir.mkdir(parents=True, exist_ok=True)
    asyncio.run(run(args.url, args.lang, args.extract, workdir))


if __name__ == "__main__":
    main()
