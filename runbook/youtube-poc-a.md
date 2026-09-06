# Runbook — YouTube PoC-A (fetch → transcribe → chunk → extract)

Runs one YouTube video end-to-end with the throwaway PoC script
[`scripts/youtube_poc/poc_youtube.py`](../scripts/youtube_poc/poc_youtube.py): downloads the audio,
transcribes it on Groq, cuts it into ~750-word chunks and — optionally — runs the project's real
`PredictionExtractor` over them and prints the real cost.

Use it to check a new video or a new channel before anyone builds `YouTubeSource`.

**Safe to run.** It touches no database, no orchestrator, no S3. Everything it writes lands in
`scripts/youtube_poc/artifacts/`. It does spend money — see *Cost* below.

PoC-B (YouTube URL straight to Gemini) is a different, rejected experiment and is not part of this
runbook.

## Prerequisites

- **`ffmpeg` in PATH.** `ffmpeg -version` must answer. Step 2 fails without it.
- **Keys in the repo `.env`:** `GROQ_API_KEY` (transcription) and `GEMINI_API_KEY` (extraction).
  The script loads `.env` itself and exits with `missing <KEY>` if one is absent. Both keys are
  required even for Phase A, because the check happens before any work starts.
- **Run from the repo root** (`cd ~/Brain/prediction-tracker`) — `.env` is found relative to it.
- **Disk:** ~96 MB per video for the raw audio in `artifacts/poc_work/`.
- No install step. Dependencies come in ephemerally through `uv run --with`.

## Phase A — fetch, transcribe, chunk

```bash
uv run --with yt-dlp --with groq python scripts/youtube_poc/poc_youtube.py --url "<VIDEO_URL>" --lang auto
```

Expect roughly a minute for a 1.5-hour video (the download was 18 s, Groq transcribes far above
realtime). A healthy run prints four steps — numbers below are from the first PoC video:

```
[1/4] downloading audio  (<url>)
      '<title>'  id=<id>  uploaded=YYYYMMDD  dur=<seconds>s
[2/4] transcoding to 16 kHz mono opus
      19.5 MB
[3/4] transcribing via Groq whisper-large-v3-turbo  (lang=auto)
      1917 whisper-segments -> 22 chunks (~750 words each)
      wrote poc_transcript_full.txt + poc_chunks.md to .../artifacts
[4/4] extraction skipped (Phase A). Eyeball the transcript, then re-run with --extract.
```

Then it prints the transcription cost and a per-hour figure.

**Stop here and read the transcript.** Open `artifacts/poc_transcript_full.txt`. You are checking
whether sentences are whole and punctuation is present. If the transcript is bad, extraction results
mean nothing — do not pay for step 4.

`artifacts/poc_chunks.md` holds the same text split into chunks, each with a `?t=` deep link back to
the exact second in the video.

## Phase B — prediction density and full cost

Same command plus `--extract`. It repeats steps 1-3 and then runs every chunk through the real
extractor:

```bash
uv run --with yt-dlp --with groq python scripts/youtube_poc/poc_youtube.py --url "<VIDEO_URL>" --lang auto --extract
```

Output to read:

- `density: 6/22 chunks (27.3%) contain predictions; 11 predictions total` — the number that decides
  whether the source is worth it. Baseline from the first PoC video: **27.3%**. Telegram sits at ~6%.
- Up to 5 example predictions, each with a deep link — click one and confirm the claim is really said there.
- The cost block: Groq transcription + LiteLLM extraction, normalized per hour of video, plus a
  monthly projection.

## Self-test (no network, no keys)

```bash
uv run python scripts/youtube_poc/poc_youtube.py --selftest
```

Checks `segment_transcript()` only: word budget, sentence boundaries, empty input, start timestamp.
It runs on invented segments, so it proves nothing about the Groq response shape.

## Flags

