"""A fallback route must not quietly hand over a much smaller video.

Found in a live audit run on 1 Sep 2026: a network drop pushed the download
onto a fallback player client, the client only offers small formats, and a
request for "max" finished green as an 11.5 MB 640x360 file whose name still
said [max]. Nothing anywhere said so.

Measured that day with yt-dlp 2026.08.19 on three videos, which is where the
new client list comes from:

    client        no PO token   with PO token
    default          full          full
    tv_simply        360p          full
    mweb             360p          full
    web_safari       180p          180p      <- was on rung 2, capped either way
    android_vr       360p          360p      <- was on rung 3, capped either way
    tv_embedded      full          full
    visionos         full          full

⚠️ The regression that matters most is at the bottom: a video that genuinely
only has 360p must NOT produce a warning. A false "you lost quality" on a file
that is fine is how warnings get ignored.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-quality-test-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import engine                                                 # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name
          + ((" | " + str(detail)[:92]) if detail else ""))


MANAGER = engine.DownloadManager()
SETTINGS = {"download_dir": str(SANDBOX)}


def a_job(quality="max", attempt=2, height=360, kind="download", opts=None):
    job = engine.Job(url="https://www.youtube.com/watch?v=x", quality=quality,
                     opts=opts or {})
    job.kind = kind
    job.attempt = attempt
    job.height = height
    return job


# best_height is the one thing here that would touch the network. Replaced
# throughout, and restored at the end - no test may depend on a real video.
REAL_BEST, REAL_OPEN, REAL_CLOSE = (engine.best_height, engine.open_cookies,
                                    engine.close_cookies)
try:
    engine.open_cookies = lambda *a, **k: (None, False, 0)
    engine.close_cookies = lambda *a, **k: None

    print("\n-- the reported case: 4K asked for, 360p delivered ---------------")
    engine.best_height = lambda *a, **k: 2160
    said = MANAGER._quality_short(a_job(quality="max", height=360), SETTINGS)
    check("⭐ it is noticed at all", said != "", said or "(nothing said)")
    check("...and both numbers are named, so the loss is legible",
          "360" in said and "2160" in said, said)
    check("...and it says the file was kept, not thrown away",
          "saved" in said.lower(), said)
    check("...and it says what to press",
          "retry" in said.lower(), said)

    print("\n-- ⚠ THE REGRESSION: a video that only HAS 360p ------------------")
    # Nothing went wrong here. A warning would be a lie, and a lie in a warning
    # is worse than no warning at all.
    engine.best_height = lambda *a, **k: 360
    check("⭐ a genuinely small video produces NO warning",
          MANAGER._quality_short(a_job(quality="max", height=360), SETTINGS) == "")
    engine.best_height = lambda *a, **k: 0        # could not be asked
    check("⭐ and when the check itself fails, it stays quiet",
          MANAGER._quality_short(a_job(quality="max", height=360), SETTINGS) == "")

    print("\n-- the main route's answer is the truth --------------------------")
    engine.best_height = lambda *a, **k: 2160
    check("⭐ attempt 1 is never second-guessed, whatever it returned",
          MANAGER._quality_short(a_job(attempt=1, height=360), SETTINGS) == "")

    print("\n-- asking for small and getting small is not a fault -------------")
    check("someone who chose 360p gets no warning",
          MANAGER._quality_short(a_job(quality="360", height=360), SETTINGS) == "")
    check("...and someone who chose 480p and got 360p does",
          MANAGER._quality_short(a_job(quality="480", height=360), SETTINGS) != "")

    print("\n-- things with no single height to judge -------------------------")
    check("a tall file is left alone",
          MANAGER._quality_short(a_job(height=1080), SETTINGS) == "")
    check("a file with no height at all (audio) is left alone",
          MANAGER._quality_short(a_job(height=0), SETTINGS) == "")
    check("a folder of chapters is left to the parts check",
          MANAGER._quality_short(
              a_job(opts={"chapters": ["one"]}), SETTINGS) == "")
    check("subtitles-only is left alone",
          MANAGER._quality_short(
              a_job(opts={"subs_only": True}), SETTINGS) == "")
    check("a convert job is not a download",
          MANAGER._quality_short(a_job(kind="convert"), SETTINGS) == "")

    print("\n-- ⭐⭐ the client list itself, as measured ----------------------")
    # Each of these was measured; the test states what was measured so that
    # putting a capped client back is a red test rather than a quiet loss.
    ladder = engine._RETRY_CLIENTS
    joined = ",".join(ladder)
    check("⭐ the first rung is still whatever yt-dlp picks",
          ladder[0] == "", repr(ladder[0]))
    check("⭐ web_safari is gone - capped at 180p even WITH a token",
          "web_safari" not in joined, joined)
    check("⭐ android_vr is gone - capped at 360p even WITH a token",
          "android_vr" not in joined, joined)
    check("...and every fallback rung carries a client that needs no token",
          all(any(good in rung for good in ("tv_embedded", "visionos",
                                            "web_embedded", "android_music",
                                            "ios_music"))
              for rung in ladder[1:]), joined)
    check("...while keeping the token-capable clients that clear bot checks",
          "tv_simply" in joined and "mweb" in joined, joined)
    check("the ladder is still three rungs, so nothing else changed shape",
          len(ladder) == 3, len(ladder))

    print("\n-- the dropdown a person picks from ------------------------------")
    # The same defect wearing a different hat: "More options" offered these by
    # bare name, four of the six capped, and the choice is remembered - so one
    # pick quietly shrank every later download.
    html = (ROOT / "src" / "templates" / "index.html").read_text(encoding="utf-8")
    picker = html.split('id="optClient"', 1)[-1].split("</select>", 1)[0]
    check("⭐ the full-quality clients are offered at all",
          all(c in picker for c in ("tv_embedded", "visionos")))
    check("⭐ and the capped ones are grouped as small",
          "Small formats only" in picker
          and picker.index("Small formats only") < picker.index("web_safari"),
          "web_safari sits under the small heading")
    check("...every value in the page is one the engine will accept",
          all(v in engine.PLAYER_CLIENTS
              for v in __import__("re").findall(r'<option value="([^"]*)"', picker)),
          __import__("re").findall(r'<option value="([^"]*)"', picker))

    print("\n-- ⭐⭐ and the DOWNLOAD PATH actually asks ---------------------")
    # Everything above tests _quality_short in isolation. If nobody CALLS it,
    # every test above stays green while the bug is fully back - which is
    # exactly how tie_to_app was written, tested and never wired up. So this
    # drives the real success path with a fake engine process.
    import subprocess as _sp
    import threading as _th

    class FakeProc:
        """Enough of Popen for _spawn: it prints one path line and exits 0."""
        returncode = 0
        pid = 4242

        def __init__(self, path, height):
            self.stdout = iter([
                engine.PATH_TAG + "%s|%d\n" % (path, height)])
            self.stderr = iter([])

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

        # _diagnostic asks the engine its version, which goes through
        # subprocess.run -> the same faked Popen. Supporting `with` keeps that
        # incidental call from blowing up the test it is not part of.
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def communicate(self, *a, **k):
            return ("", "")

    real_popen, real_tie = _sp.Popen, engine.tie_to_app
    real_version = engine.engine_version
    engine.engine_version = lambda: "test-engine"
    made = SANDBOX / "small.mp4"
    made.write_bytes(b"x" * 2048)
    try:
        engine.tie_to_app = lambda proc: None
        engine.best_height = lambda *a, **k: 2160          # 4K really exists
        _sp.Popen = lambda *a, **k: FakeProc(str(made), 360)

        job = a_job(quality="max", attempt=2, height=0)
        job.status = "downloading"
        MANAGER._spawn(job, SETTINGS, "", None)

        check("⭐ a 360p file from a fallback does NOT finish as done",
              job.status != "done", job.status)
        check("⭐ ...it is flagged, with both numbers",
              "360" in (job.error or "") and "2160" in (job.error or ""),
              job.error)

        # And the same path must still let a good download through.
        engine.best_height = lambda *a, **k: 1080
        _sp.Popen = lambda *a, **k: FakeProc(str(made), 1080)
        good = a_job(quality="max", attempt=2, height=0)
        good.status = "downloading"
        MANAGER._spawn(good, SETTINGS, "", None)
        check("⭐ ...while a full-quality download still finishes done",
              good.status == "done", "%s / %s" % (good.status, good.error))
    finally:
        _sp.Popen, engine.tie_to_app = real_popen, real_tie
        engine.engine_version = real_version
finally:
    engine.best_height = REAL_BEST
    engine.open_cookies, engine.close_cookies = REAL_OPEN, REAL_CLOSE
    shutil.rmtree(SANDBOX, ignore_errors=True)

print("\n" + "=" * 68)
print("  " + str(len(PASS)) + " passed, " + str(len(FAIL)) + " failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68 + "\n")
raise SystemExit(1 if FAIL else 0)
