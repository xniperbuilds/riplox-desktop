"""
Lay out the PortableApps.com package.

PortableApps.com will not take a ZIP. It wants a .paf.exe, which is their own
self-extracting installer wrapped around a fixed folder layout - and inside
that layout the app does NOT sit at the top:

    RiploxPortable\\
      RiploxPortable.exe      <- their launcher, not ours (see below)
      App\\
        AppInfo\\             <- the metadata their menu reads
        Riplox\\              <- our program, three levels down
      Data\\                  <- settings live HERE, at the package root
      Other\\

That last point is the whole reason this script needs a matching change in
engine.py. Riplox's own portable rule is "a Data folder beside the exe", which
in this layout would mean App\\Riplox\\Data - a second, private folder that the
PortableApps menu's backup and sync never look at. Everything would work and
nothing would ever be backed up.

Their launcher does not volunteer the answer either: %PAL:DataDir% is only a
substitution inside launcher.ini, not something the launched program can read.
So we declare it ourselves, in [Environment] below, and engine._decide_root()
picks it up. That name is ours, which is also why nothing on a normal PC sets
it and an ordinary install is untouched.

⚠️ RiploxPortable.exe CANNOT BE BUILT FROM HERE. It is produced by the
PortableApps.com Launcher, a Windows GUI tool, from the .ini files this script
writes. That step is manual and always will be. This script gets everything
ready for it and then says so.

Run after the app is built:

    python -m PyInstaller build/riplox.spec --noconfirm
    python build/make_paf.py
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "Riplox"
STAGE = ROOT / "build" / "_paf"
BRAND = ROOT / "build" / "brand" / "riplox_1024.png"
ICO = ROOT / "src" / "static" / "img" / "riplox.ico"

MARKER = "Data"
APP_ID = "RiploxPortable"

# Verified against the format reference, not guessed:
#   * Category must be one of ten exact strings; "Internet" is where
#     downloaders live.
#   * Description is capped at 512 characters.
#   * PackageVersion must be four numbers, so 1.4.0 becomes 1.4.0.0.
#   * Start is relative to the PACKAGE root; ProgramExecutable, over in
#     launcher.ini, is relative to App\\. Two different bases, one letter
#     apart in meaning - worth reading twice.
#   * Format Version is 3.9. The online manual still says 2.0; the shipping
#     packages say 3.9 and so does the format page. The manual is stale.
CATEGORY = "Internet"
DESCRIPTION = (
    "Riplox downloads video from the web, and lets you send a link from your "
    "phone straight to your PC. Share a link on the phone and the download "
    "starts here - over your own Wi-Fi, with no internet connection needed. "
    "Free and open source under the GPL v3."
)

APPINFO = """[Format]
Type=PortableAppsFormat
Version=3.9

[Details]
Name=Riplox Portable
AppID={app_id}
Publisher=XniperBuilds
Homepage=xniperbuilds.com/riplox-desktop
Category={category}
Description={description}
Language=Multilingual

[License]
Shareable=true
OpenSource=true
Freeware=true
CommercialUse=true

[Version]
PackageVersion={package_version}
DisplayVersion={version}

[Control]
Icons=1
Start={app_id}.exe
"""

# Two settings, and the second one is not optional.
#
# SingleAppInstance defaults to TRUE, and true means the launcher refuses to
# start when a local copy of the app is already running. Riplox went to some
# trouble to make exactly that work: the single-instance mutex is named after
# the data folder, so a portable copy and an installed copy are different
# programs as far as Windows is concerned and both can run. Leaving this at the
# default would throw that away and show the user an error instead.
LAUNCHER_INI = """[Launch]
ProgramExecutable=Riplox\\Riplox.exe
SingleAppInstance=false

[Environment]
RIPLOX_PORTABLE_DATA=%PAL:DataDir%
"""

DATA_README = """This folder is what the PortableApps.com Menu backs up.

Riplox keeps its settings, history, download queue and phone pairing in here.
Delete this folder and Riplox starts over with its defaults.

Do not put your own files in here. Downloads do not come here - they go to
your Downloads folder, unless you point Riplox somewhere else in Settings.
"""

APP_README = """Riplox Portable {version}

This is Riplox packaged in PortableApps.com Format. Run it from the
PortableApps.com Menu, or run RiploxPortable.exe directly.

Everything Riplox saves goes in the Data folder at the top of this package, so
the PortableApps.com Menu backs it up and syncs it with everything else.

THE BROWSER EXTENSION
  It is in App\\Riplox\\browser-extension. A browser will only talk to a
  program it has been told about, and normally an installer does the telling -
  a portable copy has none. Open Settings, go to Browser, and press "Let your
  browser reach this copy". Press it again to take it back out.

  Do that again if the drive letter changes: the entry holds a full path.

FIRST RUN IS SLOWER
  Riplox keeps its own copy of the download engine so updating it needs no
  administrator rights. That copy is made on the first run, onto the drive.