| Flag | Default | Meaning |
|---|---|---|
| `--url` | — | required, unless `--selftest` |
| `--lang` | `auto` | `auto` for mixed ru/uk, or force `ru` / `uk` |
| `--extract` | off | adds Phase B: extraction + full cost |
| `--selftest` | off | runs the segmenter test and exits |

## Cost

Measured end-to-end on the first PoC video: **$0.053 per hour of video** — Groq $0.040 + Gemini
extraction $0.013. A 1.5-hour video costs under 10 cents. Phase A alone is the Groq part only.

If the extraction line shows `$0.0000` with a note, LiteLLM has no price entry for that model —
the token counts on the next line are still real.

## Known traps

- **Every run overwrites the previous transcript.** `poc_transcript_full.txt` and `poc_chunks.md` carry
  no video id, so a second run replaces them (the audio files in `poc_work/` do keep the id, and survive).
  Two things follow. Save or rename them before processing another video, and remember that
  `poc_chunks.md` is also the ground truth PoC-B checks its timestamps against — see
  [`youtube-poc-b.md`](youtube-poc-b.md). As of 2026-08-23 the committed 1.79-hour chunk file had already
  been replaced by a short Portnikov video; the original survives only in git history and is restored with
  `git checkout -- scripts/youtube_poc/artifacts/poc_chunks.md`.
- **The transcript does not say who is speaking.** On an interview the host and the guest run together,
  so a prediction cannot be attributed to a person, and a 750-word chunk can mix both. Groq has no
  diarization option at all — see
  [`docs/youtube-source/2026-08-23-speaker-diarization-gap.md`](../docs/youtube-source/2026-08-23-speaker-diarization-gap.md).
- **Groq loses text at its internal boundaries.** Some segments come back with empty text although
  speech is there; the script's segmenter skips them silently. Roughly one segment per boundary is
  lost, so short videos suffer most: 13.9% of an 8.4-minute video versus 0.5% of a 1.79-hour one.
  Nothing in the output warns you. Rough sanity check: this content runs at ~150 words per minute, so
  a 10-minute video should give ~1500 words in `poc_transcript_full.txt`. Much less means a hole.
  Full analysis: [`docs/youtube-source/2026-08-21-groq-boundary-text-loss.md`](../docs/youtube-source/2026-08-21-groq-boundary-text-loss.md).
- **Files above 24 MB.** The script warns and sends the request anyway; Groq may reject it or return a
  truncated transcript. Roughly 3+ hours of video. Splitting the audio is not implemented.
- **Proper names get mangled**, confidently — check any name before quoting it.
- **`--extract` is a full second run.** There is no resume — steps 1-3 execute again before extraction.
- **Playlist URLs are safe.** `noplaylist` is set, so `&list=...` in the URL does not pull the whole
  playlist.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `missing GROQ_API_KEY` / `missing GEMINI_API_KEY` | Not run from the repo root, or the key is absent from `.env`. |
| `FileNotFoundError: 'ffmpeg'` | `ffmpeg` is not in PATH. Install it, then re-run. |
| `yt-dlp produced no audio file for <id>` | Download failed or the video is unavailable in this region. Re-run; if it repeats, try the URL in a browser. |
| Anti-bot / "confirm you're not a bot" | Do not run this on a cloud box. The PoC succeeded from a local machine on a residential IP; datacenter IPs are the known risk. |
| Transcript is mixed ru/uk nonsense | Force the language with `--lang uk` or `--lang ru` instead of `auto`. |

## Clean up

The audio blobs are not in git and are recreated in ~18 s:

```bash
rm -rf scripts/youtube_poc/artifacts/poc_work
```

## See also

- [`scripts/youtube_poc/README.md`](../scripts/youtube_poc/README.md) — how the PoC was built and why each step exists.
- [`docs/youtube-source/2026-07-23-youtube-poc-findings.md`](../docs/youtube-source/2026-07-23-youtube-poc-findings.md) — verdict, numbers, proposed Phase 2 design.
- [`youtube-poc-b.md`](youtube-poc-b.md) — the rejected alternative, kept as a fallback.
