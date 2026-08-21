# YouTube PoC — step-by-step flow

Throwaway de-risking scripts used to decide whether YouTube is worth adding as a source.
**Conclusions and verdicts live in [`docs/youtube-source/`](../../docs/youtube-source/).** This file
records what was done, in which order, and how to repeat it.

Two independent experiments:

- **PoC-A** (`poc_youtube.py`) — download the audio ourselves, transcribe it, cut it into chunks, extract predictions. **The chosen path.**
- **PoC-B** (`poc_gemini_video.py`) — hand the YouTube URL straight to Gemini, never touch the audio. **Rejected.**

Both write into `artifacts/`. The audio blobs (`artifacts/poc_work/`, ~96 MB) are not committed to git —
one run recreates them in ~18 s.

---

## PoC-A — step by step

### What was actually being tested

Not "how to write a YouTubeSource", but **whether it is worth it at all**. Three unknowns, each of which
could kill the idea:

- YouTube bans datacenter IPs — but does the download work from a local machine?
- Is the auto transcript clean enough for ru/uk speech?
- What share of segments really contains predictions? (in Telegram it is ~6%)

### End-to-end flow — 7 steps

One numbering, in **execution** order. Steps 1-5 are Phase A, steps 6-7 are Phase B.

```bash
# Phase A — steps 1-5
uv run --with yt-dlp --with groq python scripts/youtube_poc/poc_youtube.py \
    --url "https://www.youtube.com/watch?v=dtAbQYffGhg" --lang auto

# Phase B — the same command, steps 1-7
... --extract
```

1. **`yt-dlp`** fetches the metadata (`id`, `title`, `upload_date`, `duration`) and downloads **the audio
   track only** (`format: bestaudio`). The `noplaylist` flag is mandatory — the URL carries `&list=...`,
   without it the whole playlist is downloaded.
2. **`ffmpeg`** re-encodes to 16 kHz mono opus 24k — to fit the Groq file limit (19.5 MB instead of 79).
3. **Groq `whisper-large-v3-turbo`**, `response_format=verbose_json`, `timestamp_granularities=["segment"]`
   → timed segments. The language comes from `--lang` (`auto` for mixed ru/uk).
   `_segments_as_dicts()` reduces the SDK response to `{"start", "end", "text"}`.
4. **`segment_transcript()`** glues whisper segments into chunks of ~750 words (≈5 min of speech) without
   cutting sentences, and carries the start timestamp of each chunk. **Its input is the output of step 3.**
5. Writes `artifacts/poc_transcript_full.txt` and `artifacts/poc_chunks.md` (with `?t=` deep links).

   ⏸ **Pause — the transcript is read by eye.** A bad transcript means there is no point in going further.

6. Every chunk is run through the project's **real `PredictionExtractor`**. To avoid duplicating the logic
   while still measuring the money, the script hands it a `MeteredLLM` — a wrapper with the same
   `.complete()` contract that additionally accumulates `litellm.completion_cost()` and tokens.
7. Computes the share of chunks that contain predictions and prints the cost breakdown: transcription
   (audio duration × the Groq rate) + extraction (real cost from LiteLLM), normalized per hour of video
   plus a monthly projection.

### Checking the pure logic (not a pipeline step)

```bash
uv run python scripts/youtube_poc/poc_youtube.py --selftest
```

Exercises `segment_transcript()` — the word budget, sentence boundaries, empty input, carrying the start
timestamp.

What it does **not** cover, and this matters: the test runs on **invented** segments, that is, on the
Whisper contract as guessed from the Groq documentation. The bridge from the real SDK response to that
shape — `_segments_as_dicts()` — is not covered by the test. If Groq returned a different structure, the
test would stay green while the pipeline was broken. **The Whisper contract was de-risked by the real run,
not by this test.**

### PoC-A result

| What | Value |
|---|---|
| Anti-bot | 79 MB in 18 s, no ban |
| Transcript quality | full punctuation, sentences intact |
| Density | **6/22 chunks = 27.3%** (11 predictions) vs ~6% in Telegram |
| Cost | **$0.053 per hour of video** end-to-end |

---

## PoC-B — step by step

Here the numbering is the **order of investigation**, not a pipeline: PoC-B has no pipeline, it has one API
call per video window.

### Step 1. Tell the button apart from the API

YouTube shows the user an "Ask about this video" button. That is **UI, not an API** — using it
programmatically would mean browser automation, that is, the same anti-bot surface. But the Gemini API
accepts a YouTube URL directly (`fileData.file_uri`), and then Google downloads the video.

### Step 2. Make the experiment fair

The script imports **the same** `EXTRACTION_SYSTEM` and **the same** `parse_extraction_response` from
`prophet_checker.llm.prompts`, and uses the same model. Exactly one thing differs — the **input modality**
(video instead of text). Otherwise we would be comparing prompts, not approaches.

### Step 3. Find the limit of the mechanism

The first run failed with `500 INTERNAL`. Diagnosis, one probe at a time:

| Probe | Result |
|---|---|
| Short 19 s video | ✅ OK (1703 tokens) — the mechanism works |
| Full 1.79 h video | ❌ `500 INTERNAL`, reproduced twice |
| Clips of 10 / 30 / 60 min via `video_metadata` offsets | ✅ all OK |

So the wall is not the context (586k < 1M) but the processing of a long video. Clipping is a working
workaround.

### Step 4. Head-to-head

```bash
uv run --with google-genai python scripts/youtube_poc/poc_gemini_video.py \
    --url "https://www.youtube.com/watch?v=dtAbQYffGhg"

# smaller windows — check whether the clip size is to blame for the low recall
... --clip-seconds 600 --duration 3600
```

The script cuts the video into windows, sends each one as a separate request, merges the predictions and
the tokens, and then **checks every timestamp**: next to each prediction it shows the text from the Whisper
transcript (`artifacts/poc_chunks.md`) at the same position. That makes it visible whether the claim is
really spoken there.

### PoC-B result

| Configuration | Predictions |
|---|---|
| PoC-A | **11** |
| Gemini-direct, 60 min windows | 4 (0 in the first hour) |
| Gemini-direct, 10 min windows | 2 in the first hour |

Rejected: it fails on completeness, its timestamp frame of reference is inconsistent (sometimes absolute,
sometimes relative), and it costs 1.6×. Details —
[`docs/youtube-source/2026-07-23-gemini-direct-poc-b.md`](../../docs/youtube-source/2026-07-23-gemini-direct-poc-b.md).

---

## Requirements

- `ffmpeg` in PATH (PoC-A)
- `GROQ_API_KEY` and `GEMINI_API_KEY` in the repo `.env` — the scripts read them themselves, nothing is hardcoded
- dependencies are installed ephemerally via `uv run --with`, they were not added to `pyproject.toml` (throwaway code)

## Files in `artifacts/`

| File | Where it comes from |
|---|---|
| `poc_transcript_full.txt` | PoC-A, the full Whisper transcript (16,372 words) |
| `poc_chunks.md` | PoC-A, 22 chunks with deep links — also the ground truth for checking PoC-B timestamps |
| `poc_report.md` | PoC-A, report of the first run (the canonical conclusions are in `docs/youtube-source/`) |
| `poc_gemini_raw_*.json` | PoC-B, raw responses per window (`_0`, `_600`… = start offset) |
