# Following — plan

The feature that watches channels and playlists for new videos. It shipped as
**Watch**; the user-facing name is now **Following**, and this document is the
plan for finishing it.

## Status

| Phase | | |
|---|---|---|
| 1 | Read the feed, with fallback | done |
| 2 | Open up the limits, and rewrite the consent text | done |
| 3 | The catch-up | done |
| 4 | Download without being asked — opt-in, capped | done |
| 5 | Per-item options | done for interval, quality, folder, date, title rules, and export/import. **Not** done: length rules, per-item account, an after-download command, retention, per-item notification — see below |
| 6 | Say it out loud | the app and the README say it; the site and the store listing do not yet |

Covered by `tests/test_following.py`.

Nothing here renames a module, an element id, an API route, a settings key or
`watch.json`. Those stay exactly as they are — renaming them would break saved
state on installs that already exist, and buy nothing a label change does not
already buy.

---

## 1. Where it stands today

`src/watch.py`, 423 lines, plus `/api/watch/*` in `src/app.py` and the
`view-watch` section of the template.

| | |
|---|---|
| Check | `engine.peek()` → `yt-dlp -J --flat-playlist --no-progress --playlist-end 30` |
| Interval | 6 / 12 / 24 / 48 hours, default 12 |
| Items | 20 maximum |
| Pacing | one request in flight, 60 s tick, 90 s minimum spacing, `check_all()` walks the list with 90 s gaps |
| Memory | 400 seen ids per item, 60 unseen held |
| Downloads | **never on its own** — it lists what is new and waits |
| Consent | a fifteen-second dialog before it can be switched on |
| Failure | bot-check detected and flagged per item, with a three-step recovery panel |

The pacing, the 20-item cap and the six-hour floor all exist for one reason:
every check is a real request to the site, made by a program, on a timer. That
is the thing YouTube answers with *"Sign in to confirm you're not a bot"*.

## 2. The change, in one line

**Stop asking the site as a program. Read the feed the site already publishes.**

Every YouTube channel and playlist has a public Atom feed:

```
https://www.youtube.com/feeds/videos.xml?channel_id=UC…
https://www.youtube.com/feeds/videos.xml?playlist_id=PL…
```

Verified 2026-09-04: both return valid Atom. 15 entries for a channel, 16 for
an uploads playlist. Each entry carries `yt:videoId`, `title`, `published`,
`media:thumbnail`, `media:description` and view/rating counts. No sign-in, no
cookies, no yt-dlp process, roughly ten kilobytes.

A plain GET of a published feed is not scraping, and it is not what triggers a
bot check. Once the routine check is a feed read, the 20-item cap, the 90-second
spacing and the six-hour floor are all guarding against something that no longer
happens.

**What the feed cannot do**, and why the old path stays:

- It carries only the newest ~15 items. Fine for watching; useless for a first
  import, which is why `peek()` still does the baseline on add.
- It has **no duration and no kind field**, so Shorts and livestreams cannot be
  told apart from the feed alone. Any filter that needs duration needs a lookup.
- It can miss things. Pinchflat, which does this for a living, uses RSS by
  default and still says it *"may occasionally miss content in edge cases"* —
  and runs a periodic full index to catch them. So do we (phase 3).
- It is YouTube-only. Every other site keeps the current `peek()` path.

---

## 3. Phase 1 — read the feed

**Resolve once, at add time.** `add()` already calls `peek()` for the baseline;
that same response is where the id comes from.

- A playlist URL carries `list=` → `playlist_id`.
- A channel URL may be `/channel/UC…` (id is in the path), or `@handle`,
  `/c/name`, `/user/name` (id is not). For those, take the `channel_id` out of
  the page or out of the `peek()` payload, once, and store it.
- A channel *tab* (Videos / Shorts / Live) is really a playlist —
  uploads is `UU` + the channel id minus its `UC` prefix. Store the tab's own
  playlist id where one exists, otherwise fall back to the channel feed.
- Store nothing you cannot verify: if no id can be resolved, the item simply
  keeps the current yt-dlp path. Mixed items in one list is fine.

New fields on an item: `feed` (the full feed URL, or `""`), `feed_fail` (count
of consecutive feed errors), `full_checked` (timestamp of the last `peek()`).

**The check itself.** `check()` gains a front door:

