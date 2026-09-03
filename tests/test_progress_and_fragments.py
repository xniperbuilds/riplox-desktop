"""The bar, and the file the engine is allowed to call finished.

Two things reported from real use on 1 Sep 2026, both from one network drop.

1. "download start hote hi wo 92% pe chala jata hai or file size jump krta
   rahta hai ooper niche". Measured against the real engine: the FIRST progress
   line of a fragmented download reads 1024 of 1024. That is a ratio of 1.0,
   and percent only ever moves forward, so the bar reached the top of its band
   on line one and no honest line afterwards could bring it back. The totals
   after it are an estimate that climbs - 36 KB, 110 KB, 2 MB - which is the
   figure that was seen jumping.

2. The file itself. yt-dlp's default is to SKIP a fragment it cannot get and
   finish anyway, so one drop produced a 2160p file with 36 seconds of video
   against 390 seconds of audio - marked done, twenty abandoned .part-Frag
   files beside it. ffprobe on the two files that were kept:
       AFSANAY  2160p  14.4 MB   video  36.1s   audio 389.9s
       COME THROUGH   9.5 MB     video  85.0s   audio 197.2s
       Xcho (healthy) 4.3 MB     video 180.5s   audio 180.5s
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-progress-test-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import engine                                                 # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name
          + ((" | " + str(detail)[:88]) if detail else ""))


MANAGER = engine.DownloadManager()


def a_job():
    job = engine.Job(url="https://www.youtube.com/watch?v=x")
    job.status = "downloading"
    return job


def line(status="downloading", done=0, total=0, est=0, speed=0, eta=0,
         frag_at="NA", frag_of="NA"):
    return "|".join(str(v) for v in
                    (status, done, total, est, speed, eta, frag_at, frag_of))


try:
    print("\n-- the engine's real first line, byte for byte ------------------")
    # Captured from yt-dlp 2026.08.19 with --concurrent-fragments 16.
    job = a_job()
    MANAGER._apply_progress(job, line(done=1024, total=1024,
                                      frag_at=0, frag_of=36))
    check("⭐ 1024 of 1024 no longer means the whole file",
          job.percent < 10.0, "%.1f%%" % job.percent)

    print("\n-- and the rest of that download ---------------------------------")
    # The real sequence: totals climbing, downloaded far behind them.
    for done, total, at in [(3072, 36864, 0), (10952, 246816, 0),
                            (19144, 197136, 1), (65224, 879120, 1),
                            (120520, 2095632, 1)]:
        MANAGER._apply_progress(job, line(done=done, total=total,
                                          frag_at=at, frag_of=36))
    check("⭐ the bar is still near the start, where the download is",
          job.percent < 10.0, "%.1f%%" % job.percent)

    print("\n-- fragments are the floor, bytes move it in between -------------")
    # Fragments say half, the bytes say 5 of 9. The further-along one wins, so
    # this is the byte reading - and never less than the fragments' half.
    job = a_job()
    MANAGER._apply_progress(job, line(done=5, total=9, frag_at=18, frag_of=36))
    check("⭐ 18 of 36 fragments is the FLOOR, never the ceiling",
          job.percent >= 45.9, "%.1f%%, floor %.1f%%" % (job.percent, 0.5 * 92))
    # ⚠️ REVERSED, 3 Sep 2026. This used to require the byte ratio to carry
    # the bar PAST the fragments. That is what "% peeche jaati thi" was: the
    # ratio's denominator is total_bytes_estimate, which climbed 147 MB -> 353
    # MB during one measured download, so the ratio - and the bar with it -
    # fell 144 times. The bar now measures its way across a fragment and can
    # never reach the next one early.
    check("...and the bytes never carry it past the fragments",
          45.9 <= job.percent <= 48.5,
          "%.1f%%, next fragment at %.1f%%" % (job.percent, 19 / 36 * 92))
    check("...and nothing sent it to the top of the band",
          job.percent < 92.0, "%.1f%%" % job.percent)

    # The other way round: bytes behind, fragments ahead. The floor wins.
    job = a_job()
    MANAGER._apply_progress(job, line(done=1, total=100, frag_at=27, frag_of=36))
    check("⭐ and when the bytes lag, the fragment floor carries it",
          67.0 <= job.percent <= 70.0, "%.1f%% (27 of 36)" % job.percent)

    # ⭐⭐ The "percentage stuck" report. Fragments complete in bursts, so
    # between two of them the count says nothing at all - measured: 149
    # consecutive lines with no movement. The bytes keep arriving throughout,
    # and they are what carries the bar across the gap.
    job = a_job()
    moved = []
    for done in (10, 20, 30, 40, 50):
        MANAGER._apply_progress(job, line(done=done, total=100,
                                          frag_at=1, frag_of=36))
        moved.append(round(job.percent, 2))
    # ⚠️ NARROWED, 3 Sep 2026. The bar still moves between fragments - on the
    # two real captures in tests/fixtures it moves on 87-92% of lines - but it
    # moves by a MEASURED amount: how far the bytes have got across the current
    # fragment, judged against the size of the ones already finished. It cannot
    # run past the next fragment, so a burst of bytes larger than any fragment
    # so far parks it just short of the boundary instead of overshooting.
    #
    # These five lines are a burst like that (10 -> 50 bytes while fragment 1 is
    # the only one finished), so two distinct readings is the honest answer.
    # tests/test_progress_bar.py is where movement is judged properly, over
    # 1,439 real lines rather than five invented ones.
    check("⭐ the bar moves between fragments", len(set(moved)) >= 2, moved)
    check("...and never past the fragment it is inside",
          moved[-1] <= 2 / 36 * 92 + 0.01,
          "%.2f%%, fragment 2 of 36 ends at %.2f%%" % (moved[-1], 2 / 36 * 92))

    print("\n-- a size that contradicts itself is not shown ------------------")
    job = a_job()
    MANAGER._apply_progress(job, line(done=23_900_000, total=278_800_000,
                                      frag_at=3, frag_of=36))
    settled = job.size
    MANAGER._apply_progress(job, line(done=24_000_000, total=1024,
                                      frag_at=4, frag_of=36))
    check("⭐ an estimate below what is already on disk is ignored",
          job.size == settled, "%s -> %s" % (settled, job.size))
    check("...and what HAS arrived is still counted",
          job.got and job.got != "0 B", job.got)

    print("\n-- ⭐⭐ the wild opening estimates are never shown ---------------")
    # Measured on a real 37.3 MB download: the engine's opening readings were
    # 4, 14, 56 and 88 MB, rising and falling. Showing them is the "size jump
    # krta rahta hai ooper niche" that was reported.
    #
    # ⚠️ Holding the LARGEST instead was tried, and was worse: it froze on the
    # 88 and never came down, so an 83 MB download announced 510 MB the whole
    # way. Both faults are in this one case.
    MB = 1024 ** 2
    def as_bytes(text):
        n, _, unit = (text or "0 B").partition(" ")
        return float(n) * {"B": 1, "KB": 1024, "MB": MB, "GB": 1024 ** 3}.get(unit, 1)

    job = a_job()
    early = []
    for done, est, at in [(0.2 * MB, 4 * MB, 1), (0.8 * MB, 14 * MB, 1),
                          (3.4 * MB, 56 * MB, 2), (8.6 * MB, 88 * MB, 3)]:
        MANAGER._apply_progress(job, line(done=int(done), est=int(est),
                                          frag_at=at, frag_of=36))
        early.append(job.size)
    check("⭐ nothing at all is claimed while the estimate is still wild",
          all(s == "" for s in early), early)

    # A tenth of the fragments in, the estimate has settled - these are the
    # real readings from that same run, and they close on 37.3 MB.
    settled = []
    for done, est, at in [(10.7 * MB, 47.5 * MB, 4), (17.8 * MB, 45.5 * MB, 9),
                          (21.9 * MB, 37.5 * MB, 15), (30.0 * MB, 40.0 * MB, 26)]:
        MANAGER._apply_progress(job, line(done=int(done), est=int(est),
                                          frag_at=at, frag_of=36))
        settled.append(job.size)
    check("⭐ then it does speak", all(s for s in settled), settled)
    check("⭐ ...and never freezes on the worst reading",
          as_bytes(settled[-1]) < 60 * MB,
          " -> ".join(settled))
    check("⭐ ...landing within a third of the 37.3 MB the file really was",
          abs(as_bytes(settled[-1]) - 37.3 * MB) < 0.34 * 37.3 * MB,
          settled[-1])

    print("\n-- an unfragmented download is not held back -------------------")
    # There the total is exact from the first line, so waiting would be a
    # regression of its own.
    job = a_job()
    MANAGER._apply_progress(job, line(done=10, total=100))
    check("⭐ an exact total is shown immediately", job.size == "100 B", job.size)

    print("\n-- ⚠ the plain case must be untouched ---------------------------")
    # No fragments at all: the byte maths is the only thing there is, and it
    # was right all along.
    job = a_job()
    MANAGER._apply_progress(job, line(done=50, total=100))
    check("⭐ half of an unfragmented file is half of the band",
          45.0 <= job.percent <= 47.0, "%.1f%%" % job.percent)
    check("...and its size is shown", job.size == "100 B", job.size)

    print("\n-- ...and so must an older engine that sends six fields ---------")
    job = a_job()
    MANAGER._apply_progress(job, "downloading|50|100|0|0|0")
    check("⭐ six fields still work, no crash and the same answer",
          45.0 <= job.percent <= 47.0, "%.1f%%" % job.percent)

    print("\n-- the floor is what stops it walking backwards ------------------")
    # The furthest-reached guard is gone on purpose - it was what froze the bar.
    # What replaces it is the fragment count: those only ever go up, so the bar
    # can dip a little between them and never collapse. Measured worst dip on a
    # real download: 1.3%.
    job = a_job()
    MANAGER._apply_progress(job, line(done=9, total=10, frag_at=30, frag_of=36))
    high = job.percent
    MANAGER._apply_progress(job, line(done=1, total=10, frag_at=30, frag_of=36))
    check("⭐ a wild byte reading cannot drop it below the fragments",
          job.percent >= 30.0 / 36.0 * 92.0 - 0.1,
          "%.1f -> %.1f, floor %.1f" % (high, job.percent, 30 / 36 * 92))
    check("...and the fragments themselves never go down",
          job.percent > 70.0, "%.1f%%" % job.percent)

    # ⭐⭐ ...but it must NOT be pinned to the furthest it ever reached. That
    # guard is what produced both reports: it held the 1024-of-1024 line at the
    # top of the band, and later it froze the bar for 326 consecutive lines.
    # The real shape of a dip: bytes keep arriving while the estimate grows
    # faster, so the ratio eases back a little. The bar has to follow.
    job = a_job()
    MANAGER._apply_progress(job, line(done=60, total=100, frag_at=1, frag_of=36))
    peak = job.percent
    MANAGER._apply_progress(job, line(done=62, total=120, frag_at=1, frag_of=36))
    # ⚠️ REVERSED, 3 Sep 2026. Easing back was chosen deliberately, as the
    # lesser of two evils against a bar frozen for 326 lines. It is not needed
    # any more and it was reported as a bug in its own right: neither the
    # estimate nor anything derived from it decides the bar now, so there is
    # nothing left to ease back FROM. Measured against the old rule on the same
    # two downloads: 0 backward steps against 144, and the longest freeze got
    # SHORTER as well - 14 lines against 26.
    check("⭐⭐ a rising estimate does not drag the bar backwards",
          job.percent >= peak - 1e-9,
          "%.2f%% -> %.2f%% (60/100 then 62/120)" % (peak, job.percent))

    print("\n-- ⭐⭐ and a fragment that cannot be got must FAIL the download --")
    # The whole of bug 2. Without this the engine skips it, merges what it has
    # with the full audio, exits 0, and Riplox files a broken video as done.
    job = a_job()
    job.quality = "max"
    args = MANAGER.build_args(job, {"download_dir": str(SANDBOX)}, "", None)
    check("⭐ --abort-on-unavailable-fragments is passed",
          "--abort-on-unavailable-fragments" in args)
    check("...and the engine is NOT told to skip them",
          "--skip-unavailable-fragments" not in args
          and "--no-abort-on-unavailable-fragments" not in args)
    check("...while the retries that come first are still there",
          "--fragment-retries" in args and "--retries" in args)

    template = [a for a in args if "fragment_index" in str(a)]
    check("⭐ the progress template asks for the fragment count",
          template and "fragment_count" in template[0], bool(template))
finally:
    shutil.rmtree(SANDBOX, ignore_errors=True)

print("\n" + "=" * 68)
print("  " + str(len(PASS)) + " passed, " + str(len(FAIL)) + " failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68 + "\n")
raise SystemExit(1 if FAIL else 0)
