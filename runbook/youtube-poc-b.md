# Runbook — YouTube PoC-B (YouTube URL straight into Gemini)

Sends a YouTube URL to the Gemini API and asks the model to extract predictions from the **video
itself** — no download, no ffmpeg, no Whisper. Script:
[`scripts/youtube_poc/poc_gemini_video.py`](../scripts/youtube_poc/poc_gemini_video.py).

**This approach was tested and rejected** on 2026-07-23: 4 predictions against PoC-A's 11, inconsistent
timestamps, 1.6× the cost. It is kept as a fallback, because its one advantage is real — Google
downloads the video, so there is no anti-bot risk and the source could run on a cloud box.

Run it when you want to re-check whether video-understanding models have improved, or when yt-dlp
starts getting banned and the fallback suddenly matters. For normal work use
[`youtube-poc-a.md`](youtube-poc-a.md).

**Safe to run.** No database, no orchestrator, no S3. Everything lands in
`scripts/youtube_poc/artifacts/`.

## Prerequisites

- **`GEMINI_API_KEY` in the repo `.env`.** No Groq key is needed — this script never transcribes.
- **Run from the repo root** (`cd ~/Brain/prediction-tracker`).
- **`artifacts/poc_chunks.md` from PoC-A**, if you want the timestamp check. See the trap below.
- No `ffmpeg`, no disk space for audio. Dependencies come in through `uv run --with`.

## Extract mode — the head-to-head

```bash
uv run --with google-genai python scripts/youtube_poc/poc_gemini_video.py --url "<VIDEO_URL>" --duration <SECONDS> --video-id <ID> --person "<NAME>" --published <YYYY-MM-DD>
```

The four extra flags matter — see *The defaults are for one specific video* below. For the original
PoC video the flags can all be omitted.

The script cuts the video into windows (60 min by default), sends **one request per window**, and
prints for each prediction: the claim, a `?t=` deep link, and the Whisper text from PoC-A at the same
second, so you can read whether the claim is really spoken there.

```
[gemini] extracting  model=gemini-3.1-flash-lite-preview media_res=low clips=2x3600s
      clip     0-3600 s:  0 predictions, 360,123 in / 7 out
      clip  3600-6442 s:  4 predictions, 284,004 in / 512 out
```

Output to read, in this order:

1. **Prediction count** against the printed PoC-A baseline of 11. This is the number that rejected the
   approach.
2. **Output tokens per clip.** 5–9 output tokens means "found nothing" for a whole hour of speech —
   the sign that the model skims the video instead of listening to it.
3. **`timestamps within video bounds: N/M`** plus the `whisper@` line under each prediction. In the
   original run the claims were real but the time base was inconsistent — sometimes measured from the
   start of the video, sometimes from the start of the clip. There is no systematic correction.
4. **The cost block** — per hour of video, next to PoC-A's $0.053.

## Transcript mode — does a long transcript survive?

```bash
uv run --with google-genai python scripts/youtube_poc/poc_gemini_video.py --url "<VIDEO_URL>" --mode transcript --clip-seconds 600 --duration <SECONDS>
```

Asks for a verbatim timestamped transcript of the first `--clip-seconds` and writes
`artifacts/poc_gemini_transcript.txt`. It then compares the word count with what Whisper produced for
the same span and prints a coverage percentage; under 80% it prints `⚠ TRUNCATED`, which means the
output-token limit cut the answer.

## Flags

| Flag | Default | Meaning |
|---|---|---|
| `--url` | — | required |
| `--mode` | `extract` | `extract` or `transcript` |
| `--model` | `gemini-3.1-flash-lite-preview` | the project's production extraction model |
| `--media-res` | `low` | `low` = 100 video tokens/s, `default` = 300 |
| `--clip-seconds` | `3600` | video window per request |
| `--duration` | `6442` | video length in seconds — **not detected, you must pass it** |
| `--person` | Олексій Арестович | goes into the prompt |
| `--published` | `2022-08-10` | goes into the prompt |
| `--video-id` | `dtAbQYffGhg` | used to build the `?t=` deep links |

## Cost

Measured on the original video: **$0.083 per hour of video**, 1.6× PoC-A. Almost all of it is input
tokens — a 60-minute clip at `media_res=low` is ~360k tokens.

`media_res=default` triples that (300 tokens/s), and a 1.79-hour video then projects to ~1.9M tokens,
above the ~1M context. The script warns before sending. Keep `low` unless you are testing exactly
this.

If the cost line says LiteLLM has no price entry for the model, the token counts printed above it are
still real.

## Known traps

- **The defaults are for one specific video.** `--duration`, `--video-id`, `--person` and `--published`
  all default to the original Arestovych video. Nothing is read from YouTube. If you forget them on a
  new video: the window count and the cost per hour are wrong, the bounds check compares against the
  wrong length, and every deep link points at the wrong video — silently.
- **The `whisper@` ground truth may be from another video.** The script reads
  `artifacts/poc_chunks.md`, and every PoC-A run overwrites that file. Check its first line — it names
  the video — before you trust a single `whisper@` line. If the file is missing, the script still runs
  and prints `(no PoC-A chunks available)`.
- **A full-length request fails.** The complete 1.79-hour video returns `500 INTERNAL`, reproduced
  twice. It is not the context limit (586k < 1M) but the processing of long video. Clips of 10, 30 and
  60 minutes all work, so keep `--clip-seconds` at 3600 or below.
- **Timestamps come from the model, not from a clock.** Unlike PoC-A, where Whisper produces them
  mechanically, here they are generated text. Treat every one as unverified until the `whisper@` line
  confirms it.
- **Smaller windows do not fix recall.** 10-minute windows gave 2 predictions for the first hour
  against PoC-A's 11 for the whole video. Tested, not assumed.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `missing GEMINI_API_KEY` | Not run from the repo root, or the key is absent from `.env`. |
| `500 INTERNAL` | The window is too long. Lower `--clip-seconds`. |
| `parsed 0 predictions` | Read `artifacts/poc_gemini_raw_<start>.json` — the raw answer is saved before parsing, so you can tell "model found nothing" from "parser could not read it". |
| Deep links open the wrong video | `--video-id` was left at its default. |
| Coverage warning in transcript mode | Expected on long clips: the output-token limit truncates the answer. |

## Artifacts

| File | Content |
|---|---|
| `artifacts/poc_gemini_raw_<start>.json` | the raw answer for the window starting at `<start>` seconds |
| `artifacts/poc_gemini_transcript.txt` | transcript mode output |

Both are inside the git-ignored `artifacts/` directory.

## See also

- [`docs/youtube-source/2026-07-23-gemini-direct-poc-b.md`](../docs/youtube-source/2026-07-23-gemini-direct-poc-b.md) — the full verdict and the numbers behind it.
- [`scripts/youtube_poc/README.md`](../scripts/youtube_poc/README.md) — how the experiment was designed and why it is a fair comparison.
- [`youtube-poc-a.md`](youtube-poc-a.md) — the chosen path.
