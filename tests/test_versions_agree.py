"""
Every file that names the version names the same one.

The version is written down in four places that all ship inside a build, and
nothing checked that they agreed. TERMS.txt lost that argument silently for
two releases: the installer showed a document headed "for Riplox Desktop
1.4.1" and then installed 1.5.0. Nobody reads a licence screen closely enough
to catch it, which is exactly why it needs checking rather than remembering.

The four:

    src/app.py            VERSION - the one the app reports about itself
    build/installer.iss   AppVersion - the installer's own name for it
    build/version_info.txt Windows' file properties, as four numbers
    TERMS.txt             the document a person accepts before installing

`src/app.py` is the source of truth: it is the one a running program can be
asked for.

Deliberately NOT checked: packaging/. Those manifests point at a published
release and are written by packaging/bump.py after the assets exist, so a
manifest still naming the last release is correct until the next one goes
out. Checking them here would fail on every build for being right.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


def read(rel):
    path = ROOT / rel
    return path.read_text(encoding="utf-8") if path.exists() else ""


print("\n-- what the app says it is ------------------------------------------")
app = read("src/app.py")
found = re.search(r'^VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', app, re.M)
check("src/app.py declares a version", bool(found),
      found.group(1) if found else "no VERSION line")
if not found:
    print("\n  Nothing else can be checked against it.")
    sys.exit(1)

VERSION = found.group(1)
print(f"      the build is {VERSION}")


print("\n-- and every file that ships with it agrees --------------------------")

iss = re.search(r'^#define\s+AppVersion\s+"([^"]+)"', read("build/installer.iss"), re.M)
check("build/installer.iss AppVersion", bool(iss) and iss.group(1) == VERSION,
      iss.group(1) if iss else "not found")

# Windows wants four numbers; the fourth is a build counter nobody uses here.
info = read("build/version_info.txt")
for field in ("FileVersion", "ProductVersion"):
    m = re.search(r"StringStruct\('" + field + r"',\s*'([0-9.]+)'\)", info)
    got = m.group(1) if m else ""
    check(f"build/version_info.txt {field}",
          got.startswith(VERSION + ".") or got == VERSION,
          got or "not found")

# The line a person actually reads before pressing Install.
terms = read("TERMS.txt")
head = re.search(r"^Terms version ([0-9.]+), for Riplox Desktop ([0-9.]+)",
                 terms, re.M)
check("TERMS.txt has its heading line", bool(head),
      head.group(0) if head else "not found")
check("TERMS.txt names this version",
      bool(head) and head.group(2) == VERSION,
      f"says {head.group(2)}" if head else "")


print("\n-- and the terms are the ones the installer shows --------------------")
# A licence file the installer does not point at is a file nobody sees.
iss_text = read("build/installer.iss")
check("the installer shows TERMS.txt before installing",
      "LicenseFile=..\\TERMS.txt" in iss_text)
check("and leaves a copy in the install folder",
      'Source: "..\\TERMS.txt"; DestDir: "{app}"' in iss_text)


print("\n-- packaging is not checked here, on purpose -------------------------")
# Stated rather than silent: someone will grep for the version, find these,
# and wonder why they are behind.
wg = read("packaging/winget/XniperBuilds.Riplox.yaml")
at = re.search(r"^PackageVersion:\s*([0-9.]+)", wg, re.M)
print(f"      winget manifest says {at.group(1) if at else '?'} - written by "
      f"packaging/bump.py once")
print("      the release exists, so it trails the build by design")


print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
sys.exit(1 if FAIL else 0)