1. If `item["feed"]` is set and `feed_fail < 3`, GET it with a short timeout and
   an ordinary browser user-agent. Parse `entry/yt:videoId`, `title`,
   `published`, `media:thumbnail`.
2. Anything not in `known` is new, exactly as now. The rest of `check()` —
   `known`/`fresh` bookkeeping, caps, the tray notice — does not change.
3. On HTTP error, timeout or unparseable body: increment `feed_fail`, fall
   through to `peek()` for this round. Three consecutive failures and the item
   goes back to the yt-dlp path permanently until a full check succeeds.
4. `_asking`, the single-in-flight lock, stays. It costs nothing and it still
   protects the `peek()` path.

Use the standard library for the parse (`xml.etree.ElementTree`) — no new
dependency. Register the two namespaces (`atom`, `yt`, `media`) explicitly
rather than string-matching tags.

**Guard:** never accept a feed that returns zero entries as "nothing new" on the
first read after a resolve. Zero entries means the id is wrong, not that the
channel is quiet.

## 4. Phase 2 — open up the limits

Only after phase 1 is in.

| | Now | After |
|---|---|---|
| Interval choices | 6 / 12 / 24 / 48 h | **15 min / 1 h / 6 h / 24 h**, default 1 h |
| `MAX_ITEMS` | 20 | **100** |
| `SPACING` | 90 s | 90 s for `peek()` items, **5 s** for feed items |
| `TICK` | 60 s | unchanged |

The interval selector needs a per-item override (phase 5); the global setting
becomes the default for new items rather than the rule for all of them.

The consent dialog must be rewritten in this phase, not left to drift. Its
current text — *"the newest 30 items only, one check at a time, at least 90
seconds apart, and once every 12 hours per channel"* — stops being true here.
The honest replacement says: reads a published feed, no sign-in, and the
yt-dlp path only for the occasional full check.

## 5. Phase 3 — the catch-up

One `peek()` per item, at most once a week, spread out — not all on the same
day. It exists to repair what the feed missed.

- Pick items where `now - full_checked > 7 days`, one per tick, honouring the
  90-second spacing that still applies to `peek()`.
- Anything it finds that is not in `known` is new, and is announced normally.
- On success, reset `feed_fail` to 0.

This is also the natural place to prune `known` (it is capped at 400 ids) and to
drop `fresh` entries older than, say, 60 days.

## 6. Phase 4 — download without being asked

This is the only real gap against the competition, and it is the one thing in
this plan that changes a promise the app currently makes.

