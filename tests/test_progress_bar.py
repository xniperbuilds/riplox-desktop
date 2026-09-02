"""The bar, replayed over two real downloads.

Four attempts at this were chosen by reasoning and all four came back reported
as broken. The fifth was chosen from a measurement, and this file is what keeps
it that way: the fixtures beside it are the raw --progress-template output of
two actual YouTube downloads at quality "max" - 824 and 644 lines - and every
line is fed to the real _apply_progress.

What it guards, in the words the reports used:

  "% peeche jaati thi"   - the bar must never fall inside a stream
  "% atak jata tha"      - and must not stand still for long stretches
  "size aage peeche"     - the size must not flicker under the reader

The cause each time was the same and is worth keeping written down: total_bytes
is absent on almost every line of a fragmented download, so anything dividing
by "the total" is really dividing by yt-dlp's own extrapolation - which climbed
from 147 MB to 353 MB during one of these two downloads, and fell by 174 MB
during the other.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                                   # noqa: E402


def a_job():
    """The real Job, not a stand-in.

    An earlier version of this file used a plain class with the handful of
    fields the method touches. It accepted any attribute, so a change that
    wrote a new field passed here and raised AttributeError on every real
    download - Job has __slots__. The stand-in was not testing the code, it was
    testing itself.
    """
    job = engine.Job("https://example.com/v", quality="max")
    job.status = "downloading"
    return job


def replay(path):
    """Feed one capture through the real code, one stream at a time.

    yt-dlp fetches the video and then the audio, and each reports from zero, so
    a falling fragment index means a new stream rather than a bar going
    backwards. The engine moves to its second band there; this splits on the
    same signal so each stream is judged on its own.
    """
    manager = engine.DownloadManager.__new__(engine.DownloadManager)
    # ONE job for the whole capture, because that is what the app has. An
    # earlier version of this file made a fresh job per stream, and the reset
    # that handles the video-to-audio boundary was therefore never executed -
    # a mutation removing it entirely went unnoticed.
    job = a_job()
    runs, rows, last_at = [], [], -1.0

    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("|")
        if len(parts) < 8:
            continue
        at = float(parts[6]) if parts[6] not in ("", "NA") else 0.0
        if at < last_at:
            runs.append(rows)
            rows = []
            job.streams += 1            # the engine moves band here too
        last_at = at

        engine.DownloadManager._apply_progress(manager, job, line)
        rows.append({"pct": job.percent, "size": job.size})

    if rows:
        runs.append(rows)
    return runs


PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          (" | " + detail) if detail else ""))


for fixture in sorted(HERE.glob("fixtures/progress-*.txt")):
    label = fixture.stem.replace("progress-", "")
    for index, rows in enumerate(replay(fixture), 1):
        if len(rows) < 20:
            continue
        pcts = [r["pct"] for r in rows]
        sizes = [r["size"] for r in rows if r["size"]]

        print("\n-- %s, stream %d (%d lines) %s"
              % (label, index, len(rows), "-" * 26))

        drops = [(a, b) for a, b in zip(pcts, pcts[1:]) if b < a - 1e-9]
        worst = max((a - b for a, b in drops), default=0.0)
        check("never goes backwards", not drops,
              "%d drops, worst %.2f%%" % (len(drops), worst) if drops
              else "%d readings" % len(pcts))

        longest = run = 0
        for a, b in zip(pcts, pcts[1:]):
            run = run + 1 if abs(a - b) < 1e-9 else 0
            longest = max(longest, run)
        # The measured winner froze for 17 lines at worst; 40 leaves room for a
        # slower connection without letting the old 149-line stalls back in.
        check("does not stall", longest <= 40, "longest freeze %d lines" % longest)

        moves = sum(1 for a, b in zip(pcts, pcts[1:]) if abs(a - b) > 1e-9)
        share = moves / max(1, len(pcts) - 1)
        # Fragments alone move on 6.7% of lines and stall visibly between
        # them; filling the gap with measured bytes moves on about 90%. The
        # freeze check cannot tell those apart - 18 lines against 14 - so this
        # is what stops the bar quietly becoming a staircase again.
        check("moves between fragments", share >= 0.40,
              "moved on %.0f%% of readings" % (share * 100))

        check("gets to the end", pcts[-1] >= 90.0, "ended at %.1f%%" % pcts[-1])

        changes = sum(1 for a, b in zip(sizes, sizes[1:]) if a != b)
        # On this same data: 484 changes raw, 89 with rounding alone, 7-10 once
        # the number on screen is held until the estimate leaves a band around
        # it. 20 is tight enough that dropping the band fails here - 89 was
        # still reported as "size aage peeche ho raha hai".
        check("the size does not flicker", changes <= 20,
              "%d changes across %d readings" % (changes, len(sizes)))

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
print("=" * 68)
sys.exit(1 if FAIL else 0)