Riplox is free software under the GPL v3. Source: App\\Riplox\\LICENSE.txt
https://xniperbuilds.com
"""

HELP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Riplox Portable</title>
<style>
 body {{ font-family: system-ui, sans-serif; max-width: 40em; margin: 3em auto;
        padding: 0 1.5em; line-height: 1.6; color: #16202e; }}
 code {{ background: #eef2f7; padding: .1em .35em; border-radius: 3px; }}
</style>
</head>
<body>
<h1>Riplox Portable {version}</h1>
<p>Riplox downloads video from the web, and lets you send a link from your
phone straight to this PC &mdash; over your own Wi&#8209;Fi, with no internet
connection needed.</p>

<h2>Where your things are kept</h2>
<p>Settings, history, the download queue and the pairing with your phone all
live in the <code>Data</code> folder at the top of this package, so the
PortableApps.com Menu backs them up with everything else.</p>
<p>Downloads are the exception, on purpose: they go to your Downloads folder,
because a 4&nbsp;GB video landing on a USB stick by surprise helps nobody.
Point Riplox somewhere else in Settings if you would rather have them here.</p>

<h2>The browser extension</h2>
<p>It ships in <code>App\\Riplox\\browser-extension</code>. Open Settings,
go to Browser, and press <em>Let your browser reach this copy</em>. Press it
again to undo it. Do it again if the drive letter changes.</p>

<h2>Licence</h2>
<p>Riplox is free software under the GPL&nbsp;v3, built on yt-dlp and FFmpeg.
See <code>App\\Riplox\\LICENSE.txt</code>.</p>
<p><a href="https://xniperbuilds.com">xniperbuilds.com</a></p>
</body>
</html>
"""

SOURCE_README = """Riplox is free software under the GPL v3.

The complete source is at https://github.com/xniperbuilds/riplox-desktop

This package contains the program as built, plus the PortableApps.com launcher
configuration in App\\AppInfo\\Launcher.
"""


def version() -> str:
    """Read it from the app rather than keeping a second copy here to forget."""
    found = re.search(r'^VERSION\s*=\s*"([^"]+)"',
                      (ROOT / "src" / "app.py").read_text(encoding="utf-8-sig"),
                      re.M)
    return found.group(1) if found else "0.0.0"


def package_version(display: str) -> str:
    """1.4.0 -> 1.4.0.0. Their installer rejects anything that is not four."""
    parts = [p for p in display.split(".") if p.isdigit()]
    while len(parts) < 4:
        parts.append("0")
    return ".".join(parts[:4])


def icons(into: Path) -> None:
    """
    appicon.ico plus the PNGs their menu draws at various sizes.

    16 and 32 are required, 128 encouraged; 75 and 256 are here because the
    shipping packages carry them and the menu asks for them at higher DPI.
    """
    master = Image.open(BRAND).convert("RGBA")
    for size in (16, 32, 75, 128, 256):
        art = master.resize((size, size), Image.LANCZOS)
        art.save(into / ("appicon_%d.png" % size), "PNG", optimize=True)

    if ICO.exists():
        shutil.copy2(ICO, into / "appicon.ico")
    else:
        master.save(into / "appicon.ico", "ICO",
                    sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])


def stage(into: Path) -> Path:
    """
    Assemble the package in `into`, and return its root.

    Separate from everything else so the tests can look at what was assembled
    without needing PyInstaller output or their GUI tool.
    """
    display = version()
    if into.exists():
        shutil.rmtree(into)

    root = into / APP_ID
    app = root / "App"
    info = app / "AppInfo"
    program = app / "Riplox"
    (info / "Launcher").mkdir(parents=True)
    (root / MARKER).mkdir(parents=True)
    (root / "Other" / "Source").mkdir(parents=True)

    # The program, three levels down where the format wants it. Data is
    # ignored for the same reason make_portable.py ignores it: this script
    # decides the layout, not whatever was lying around in dist.
    shutil.copytree(DIST, program, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(MARKER))
    shutil.copytree(ROOT / "bin", program / "bin", dirs_exist_ok=True)
    shutil.copytree(ROOT / "browser-extension", program / "browser-extension",
                    dirs_exist_ok=True)
    shutil.copy2(ROOT / "TERMS.txt", program / "TERMS.txt")
    shutil.copy2(ROOT / "LICENSE", program / "LICENSE.txt")

    (info / "appinfo.ini").write_text(
        APPINFO.format(app_id=APP_ID, category=CATEGORY,
                       description=DESCRIPTION, version=display,
                       package_version=package_version(display)),
        encoding="utf-8", newline="\r\n")
    (info / "Launcher" / (APP_ID + ".ini")).write_text(
        LAUNCHER_INI, encoding="utf-8", newline="\r\n")

    if len(DESCRIPTION) > 512:
        raise SystemExit("description is over the 512-character limit")

    icons(info)

    (root / MARKER / "readme.txt").write_text(DATA_README, encoding="utf-8")
    (app / "Readme.txt").write_text(APP_README.format(version=display),
                                    encoding="utf-8")
    (root / "help.html").write_text(HELP_HTML.format(version=display),
                                    encoding="utf-8")
    (root / "Other" / "Source" / "Readme.txt").write_text(
        SOURCE_README, encoding="utf-8")
    return root


def main() -> int:
    if not DIST.is_dir():
        print("no dist/Riplox - build the app first")
        return 1
    if (DIST / MARKER).exists():
        print("REFUSING: dist/Riplox contains a " + MARKER + " folder. "
              "That must never ship - delete it.")
        return 1
    if not BRAND.exists():
        print("brand mark not found: " + str(BRAND))
        return 1

    root = stage(STAGE)
    files = sorted(p for p in root.rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in files)
    print("staged " + str(root))
    print("  " + str(len(files)) + " files, "
          + str(round(size / 1048576, 1)) + " MB")
    print("")
    print("NOT FINISHED - the launcher exe has to be made by hand:")
    print("  1. Install the PortableApps.com Platform, then its Launcher")
    print("     and Installer from the app store inside it.")
    print("  2. Point the Launcher at " + str(root))
    print("     It reads App\\AppInfo\\Launcher\\" + APP_ID + ".ini and")
    print("     writes " + APP_ID + ".exe into the package root.")
    print("  3. Point the Installer at the same folder to get the .paf.exe.")
    print("")
    print("Then RUN it from a stick and check Settings shows portable, and")
    print("that the settings file landed in " + APP_ID + "\\Data - NOT in")
    print("App\\Riplox\\Data. That is the whole point of the change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