**Per item**, three states: `off` (default, exactly today's behaviour), `ask`,
`auto`. Plus a global master switch, off until the user turns it on once.

Guardrails, all of them, not a subset:

- at most **N new items per check** (default 3) — a channel that publishes
  forty things overnight must not fill a drive;
- an optional **maximum file size**;
- an optional **duration range**, which requires a lookup per candidate, so it
  only runs when auto-download is on for that item;
- everything still goes through the normal queue, visible, cancellable, with
  the normal per-item quality and folder;
- a notification that says what was taken, not just that something was.

**The promise.** `watch.py` opens with a docstring saying it *"never downloads
anything by itself"*, and the consent dialog repeats it. That text has to change
with the code, in the same commit, to the promise that is actually true:
**nothing downloads unless you turned it on for that channel.** Leaving the old
sentence in place while the behaviour changes is worse than not building the
feature.

## 7. Phase 5 — per-item options

An **Edit** panel on each item. Everything here is per item, falling back to the
global default when unset.

| Option | Notes |
|---|---|
| Check every | 15 min / 1 h / 6 h / 24 h — a monthly uploader does not need hourly |
| Quality / format | one channel 1080p MP4, another audio-only |
| Save to | folder, plus a filename template |
| Only after | a date — skip the back catalogue |
| Length between | min/max minutes; the practical way to drop Shorts. Needs a lookup, see phase 1 |
| Title contains / does not contain | plain words for everyone, regex behind an advanced toggle |
| Account | for members-only and private lists; accounts already exist in the app |
| After download | run a command — move the file, refresh a media server |
| Keep only newest N | optional retention |
| Notify | tray / silent / badge only |

**What is built, and what is not.** Interval, quality, folder, "only after"
and the two title rules are in, each falling back to the setting when left
empty, and the list exports and imports as a file. Four are not:

- **Length between** needs a duration, and the feed carries none — so it needs
  a lookup per candidate, which is the one thing the feed change was meant to
  avoid. It belongs with auto-download's other guardrails, not here.
- **Per-item account** and **after-download command** are both small, and both
  wanted rarely enough that they can wait for someone to ask.
- **Keep only newest N** deletes files. That is a different kind of feature and
  should not arrive as a checkbox inside a panel of filters.

A filtered video is still written into `known`. Without that it would be found
again on every check for the rest of time, and the filter would be a way of
making the same video reappear for ever rather than a way of ignoring it.

**Two more, outside the table:**

- **Export / import the list.** `watch.json` is already the right shape; this is
  a file dialog and two functions. It is how someone moves to a new machine.
- **Send it to the phone.** New items already produce a notification on the
  desktop. The pairing that already exists can put that notification on the
  phone, with one tap to start the download on the PC. Nothing else in this
  category can do that, because nothing else has the pairing.

## 8. Phase 6 — say it out loud

The UI now says **Following**, with *Follow channel* / *Follow playlist* as the
verbs. What is left is that this feature is not mentioned anywhere outside the
app — not in the README, not on the site, not in the store listing.

Write it where it will be looked for. People do not search for "watch a
channel"; they search for *subscribe to a channel*, *download new videos
automatically*, *auto download from a YouTube channel*. Use those words in the
public copy while the interface keeps the clearer one.

---

## 9. Data model

`watch.json` gains fields; it does not change shape. Old files load unchanged —
every new field defaults to empty and the item simply takes the old path until
its next successful resolve.

```
item:
  id, url, kind, title, uploader, thumbnail          (unchanged)
  added, checked, paused, error, botcheck            (unchanged)
  known[], fresh[]                                   (unchanged)
  feed          ""                 phase 1
  feed_fail     0                  phase 1
  full_checked  0                  phase 3
  auto          "off"              phase 4
  limits        {}                 phase 4/5
  opts          {}                 phase 5
```

Write a `_migrate(data)` that fills defaults on load. Do not write a version
number that nothing reads; fill what is missing and move on.

## 10. Tests

Per phase, in `tests/`, in the style already used:

- **Feed parse** — a saved Atom fixture, both channel and playlist. Assert the
  ids, the count, and that an entry with a missing field does not throw.
- **Fallback** — feed returns 500 → falls through to `peek()`; three failures →
  the item stops using the feed.
- **Zero entries** — a feed with no entries must not be read as "nothing new"
  right after a resolve.
- **No duplicate announcements** — an id in `known` never re-enters `fresh`,
  through either path.
- **Caps** — `known` stays at 400, `fresh` at 60.
- **Auto-download guardrails** — more than N new in one check downloads exactly
  N; `off` downloads nothing, ever.
- **Promise** — a source-level check that the docstring and the consent dialog
  do not claim "never downloads" while auto-download exists. The suite already
  has this kind of check; add to it rather than inventing a new mechanism.

Run `tests/run_all.py` after each phase. A file that reports nothing is a
failure, not a gap.

## 11. What not to do

- **Do not rename ids, routes, settings keys or `watch.json`.** The label was
  the problem; the plumbing was not.
- **Do not drop the yt-dlp path.** It is the baseline, the catch-up, and every
  non-YouTube source.
- **Do not turn auto-download on for anyone by default**, including on upgrade.
- **Do not use an API key.** The feed needs none, and a key is a per-user setup
  step plus a quota to run out of.
- **Do not poll faster than the feed updates.** See below — this is unmeasured.

## 12. Measure these before building on them

- **Feed lag.** How long after a video goes live does it appear in the feed? If
  it is ten or fifteen minutes, a 15-minute interval is decoration. Post-time
  versus first-seen, on a few channels, for a few days.
- **Shorts and livestreams in the feed.** Do they appear at all, and can they be
  told apart without a lookup? The field list says no. Confirm it.
- **Handle resolution.** Which channel URL forms yield a `channel_id` from the
  existing `peek()` payload, and which need a page read.

---

## Order

1. Feed read with fallback (phase 1)
2. Intervals and caps, and the rewritten consent text (phase 2)
3. Weekly catch-up (phase 3)
4. Auto-download, opt-in and capped, with the promise updated (phase 4)
5. Per-item options and the Edit panel (phase 5)
6. Export/import, phone notification (phase 5, tail)
7. Public copy (phase 6)

Phases 1–3 make the feature cheap. Phase 4 makes it competitive. Phases 5–6
make it findable.
