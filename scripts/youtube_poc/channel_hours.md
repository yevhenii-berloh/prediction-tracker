# How many video hours does a channel hold?

Metadata only — nothing is downloaded. Multiply the result by $0.053/hour (the PoC-A end-to-end rate)
to size the corpus before ingesting it.

```bash
uv run --with yt-dlp yt-dlp --flat-playlist --print "%(duration)s" "https://www.youtube.com/@portnikov.argumenty/videos" | awk '/^[0-9]+$/ {s+=$1; n++} END {printf "%d videos, %.1f hours\n", n, s/3600}'
```

`--flat-playlist` walks the channel listing page by page instead of fetching each video, and the `awk`
skips entries with no duration (upcoming or live items).

Two things change the number. The `/videos` tab excludes Shorts and past live streams — run the same
command on `/shorts` and `/streams` and add them up. And only public videos are visible: members-only,
unlisted and deleted ones are not counted.

If durations come back empty for a channel, drop `--flat-playlist`. It then fetches each video's
metadata — accurate, but slow on a large channel.
