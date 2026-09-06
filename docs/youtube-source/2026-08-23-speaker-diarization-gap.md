# Groq Whisper cannot tell two speakers apart

**Date:** 2026-08-23
**Status:** gap verified against the vendor docs; no provider chosen, nothing implemented
**Applies to:** the transcription path chosen in [PoC-A](2026-07-23-youtube-poc-findings.md)

## Why this exists

A prediction only has value when it is attached to a person. On a monologue broadcast that is free —
whoever owns the channel said everything in it. On an interview it is not: the host and the guest both
make claims, and the pipeline has no way to tell them apart.

This was found by running PoC-A on a two-speaker video
([`KKkX34Yw-bs`](https://www.youtube.com/watch?v=KKkX34Yw-bs), host + guest). The transcript comes back
as one continuous stream of speech with no marker of who is talking.

## What was established

**Groq's speech-to-text API has no diarization.** The documented parameters are `model`, `language`,
`response_format`, `timestamp_granularities`, `prompt` and `temperature` — there is no speaker option
of any kind (checked at [console.groq.com/docs/speech-to-text](https://console.groq.com/docs/speech-to-text)
on 2026-08-23). This is not a Groq omission: Whisper itself produces no speaker labels, so no setting
on the current path can produce them.

## Why it is worse than one missing field

Two consequences follow, and neither is solved by simply switching provider.

**Speaker labels are not names.** Every diarization service returns `Speaker A` / `Speaker B`, derived
from voice similarity. Attaching the right person to a label is a separate step — from the title, the
description, or by asking the LLM on the first chunk. That work exists no matter which option below is
chosen.

**Chunking would cut across speakers.** `segment_transcript()`
([`scripts/youtube_poc/poc_youtube.py:55`](../../scripts/youtube_poc/poc_youtube.py#L55)) glues ~750
words together without looking at who is speaking. A chunk that contains the end of a host question and
the start of a guest answer gives the extractor no way to attribute the claim — and the failure is
silent, exactly like the [Groq boundary text loss](2026-08-21-groq-boundary-text-loss.md). So speaker
turns have to become a chunk boundary, not just an annotation.

## The options, and what they cost

Prices below are vendor list prices read on 2026-08-23, not measured on our content.

| Option | Price per hour | Note |
|---|---|---|
| Groq `whisper-large-v3-turbo` (today) | $0.04 | no diarization at any price |
| AssemblyAI | ~$0.15–0.21 + ~$0.02 diarization | cheapest of the labelled options |
| ElevenLabs Scribe v2 | ~$0.22–0.40 | claims 98% speaker-label accuracy |
| Deepgram Nova-3 | ~$0.46 + ~$0.12 diarization | most expensive |
| pyannote.audio / WhisperX, self-hosted | $0 | keeps Groq; needs a GPU and a merge step |
| Gemini native audio + "label the speakers" prompt | ~$0.027 | key already in the project |

Diarization-capable providers are 5–15× the current transcription price. At the 135 hours/month figure
used in PoC-A, transcription moves from about $5 to about $25–80 per month. That is affordable, but it
ends the "cost is irrelevant" argument that PoC-A relied on.

The Gemini row carries a known risk: PoC-B showed that Gemini skips large parts of long media, so its
coverage would have to be measured before trusting it.

## Requirement for Phase 2 (proposed, not decided)

The `TranscriptProvider` contract already has to close holes from the boundary-loss finding. Speaker
labels belong in the same contract: segments should carry a speaker id, chunking should break on speaker
turns, and a naming step should map ids to real people before extraction runs.

## Open questions

- **Which provider.** Nothing here was tested on our audio; the choice is open.
- **Quality on mixed ru/uk speech is unknown.** Every accuracy claim above is vendor-published and
  English-centric. Our content switches language mid-sentence, which is the hard case for diarization.
- **Whether the extractor could infer the speaker from context** instead, cheaply. Untested.
- **Whether `pyannote` is fast enough on this Mac** without a GPU. Untested.
- **What to do with the host's own predictions** — discard, or store under the host as a person.

## How to reproduce

```bash
uv run --with yt-dlp --with groq python scripts/youtube_poc/poc_youtube.py \
    --url "https://www.youtube.com/watch?v=KKkX34Yw-bs" --lang auto
```

Read `artifacts/poc_transcript_full.txt`: questions and answers run together with nothing separating them.
