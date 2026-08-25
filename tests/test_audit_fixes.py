"""The three bugs the 25-Aug audit found, each held shut by a test.

All three shared one shape: the software said the thing worked. That is why
none of them were reported by anyone - there was nothing to report.

  1. Any web page could read the room id off the LAN listener, because every
     reply carried Access-Control-Allow-Origin: *. A room id alone is enough to
     empty someone's waiting message at the relay.

  2. The native host wrote to LOCALAPPDATA while a portable app read its own
     Data folder, so the extension said "sent" over a link that went nowhere.

  3. "Best available" and "Highest" produced identical filenames, so the second
     one collided with the first, yt-dlp said "has already been downloaded",
     and the row went green over the older file.

⚠️ LOCALAPPDATA is redirected before anything is imported.
"""
import os
import re
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-auditfix-test-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "build"))

import engine                                               # noqa: E402
import native_host                                          # noqa: E402
import sharing                                              # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name
          + ((" | " + str(detail)[:90]) if detail else ""))


print("\n-- 1. the LAN listener does not talk to web pages -------------------")

# Read the handler's own source: the header must not be there to be sent.
handler = sharing._LanHandler
source = Path(sharing.__file__).read_text(encoding="utf-8")
json_fn = source.split("def _json(", 1)[1].split("\n    def ", 1)[0]

check("⭐ no Access-Control-Allow-Origin on any reply",
      "Access-Control-Allow-Origin" not in json_fn
      or "send_header(\"Access-Control-Allow-Origin\"" not in json_fn)
check("⭐ ...and none anywhere else in the listener either",
      'send_header("Access-Control-Allow-Origin"' not in source)
check("no OPTIONS handler - preflight is a browser thing and no browser is a "
      "legitimate client here",
      not hasattr(handler, "do_OPTIONS"))
check("the listener still answers a ping at all", hasattr(handler, "do_GET"))
check("...and still accepts a sealed envelope", hasattr(handler, "do_POST"))

# The reason the header was wrong is written down in this file. If somebody
# removes the measurement, the argument for the fix goes with it.
check("the mixed-content measurement is still recorded",
      "mixed content" in source.lower())


print("\n-- 2. the browser's link lands where the app reads it ---------------")

# Three layouts, each answered the way the app answers it.
installed = SANDBOX / "installed"
installed.mkdir(parents=True, exist_ok=True)
real_beside = native_host._beside
try:
    native_host._beside = lambda: installed
    check("an ordinary install -> LOCALAPPDATA, exactly as before",
          native_host.data_dir() == SANDBOX / native_host.APP_NAME,
          native_host.data_dir())

    # The portable ZIP: RiploxHost.exe sits beside Riplox.exe and Data.
    zipped = SANDBOX / "portable"
    (zipped / "Data").mkdir(parents=True, exist_ok=True)
    native_host._beside = lambda: zipped
    check("⭐ a portable copy -> the Data folder beside the exe",
          native_host.data_dir() == zipped / "Data", native_host.data_dir())

    # A PortableApps package: Data is at the package root, three levels up
    # from the exe, so it cannot be derived - the app has to say.
    paf = SANDBOX / "RiploxPortable"
    app_dir = paf / "App" / "Riplox"
    (paf / "Data").mkdir(parents=True, exist_ok=True)
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / native_host.POINTER).write_text(str(paf / "Data"),
                                               encoding="utf-8")
    native_host._beside = lambda: app_dir
    check("⭐ a PortableApps package -> the folder the app pointed at",
          native_host.data_dir() == paf / "Data", native_host.data_dir())

    # And the guard that stops the fix becoming the bug: a stale pointer.
    (app_dir / native_host.POINTER).write_text(str(SANDBOX / "gone-away"),
                                               encoding="utf-8")
    check("⭐ a pointer at a folder that no longer exists is not believed",
          native_host.data_dir() != SANDBOX / "gone-away",
          native_host.data_dir())
    check("...and nothing is invented to make it true",
          not (SANDBOX / "gone-away").exists())

    (app_dir / native_host.POINTER).write_text("   ", encoding="utf-8")
    check("an empty pointer is ignored rather than treated as a path",
          native_host.data_dir() == SANDBOX / native_host.APP_NAME,
          native_host.data_dir())
finally:
    native_host._beside = real_beside

# The other half: the app must actually write that pointer.
app_source = (ROOT / "src" / "app.py").read_text(encoding="utf-8-sig")
manifest_fn = app_source.split("def _write_host_manifest(", 1)[1][:1400]
check("⭐ the app writes the pointer when it connects a browser",
      "native_host.POINTER" in manifest_fn and "engine.data_dir()" in manifest_fn)
check("...and a failure to write it does not stop the button working",
      "except OSError" in manifest_fn)


print("\n-- 3. re-upload quality is a different file -------------------------")


class FakeJob:
    def __init__(self, quality):
        self.quality = quality
        self.start = self.end = ""
        self.opts = {}


settings = {"download_dir": str(SANDBOX / "dl")}
manager = engine.DownloadManager.__new__(engine.DownloadManager)

best = manager._outtmpl(settings, FakeJob("best"))
top = manager._outtmpl(settings, FakeJob("max"))
p1080 = manager._outtmpl(settings, FakeJob("1080"))

check("⭐ best and max no longer produce the same filename", best != top,
      Path(best).name)
check("⭐ ...and it is max that carries the mark, not best",
      "[max]" in top and "[max]" not in best, Path(top).name)
check("an ordinary quality is untouched - old files keep their names",
      "[max]" not in p1080 and Path(p1080).name == Path(best).name,
      Path(p1080).name)
check("both still carry the height, which is what stops 720p eating 1080p",
      "%(height)sp" in best and "%(height)sp" in top)
check("both still carry Riplox in the name",
      best.endswith(" Riplox.%(ext)s") and " Riplox" in top)

# The collision only mattered because of what happens when it happens.
engine_source = (ROOT / "src" / "engine.py").read_text(encoding="utf-8")
check("the 'already downloaded' path that made this silent still exists",
      "has already been downloaded" in engine_source)
check("⭐ and max is still kept out of the archive, the other half of the "
      "same intent",
      re.search(r'quality != "max"', engine_source) is not None)


print("\n-- the two smaller ones ---------------------------------------------")

sums_source = (ROOT / "build" / "sums.py").read_text(encoding="utf-8")
check("⭐ the checksums file names the APK as it is UPLOADED, not as built",
      "RiploxSend_Android_v%s.apk" in sums_source, )
check("...and the built name is still what gets hashed",
      "RiploxSend-v%s.apk" in sums_source)

bump_source = (ROOT / "packaging" / "bump.py").read_text(encoding="utf-8")
check("⭐ bump.py keeps the extension's version in step with the app",
      "browser-extension/manifest.json" in bump_source)

ext = (ROOT / "browser-extension" / "manifest.json").read_text(encoding="utf-8")
app_version = re.search(r'^VERSION\s*=\s*"([^"]+)"', app_source, re.M).group(1)
ext_version = re.search(r'"version":\s*"([^"]+)"', ext).group(1)
check("the extension and the app agree on the version right now",
      ext_version == app_version, ext_version + " vs " + app_version)


print("")
print(str(len(PASS)) + " passed, " + str(len(FAIL)) + " failed")
for name in FAIL:
    print("  FAILED: " + name)
import shutil                                               # noqa: E402
shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
