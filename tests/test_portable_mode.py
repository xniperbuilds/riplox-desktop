"""Portable mode - a Data folder beside the exe moves everything into it.

The whole feature is one decision in engine.data_dir(), which is what makes it
safe: every path in the app already goes through that function, so there is no
second place that can disagree about where things live.

Two failures matter more than the feature working, and both are silent:

  * an INSTALLED copy finding a marker it should never have had, and quietly
    moving its data root - settings, history, queue and the phone pairing all
    apparently gone, with the real files still in LOCALAPPDATA;

  * a portable copy that could not write to its own folder carrying on as
    though it had, writing to a PC the user believed it never touched.

⚠️ LOCALAPPDATA is redirected before engine is imported. Patching save() is not
enough - private helpers write through their own paths, and a test of mine once
destroyed a real phone pairing that way.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-portable-test-"))
REAL_LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
os.environ["LOCALAPPDATA"] = str(SANDBOX)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "build"))

import engine                                              # noqa: E402
import make_portable                                       # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name
          + ((" | " + str(detail)[:90]) if detail else ""))


def as_app(folder):
    """Pretend to be the packaged Riplox sitting in `folder`."""
    folder.mkdir(parents=True, exist_ok=True)
    sys.frozen = True
    sys.executable = str(folder / "Riplox.exe")
    engine._root = None
    engine._root_state = "off"


def as_source():
    """Back to a plain checkout."""
    if hasattr(sys, "frozen"):
        del sys.frozen
    engine._root = None
    engine._root_state = "off"


REAL_EXECUTABLE = sys.executable
INSTALLED = SANDBOX / engine.APP_NAME


print("\n-- which root, and why ---------------------------------------------")

home = SANDBOX / "stick" / "Riplox"
(home / "Data").mkdir(parents=True, exist_ok=True)

as_source()
sys.executable = str(home / "Riplox.exe")
check("a checkout is never portable, marker or no marker",
      engine.data_dir() == INSTALLED and engine.portable_state() == "off",
      engine.data_dir())

as_app(home)
check("⭐ packaged, with a writable Data folder -> beside the exe",
      engine.data_dir() == home / "Data" and engine.portable_state() == "on",
      engine.data_dir())

plain = SANDBOX / "installed-app"
as_app(plain)
check("packaged, no Data folder -> the usual place on this PC",
      engine.data_dir() == INSTALLED and engine.portable_state() == "off",
      engine.data_dir())

# A read-only stick. The probe is exercised for real further down; here the
# branch it feeds is what is being checked.
real_writable = engine._writable
try:
    engine._writable = lambda folder: False
    as_app(home)
    check("⭐ wanted portable, cannot write -> falls back to this PC",
          engine.data_dir() == INSTALLED, engine.data_dir())
    check("⭐ ...and says so, rather than looking portable",
          engine.portable_state() == "read-only", engine.portable_state())
finally:
    engine._writable = real_writable


print("\n-- the probe is a real write, not a guess --------------------------")
good = SANDBOX / "probe-ok"
check("a writable folder answers yes", engine._writable(good) is True)
check("...and it cleans up after itself",
      not (good / ".write-test").exists())

blocked = SANDBOX / "not-a-folder"
blocked.write_text("I am a file", encoding="utf-8")
check("a path that cannot be a folder answers no",
      engine._writable(blocked / "Data") is False)


print("\n-- decided once, not re-probed on every call -----------------------")
as_app(home)
first = engine.data_dir()
shutil.rmtree(home / "Data")                    # the marker disappears mid-run
check("the root does not wander when the folder changes underneath it",
      engine.data_dir() == first, engine.data_dir())
(home / "Data").mkdir(parents=True, exist_ok=True)


print("\n-- every path follows the root -------------------------------------")
# Not "they all call data_dir()" - that is the thing being tested. Move the
# root and look at where the paths actually point.
as_app(home)
want = home / "Data"
paths = {
    "settings": engine.settings_file(),
    "history": engine.history_file(),
    "failed": engine.failed_file(),
    "queue": engine.queue_file(),
    "downloaded": engine.archive_file(),
    "pacing": engine.pace_file(),
    "engine copy": engine.bin_dir(),
}
try:
    import cookies                                          # noqa: E402
    paths["cookies"] = cookies.store_file()
    paths["browser profile"] = cookies.profile_dir()
    paths["cookie tmp"] = cookies.temp_dir()
except Exception as exc:                                    # pragma: no cover
    print("  (cookies not checked: " + str(exc)[:60] + ")")

for label, where in paths.items():
    check(label + " lands inside the portable folder",
          str(where).startswith(str(want)), where)


print("\n-- autostart is refused, and explained -----------------------------")
as_app(home)
answer = engine.set_autostart(True)
check("⭐ a portable copy will not write to the registry",
      answer.get("ok") is False and answer.get("on") is False, answer)
check("...and the message says why, and what to do instead",
      "portable" in answer.get("message", "").lower()
      and "install" in answer.get("message", "").lower(),
      answer.get("message"))
check("it never claims an entry some installed copy left behind",
      engine.autostart_on() is False)

as_source()
sys.executable = REAL_EXECUTABLE
answer = engine.set_autostart(True)
check("a checkout is still refused, for its own reason",
      answer.get("ok") is False
      and "portable" not in answer.get("message", "").lower(),
      answer.get("message"))


print("\n-- the marker must never reach an installed copy -------------------")
fake = SANDBOX / "fake"
for part in ("dist/Riplox", "bin", "browser-extension"):
    (fake / part).mkdir(parents=True, exist_ok=True)
(fake / "dist/Riplox/Riplox.exe").write_text("x", encoding="utf-8")
(fake / "bin/ffmpeg.exe").write_text("x", encoding="utf-8")
(fake / "browser-extension/manifest.json").write_text("{}", encoding="utf-8")
(fake / "TERMS.txt").write_text("terms", encoding="utf-8")
(fake / "LICENSE").write_text("gpl", encoding="utf-8")
(fake / "src").mkdir(parents=True, exist_ok=True)
(fake / "src/app.py").write_text('VERSION = "9.9.9"\n', encoding="utf-8")

real_root, real_dist = make_portable.ROOT, make_portable.DIST
try:
    make_portable.ROOT = fake
    make_portable.DIST = fake / "dist/Riplox"
    built = make_portable.stage(SANDBOX / "staged")

    check("the staged copy carries the marker",
          (built / "Data").is_dir())
    check("⭐ and dist/Riplox does NOT - an installed copy must never find one",
          not (fake / "dist/Riplox/Data").exists())
    check("the marker folder explains itself",
          (built / "Data" / "readme.txt").is_file())
    check("the version comes from the app, not a second copy to forget",
          make_portable.version() == "9.9.9", make_portable.version())
    # Wrapped across lines in the file, so match the words rather than the
    # whole sentence - this failed once on the line break alone.
    readme = (built / "README.txt").read_text(encoding="utf-8")
    check("the readme tells a portable user how to reach their browser",
          "Let your browser reach this" in readme and "registry" in readme)
    check("...and warns that a new drive letter needs it done again",
          "drive letter changes" in readme)

    # If one ever does turn up in dist, building must stop rather than ship it.
    (fake / "dist/Riplox/Data").mkdir()
    check("⭐ and building refuses outright if one is ever left there",
          make_portable.main() == 1)
finally:
    make_portable.ROOT, make_portable.DIST = real_root, real_dist

installer = (ROOT / "build" / "installer.iss").read_text(encoding="utf-8-sig")
check("the installer excludes it too - the second lock on that door",
      'Excludes: "bin\\*,Data\\*"' in installer)


print("\n-- and the real machine was never touched --------------------------")
check("⭐ every path stayed inside the sandbox",
      str(engine._installed_root()).startswith(str(SANDBOX)),
      engine._installed_root())
check("the real LOCALAPPDATA was left alone",
      REAL_LOCALAPPDATA != str(SANDBOX)
      and not (Path(REAL_LOCALAPPDATA) / engine.APP_NAME / ".write-test").exists()
      if REAL_LOCALAPPDATA else True)

shutil.rmtree(SANDBOX, ignore_errors=True)

print("\n" + "=" * 68)
print("  " + str(len(PASS)) + " passed, " + str(len(FAIL)) + " failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68 + "\n")
raise SystemExit(1 if FAIL else 0)
