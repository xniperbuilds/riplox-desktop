"""
Options that cannot run must not be sent, and must not be silent.

Without ffmpeg, yt-dlp accepts --embed-subs, downloads the video, and skips
the step. The switch stays on, the file is missing the thing, and nobody is
told. This checks both halves of the fix: the flags are left out, and the app
can say which ones.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-ff-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


def with_ffmpeg(present, fn):
    real = engine.has_ffmpeg
    engine.has_ffmpeg = lambda: present
    try:
        return fn()
    finally:
        engine.has_ffmpeg = real


ON = dict(engine.DEFAULT_SETTINGS, write_subs=True, embed_subs=True,
          embed_chapters=True, sponsorblock=True)

print("\n-- with the media tool, everything is sent ------------------------")
args = with_ffmpeg(True, lambda: engine.extra_args(ON, "best"))
for flag in ("--embed-subs", "--embed-chapters", "--sponsorblock-remove",
             "--convert-subs", "--write-subs"):
    check(f"sent: {flag}", flag in args)
check("nothing is reported as dropped",
      with_ffmpeg(True, lambda: engine.needs_ffmpeg(ON, "best")) == [])

print("\n-- without it, the impossible ones are left out -------------------")
args = with_ffmpeg(False, lambda: engine.extra_args(ON, "best"))
for flag in ("--embed-subs", "--embed-chapters", "--sponsorblock-remove",
             "--convert-subs"):
    check(f"⭐ NOT sent to be ignored: {flag}", flag not in args, str(flag in args))

print("\n-- but the half that still works is kept --------------------------")
check("the subtitle file is still asked for", "--write-subs" in args)
check("auto subtitles too", "--write-auto-subs" in args)
check("and its languages", "--sub-langs" in args)

print("\n-- and the app can say exactly what is not happening --------------")
lost = with_ffmpeg(False, lambda: engine.needs_ffmpeg(ON, "best"))
check("subtitles-inside is named", "subtitles inside the video" in lost, str(lost))
check("chapters is named", "chapter marks" in lost)
check("sponsor skipping is named", "skipping sponsor segments" in lost)

print("\n-- nothing is claimed lost that was never switched on -------------")
off = dict(engine.DEFAULT_SETTINGS, write_subs=False, embed_subs=False,
           embed_chapters=False, sponsorblock=False)
check("all switches off means nothing to report",
      with_ffmpeg(False, lambda: engine.needs_ffmpeg(off, "best")) == [])

subs_only = dict(engine.DEFAULT_SETTINGS, write_subs=True, embed_subs=False)
check("wanting a subtitle FILE is not a loss (that still works)",
      with_ffmpeg(False, lambda: engine.needs_ffmpeg(subs_only, "best")) == [],
      str(with_ffmpeg(False, lambda: engine.needs_ffmpeg(subs_only, "best"))))

print("\n-- audio-only downloads are judged on their own terms -------------")
lost_mp3 = with_ffmpeg(False, lambda: engine.needs_ffmpeg(ON, "mp3"))
check("subtitles and chapters are not claimed for an mp3",
      "chapter marks" not in lost_mp3 and "subtitles inside the video" not in lost_mp3,
      str(lost_mp3))

print("\n-- the settings API hands the list to the screen ------------------")
import app as riplox_app                                    # noqa: E402
engine.save_settings(ON)
real = engine.has_ffmpeg
engine.has_ffmpeg = lambda: False
try:
    with riplox_app.app.test_request_context():
        body = riplox_app.api_get_settings().get_json()
finally:
    engine.has_ffmpeg = real
check("the API reports the dropped list", isinstance(body.get("dropped"), list),
      str(body.get("dropped")))
check("...with all three in it", len(body.get("dropped") or []) == 3,
      str(body.get("dropped")))

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
