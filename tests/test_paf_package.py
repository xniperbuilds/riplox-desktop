"""PortableApps.com packaging - the layout, and the one thing that ties it to the app.

A PAF package puts the program at App\\Riplox\\ and its data at the PACKAGE
root. Riplox's own portable rule is "Data beside the exe", which in that layout
points at App\\Riplox\\Data - a private folder the PortableApps.com Menu never
backs up. The package would install, run, and look perfect, and every setting,
every history entry and the phone pairing would sit outside the backup. Nobody
finds out until they restore.

So the launcher is told to hand the real location over in an environment
variable, and engine._decide_root() takes it. That makes the variable name a
CONTRACT between a .ini file and a Python constant, in two different repos'
worth of distance from each other - the exact kind of pair that gets renamed on
one side. The last check in this file is the one that catches that.

Two silent failures are worth more than the feature working, and both are here:

  * an env var pointing somewhere that does not exist, quietly having the
    folder created for it - which hides a typo behind a package that syncs
    nothing;

  * an ORDINARY install or the plain ZIP behaving differently because this
    branch exists at all.

⚠️ LOCALAPPDATA is redirected before engine is imported. Patching save() is not
enough - private helpers write through their own paths.
"""
import configparser
import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-paf-test-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "build"))

from PIL import Image                                       # noqa: E402

import engine                                               # noqa: E402
import make_paf                                             # noqa: E402

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
    if hasattr(sys, "frozen"):
        del sys.frozen
    engine._root = None
    engine._root_state = "off"


def given(value):
    """Set the variable the launcher would set, or clear it."""
    if value is None:
        os.environ.pop(engine._PAF_DATA_VAR, None)
    else:
        os.environ[engine._PAF_DATA_VAR] = str(value)


REAL_EXECUTABLE = sys.executable
INSTALLED = SANDBOX / engine.APP_NAME

# The package as the launcher would lay it out: program down in App\Riplox,
# data at the root.
package = SANDBOX / "stick" / "RiploxPortable"
inside = package / "App" / "Riplox"
paf_data = package / "Data"
paf_data.mkdir(parents=True, exist_ok=True)


print("\n-- the launcher's Data folder wins ---------------------------------")

given(paf_data)
as_app(inside)
check("⭐ told where Data is -> that folder, and it counts as portable",
      engine.data_dir() == paf_data and engine.portable_state() == "on",
      engine.data_dir())

check("⭐ ...and NOT the folder beside the exe, which the menu never backs up",
      engine.data_dir() != inside / "Data", engine.data_dir())

# A package where somebody made Data beside the exe as well. The launcher's
# answer is still the right one - it is the folder that gets synced.
(inside / "Data").mkdir(parents=True, exist_ok=True)
as_app(inside)
check("⭐ beats a Data folder beside the exe when both exist",
      engine.data_dir() == paf_data, engine.data_dir())
shutil.rmtree(inside / "Data")


print("\n-- and it is not allowed to invent anything ------------------------")

missing = SANDBOX / "typo" / "Data"
given(missing)
as_app(inside)
check("a path that is not there is not believed",
      engine.data_dir() == INSTALLED, engine.data_dir())
check("⭐ ...and is NOT created - a typo must not hide behind a working app",
      not missing.exists())
check("...and Settings says off, which is the visible signal",
      engine.portable_state() == "off", engine.portable_state())

given("   ")
as_app(inside)
check("an empty value is ignored rather than treated as a path",
      engine.data_dir() == INSTALLED, engine.data_dir())

given(paf_data)
as_source()
sys.executable = str(inside / "Riplox.exe")
check("a checkout is never portable, variable or no variable",
      engine.data_dir() == INSTALLED and engine.portable_state() == "off",
      engine.data_dir())

real_writable = engine._writable
try:
    engine._writable = lambda folder: False
    given(paf_data)
    as_app(inside)
    check("⭐ told where Data is but cannot write -> falls back to this PC",
          engine.data_dir() == INSTALLED, engine.data_dir())
    check("⭐ ...and says so, rather than looking portable",
          engine.portable_state() == "read-only", engine.portable_state())
finally:
    engine._writable = real_writable


print("\n-- an ordinary copy is untouched by any of this --------------------")

given(None)
plain = SANDBOX / "installed-app"
as_app(plain)
check("no variable, no Data folder -> the usual place on this PC",
      engine.data_dir() == INSTALLED and engine.portable_state() == "off",
      engine.data_dir())

zipped = SANDBOX / "zip" / "Riplox"
(zipped / "Data").mkdir(parents=True, exist_ok=True)
as_app(zipped)
check("no variable, Data beside the exe -> the plain ZIP still works",
      engine.data_dir() == zipped / "Data" and engine.portable_state() == "on",
      engine.data_dir())

as_source()
sys.executable = REAL_EXECUTABLE
given(None)


print("\n-- the package that gets built -------------------------------------")

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

