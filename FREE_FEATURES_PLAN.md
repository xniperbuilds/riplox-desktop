# Free features — plan

Other downloaders charge between $15 and $70 for this category. Most of what
they charge for, Riplox already does. This is the plan for the rest of it, and
for saying so.

The promise is one line: **everything they put behind a licence, free, with no
caps.** Nothing here adds a paid tier, a trial, a quota or an account.

## Status

| Part | | |
|---|---|---|
| A | Write down what already exists | done for the README; the site and the store listing are still to do, and the comparison table is deliberately not written yet — see §7 |
| B | An 8K rung | done |
| C | Convert video, not only audio | done, including GIF |
| D | Schedule at a time | done |
| E | Player, macOS and Linux, site crawl, VR | not started, and deliberately after |

Covered by `tests/test_free_features.py`.

---

## Part A — write down what already exists

No code. The largest gap is not a feature, it is that twenty-six things Riplox
already does are not written anywhere a person or a search engine will find.

What the paid products gate, and Riplox does not:

| They limit | Riplox |
|---|---|
| 10 downloads a day (free tier of a major competitor) | no cap |
| one download at a time | as many as set |
| 10 videos from a playlist | the whole playlist, with filtering |
| 5 videos from a channel | the whole section |
| 1080p unless you pay | up to 4K, and higher through Max |
| ads in the free build | none |
| private / members-only / age-gated as a *premium feature* | sign in through your own browser |
| proxy as a *premium feature* | http, https and SOCKS |
| themes and a media library as a *premium subscription* | both, free |

And the ones sold as headline features elsewhere: subtitles in many languages,
embedded subtitles, chapter marks, trimming, audio extraction with cover art
and tags, batch paste, speed limiting, a browser extension, clipboard capture,
a global hotkey, desktop notifications, tray operation, finding every media
link on a page, following a channel, and taking each dubbed audio track as its
own file.

Three that no paid product has at all: **sending a link from a phone to the
PC**, **a second route when the engine is refused**, and **skipping sponsor
segments**.

**Where this goes:** the README feature table (it is already there — it needs a
comparison column, not new prose), the site page, and the store listing. Write
it in the words people search for: *free*, *no limit*, *no watermark*, *no
account*, *unlimited downloads*.

**One rule for this section:** every claim about a competitor must be checked
against that competitor's own page on the day it is written, and dated. A
comparison table that is wrong is worse than no comparison table. See §7.

---

## Part B — an 8K rung

**What is actually true today:** 8K already downloads. `max` and `best` ask the
site for its highest and take it, and the code says so in as many words —
*"which is exactly what 'highest' means on an 8K video"*. What is missing is a
**named rung**, so the word never appears in the interface and cannot appear on
a feature list either.

Three places, all small:

1. `QUALITY_LABELS` in `engine.py` — add `"4320": "8K · 4320p"`, above `2160`.
2. The rung loop, `for key in ("2160", "1440", ...)` — prepend `"4320"`. Rungs
   are only offered when a real format reaches that height, so the chip appears
   on 8K videos and nowhere else. No guard needed for the rest.
3. `_ASKED_HEIGHT` — add `"4320": 4320`, so the post-download height check knows
   what was asked for.

