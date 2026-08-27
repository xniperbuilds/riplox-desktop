"""
Build the portable ZIP.

A portable Riplox is the same binary as the installed one. The only difference
is a folder called Data sitting beside the exe: engine.data_dir() finds it and
keeps everything in there instead of in LOCALAPPDATA.

⚠️ THE MARKER IS NEVER WRITTEN INTO dist/Riplox.

installer.iss copies dist\\Riplox\\* wholesale into the installed app. A Data
folder left in there would ship inside the installer, and every INSTALLED user
would silently get a new data root - their settings, history, queue and phone
pairing all apparently gone, with the real files still sitting in LOCALAPPDATA
where nobody would think to look. A per-user install is writable, so nothing
would fail and nothing would complain.

That is why this stages its own copy. installer.iss also excludes Data\\* now,
which is the second lock on the same door.

Run after the app is built:

    python -m PyInstaller build/riplox.spec --noconfirm
    python build/make_portable.py
"""
from __future__ import annotations

import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "Riplox"
STAGE = ROOT / "build" / "_portable"
OUT = ROOT / "dist_installer"

MARKER = "Data"

DATA_README = """This folder is what makes Riplox portable.

Riplox keeps its settings, history, download queue and phone pairing in here,
beside the program, instead of on the PC it happens to be running on. Delete
this folder and Riplox goes back to storing those things on the PC.

Do not put your own files in here.
"""

READ_ME = """Riplox {version} - portable

Unzip it anywhere - a USB stick, an external drive, a folder of your own - and
run Riplox.exe. There is nothing to install and nothing to uninstall.

WHAT PORTABLE MEANS HERE
  Everything Riplox saves goes into the Data folder beside the program:
  settings, history, the download queue, and the pairing with your phone.
  Nothing is written anywhere else on the PC.

  Downloads are the exception, on purpose. They go to your Downloads folder
  like any other program, because a 4 GB video landing on a USB stick by
  surprise helps nobody. Point Riplox somewhere else in Settings if you want
  them on the stick.

THE BROWSER EXTENSION
  It is in the browser-extension folder and it works - but a browser will only
  talk to a program it has been told about, and normally the installer does the
  telling. A portable copy has no installer.

  So open Settings, go to Browser, and press "Let your browser reach this
  copy". It adds one entry to the Windows registry, which is the one thing a
  portable copy otherwise never does - which is why it asks first, and why
  pressing it again takes the entry back out.

  Do that again if the drive letter changes: the entry points at a full path.

IF THE DRIVE LETTER CHANGES
  Riplox itself does not mind. Anything you pointed at by hand - a download
  folder on the stick - will need pointing at again.

IF THE DRIVE IS READ-ONLY
  Riplox will say so in Settings, under Advanced, and keep working by storing
  its things on the PC instead. It will not pretend it stayed portable.

FIRST RUN IS SLOWER
  Riplox keeps its own copy of the download engine so that updating it needs no
  administrator rights. That copy is made on the first run, onto the stick.

Riplox is free software under the GPL v3 - see LICENSE.txt.
https://xniperbuilds.com
"""


def version() -> str:
    """Read it from the app rather than keeping a second copy here to forget."""
    found = re.search(r'^VERSION\s*=\s*"([^"]+)"',
                      (ROOT / "src" / "app.py").read_text(encoding="utf-8-sig"),
                      re.M)
    return found.group(1) if found else "0.0.0"


def stage(into: Path) -> Path:
    """
    Assemble the portable folder in `into`, and return it.

    Separate from the zipping so the tests can look at what was assembled
    without building a 200 MB archive to do it.
    """
    app = into / "Riplox"
    if into.exists():
        shutil.rmtree(into)
    app.mkdir(parents=True)

    # The app itself. ignore Data in case one was ever left in dist by hand -
    # it would be harmless here, but the point is that this folder decides what
    # is portable, not whatever happened to be lying around.
    shutil.copytree(DIST, app, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(MARKER))
    # ⚠ The WebView2 bootstrapper lives in bin/ for the installer to pick up,
    # and has no business here. The portable build installs nothing - it is
    # unzipped and run - so a 1.7 MB prerequisite installer riding along in the
    # ZIP is weight that nothing would ever execute.
    shutil.copytree(ROOT / "bin", app / "bin", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("MicrosoftEdgeWebview2Setup.exe"))
    shutil.copytree(ROOT / "browser-extension", app / "browser-extension",
                    dirs_exist_ok=True)
    shutil.copy2(ROOT / "TERMS.txt", app / "TERMS.txt")
    shutil.copy2(ROOT / "LICENSE", app / "LICENSE.txt")

    # The marker, made here and only here.
    marker = app / MARKER
    marker.mkdir()
    (marker / "readme.txt").write_text(DATA_README, encoding="utf-8")

    (app / "README.txt").write_text(READ_ME.format(version=version()),
                                    encoding="utf-8")
    return app


def main() -> int:
    if not DIST.is_dir():
        print("no dist/Riplox - build the app first")
        return 1
    if (DIST / MARKER).exists():
        # Loud, because the quiet version of this is every installed user
        # losing their settings.
        print("REFUSING: dist/Riplox contains a " + MARKER + " folder. "
              "That must never ship in the installer - delete it.")
        return 1

    app = stage(STAGE)
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / ("Riplox_Portable_v" + version() + ".zip")
    if target.exists():
        target.unlink()

    files = sorted(p for p in app.rglob("*") if p.is_file())
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as zip_file:
        for one in files:
            zip_file.write(one, Path("Riplox") / one.relative_to(app))

    shutil.rmtree(STAGE, ignore_errors=True)
    size = target.stat().st_size
    print("built " + target.name)
    print("  " + str(len(files)) + " files, "
          + str(round(size / 1048576, 1)) + " MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