brand = SANDBOX / "brand.png"
Image.new("RGBA", (1024, 1024), (7, 12, 21, 255)).save(brand)

real = (make_paf.ROOT, make_paf.DIST, make_paf.BRAND, make_paf.ICO)
try:
    make_paf.ROOT = fake
    make_paf.DIST = fake / "dist/Riplox"
    make_paf.BRAND = brand
    make_paf.ICO = SANDBOX / "no-such.ico"      # exercise the fallback
    built = make_paf.stage(SANDBOX / "staged")

    check("the program sits at App\\Riplox, where the format wants it",
          (built / "App" / "Riplox" / "Riplox.exe").is_file())
    check("⭐ Data is at the package root - the folder their menu backs up",
          (built / "Data").is_dir())
    check("⭐ and there is NO second Data folder beside the exe",
          not (built / "App" / "Riplox" / "Data").exists())
    check("dist/Riplox is left alone",
          not (fake / "dist/Riplox/Data").exists())
    check("the engine and the extension come along",
          (built / "App/Riplox/bin/ffmpeg.exe").is_file()
          and (built / "App/Riplox/browser-extension/manifest.json").is_file())
    check("the licence ships with it, as the GPL requires",
          (built / "App/Riplox/LICENSE.txt").is_file())

    info = configparser.ConfigParser()
    info.optionxform = str
    info.read(built / "App/AppInfo/appinfo.ini", encoding="utf-8")

    check("appinfo.ini parses at all", info.has_section("Details"))
    check("the format version is the one the packages ship, not the stale doc",
          info["Format"]["Version"] == "3.9", info["Format"]["Version"])
    check("the category is one of the ten they accept",
          info["Details"]["Category"] in (
              "Accessibility", "Development", "Education", "Games",
              "Graphics & Pictures", "Internet", "Music & Video", "Office",
              "Security", "Utilities"),
          info["Details"]["Category"])
    check("the description is inside the 512-character limit",
          len(info["Details"]["Description"]) <= 512,
          len(info["Details"]["Description"]))
    check("the version comes from the app, not a second copy to forget",
          info["Version"]["DisplayVersion"] == "9.9.9",
          info["Version"]["DisplayVersion"])

    package_version = info["Version"]["PackageVersion"]
    bits = package_version.split(".")
    check("PackageVersion is four numbers, which their installer insists on",
          len(bits) == 4 and all(b.isdigit() for b in bits), package_version)

    check("every licence answer is there, and true for a GPL app",
          all(info["License"][k] == "true"
              for k in ("Shareable", "OpenSource", "Freeware",
                        "CommercialUse")))

    # interpolation off: the value is literally %PAL:DataDir%, and Python's
    # default parser reads % as its own substitution and refuses the file.
    launcher = configparser.ConfigParser(interpolation=None)
    launcher.optionxform = str
    launcher.read(built / "App/AppInfo/Launcher/RiploxPortable.ini",
                  encoding="utf-8")

    program = launcher["Launch"]["ProgramExecutable"]
    check("⭐ ProgramExecutable resolves - it is relative to App, not the root",
          (built / "App" / program).is_file(), program)

    check("⭐ SingleAppInstance is off, so a portable copy can run beside an "
          "installed one",
          launcher["Launch"].get("SingleAppInstance") == "false",
          launcher["Launch"].get("SingleAppInstance"))

    start = info["Control"]["Start"]
    check("Start names the launcher, relative to the package root - a "
          "different base from ProgramExecutable",
          start == "RiploxPortable.exe", start)

    for size in (16, 32, 75, 128, 256):
        icon = built / ("App/AppInfo/appicon_%d.png" % size)
        square = icon.is_file() and Image.open(icon).size == (size, size)
        check("appicon_%d.png is there and is %dx%d" % (size, size, size),
              square)
    check("appicon.ico is there even with no prebuilt one to copy",
          (built / "App/AppInfo/appicon.ico").is_file())

    check("the Data folder explains itself",
          (built / "Data" / "readme.txt").is_file())
    check("help.html ships, as the format expects",
          (built / "help.html").is_file())

    print("\n-- the contract between the .ini and the app -----------------------")

    names = dict(launcher["Environment"])
    check("⭐ launcher.ini sets the exact variable engine.py reads",
          engine._PAF_DATA_VAR in names,
          str(list(names)) + " vs " + engine._PAF_DATA_VAR)
    check("⭐ ...and sets it to the launcher's own Data directory",
          names.get(engine._PAF_DATA_VAR) == "%PAL:DataDir%",
          names.get(engine._PAF_DATA_VAR))
finally:
    make_paf.ROOT, make_paf.DIST, make_paf.BRAND, make_paf.ICO = real


print("")
print(str(len(PASS)) + " passed, " + str(len(FAIL)) + " failed")
for name in FAIL:
    print("  FAILED: " + name)
shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