**The compatibility question, which must be answered on the chip, not in code.**
Play-anywhere prefers H.264. There is no H.264 at 8K — it is VP9 or AV1. The
documented behaviour already covers this (*"Where the highest resolution exists
only in a newer codec, Riplox takes the resolution"*), so the rung works; what
it must not do is silently hand back a file that will not open in Windows Media
Player. The 8K chip carries a short note saying the file is VP9/AV1 and needs a
modern player.

**Size:** the chip already shows the size of each rung, which is the existing
guard against meeting a very large download by surprise. Nothing to add.

---

## Part C — convert video, not only audio

`convert.py` today converts to MP3, M4A, OPUS, FLAC and WAV. `build_args()`
passes `-vn` unconditionally — video is dropped by design. This is the single
biggest feature the converter suites charge for, and ffmpeg already ships with
the app.

**Shape:** a second table beside `FORMATS`, not a rewrite.

```
VIDEO_FORMATS = {
    "mp4":  {"label": "MP4",  "vcodec": "libx264", "acodec": "aac",
             "copyable": ("h264", "avc1")},
    "mkv":  {"label": "MKV",  ...},   # copies almost anything
    "mov":  {"label": "MOV",  ...},
    "webm": {"label": "WebM", "vcodec": "libvpx-vp9", "acodec": "libopus", ...},
    "gif":  {"label": "GIF",  ...},   # its own path, see below
}
```

`build_args()` branches on which table the format came from. The audio path is
untouched. The video path:

- **Copy where possible.** A remux (`-c copy`) is instant and lossless; only
  re-encode when the codec cannot live in the target container. The audio path
  already works this way (`copyable`) — same idea, two streams.
- **Resolution** as a separate control: keep, or scale down to 1080p / 720p /
  480p (`-vf scale=-2:H`). Scaling up is not offered.
- **Quality** as three presets mapped to CRF, not a bitrate box.
- `-map_metadata 0` stays, and subtitle streams are carried where the container
  can hold them.

**GIF** is a third path, not a codec swap: palette generation then the encode,
capped in length and width, and reachable from the existing trim controls,
because a GIF of a whole video is never what anyone wanted.

**Guards:** refuse a target that would overwrite the source; `free_name()`
already exists for that. Keep the existing cancel behaviour — a video encode
runs long and must remain interruptible.

---

## Part D — schedule at a time

Today `schedule_from` / `schedule_to` hold *new* downloads outside a window,
defaulting to 01:00–08:00, and `schedule_note()` puts one line on the screen.
That is a window, not a schedule: there is no way to say "start this one at
2am".

**Add:** a per-item start time. A queued item may carry `start_after` (an epoch
seconds value); the queue skips it until then, and the row says when it will
begin, in the same voice `schedule_note()` already uses.

This pairs directly with the auto-download work in the Following plan: a
followed channel that downloads on its own should be able to do it overnight.

**Do not** build a calendar. One field, one line of text on the row.

---

## Part E — later, and deliberately after

- **Built-in player.** A competitor's entire paid tier rests on this plus a
  library. Riplox has the library. Two to three days, and worth doing once
  Parts A–D are in.
- **macOS and Linux.** The largest structural gap — every other serious tool in
  this category is cross-platform. Python and Flask make it possible; building,
  signing and testing on three platforms is what makes it weeks.
- **Crawling a whole site**, rather than one page. Wanted rarely, and the
  existing per-page finder covers most of the real need.
- **VR / 360°.** Sold as a headline elsewhere; in practice the file downloads
  like any other and only the metadata differs. Low value, low cost — do it if
  it ever blocks a comparison, not before.

## Borrowed from JDownloader

Four mechanisms were read out of JDownloader's own source and judged against
this app. Two are in; two are not, and the reasons are worth keeping.

**In: asking the server about an address.** JDownloader's link crawler decides
what is downloadable from the *answer*, not the address — response code, then
`Content-Disposition`, then content type, then a length with byte ranges, then
simply something far too big to be a page. "Find on a page" now does the same
for the addresses it cannot judge otherwise, capped at a dozen questions per
page so a page never becomes a scan of somebody's site.

**In: a folder that anything can write into.** JDownloader's `.crawljob` file
is its cheapest interface by a distance — no API, no auth, just a file — and
it is the only way in that a script or a scheduled task can use, because every
other one needs the app itself. Same idea here, with the two shapes people
actually write: a line per link, or JSON.

**Out: reading the clipboard's HTML flavour.** JDownloader reads the
`text/html` clipboard alongside the plain text, which carries a `SourceURL:`
the browser wrote — so links copied as part of a page resolve against the page
they came from. It is genuinely clever and it is worth doing. It is also
`CF_HTML` through `ctypes` on a thread that currently touches none of that,
and this app's clipboard watcher is one of the few things that must never
throw. Worth its own change, not a corner of this one.

**Out: the dynamic chunk splitter.** JDownloader keeps a byte map of the file
and sends each finishing connection back to the largest gap, which is better
than splitting a file into N parts up front. It does that because it *is* the
downloader. Riplox is not — yt-dlp fetches, with `concurrent_fragments` set to
16. Copying this would mean writing a second HTTP downloader beside the engine
that already works, to win on a case the engine already handles. Not worth it,
and it would be the largest untested surface in the app.

## Never

**DRM-protected streaming.** It is where the money in this category is, and it
is the one thing that would end the browser extension, close every store route
permanently, and put the project under a different body of law. Not a
maybe-later.

---

## Tests

Per part, in the existing style:

- **8K** — a fixture whose formats reach 4320 offers the rung; one that reaches
  only 2160 does not. Asking for `4320` and receiving 2160 is reported, exactly
  as the other rungs are.
- **Convert** — every video format round-trips a short fixture; a remux
  produces no re-encode; scaling down changes height and never increases it;
  the source file is never overwritten; cancel stops ffmpeg.
- **GIF** — length and width caps hold; a trim range is respected.
- **Schedule** — an item with `start_after` in the future is skipped and the
  row explains itself; one in the past runs; the existing window still applies.
- **Comparison claims (Part A)** — a source-level check that every competitor
  claim in the README carries a date. A claim without a date fails.

Run `tests/run_all.py` after each part.

## Order

1. **Part A** — write down the twenty-six. No code, largest effect.
2. **Part B** — the 8K rung. About an hour.
3. **Part C** — video convert and GIF. Two to three days.
4. **Part D** — schedule at a time. A day.
5. **Part E** — player, then the rest.

---

## 7. Measure before writing any comparison

The free-tier limits quoted in Part A came from reviews, not from the vendors'
own pages, and different reviews give slightly different numbers. Before any of
it is published:

- install the competitor's free build and **count** — downloads per day, videos
  per playlist, videos per channel, the resolution ceiling, concurrent
  downloads;
- record the date next to each number;
- re-check before each release that repeats the claim.

Publishing a competitor's limit without checking it makes this project the
thing it is arguing against.
