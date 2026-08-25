"""
Write SHA256SUMS.txt for the artefacts THIS release is made of.

Four dead hashes went round in one week. Every rebuild - a fix, the extension,
portable mode - quietly retired the numbers written into the launch notes, and
a wrong hash is worse than a missing one: it asks to be verified and then fails
the verification.

So they stop being copied by hand. This works out the four names the current
versions imply, hashes exactly those, and writes one file beside them. The
launch notes point at that file; the release publishes it, which is what the
site already promises ("SHA-256 published").

⚠ It is also the check for "did I rebuild everything?". A name it cannot find
is an error, not a warning - a release missing one of these is the failure this
exists to catch.

    python build\\sums.py
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist_installer" / "SHA256SUMS.txt"


def version_of(path: Path, pattern: str) -> str:
    found = re.search(pattern, path.read_text(encoding="utf-8-sig"), re.M)
    if not found:
        raise SystemExit("could not read a version from " + path.name)
    return found.group(1)


def wanted() -> list:
    """
    (path, published name, why) for everything a release carries.

    ⚠️ The published name is not always the built name. The APK is built as
    RiploxSend-v1.0.5.apk and uploaded as RiploxSend_Android_v1.0.5.apk,
    because the release before it used the underscore form and a name that
    changes between releases is a name nobody can link to. A checksums file
    that lists the BUILT name describes a file that does not exist on the
    release - which is worse than publishing no checksum at all, because it
    asks to be verified and then cannot be.
    """
    app = version_of(ROOT / "src" / "app.py", r'^VERSION\s*=\s*"([^"]+)"')
    send = version_of(ROOT / "send-windows" / "src" / "app.py",
                      r'^VERSION\s*=\s*"([^"]+)"')
    apk = version_of(ROOT / "send-android" / "AndroidManifest.xml",
                     r'versionName="([^"]+)"')

    here = ROOT / "dist_installer"
    return [
        (here / ("Riplox_Setup_v%s.exe" % app),
         "Riplox_Setup_v%s.exe" % app, "the installer"),
        (here / ("Riplox_Portable_v%s.zip" % app),
         "Riplox_Portable_v%s.zip" % app, "the portable build"),
        (ROOT / "send-windows" / "dist_installer"
             / ("RiploxSend_Setup_v%s.exe" % send),
         "RiploxSend_Setup_v%s.exe" % send, "the Windows sender"),
        (ROOT / "send-android" / "dist" / ("RiploxSend-v%s.apk" % apk),
         "RiploxSend_Android_v%s.apk" % apk, "the phone sender"),
    ]


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


def main() -> int:
    missing, lines = [], []
    for path, published, why in wanted():
        if not path.is_file():
            missing.append("  %-36s %s - NOT BUILT" % (path.name, why))
            continue
        # The PUBLISHED name goes in the file, not the built one - see wanted().
        lines.append("%s  %s" % (digest(path), published))
        note = "" if published == path.name else "  (upload as this name)"
        print("  %-36s %11d B  %s%s" % (published, path.stat().st_size,
                                        lines[-1][:16] + "...", note))

    if missing:
        print("\nmissing:")
        for line in missing:
            print(line)
        print("\nnothing written - build these first, or the release ships "
              "without them.")
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\nwrote " + str(OUT.relative_to(ROOT)))
    print("upload it with the release - the site promises SHA-256 is published")
    return 0


if __name__ == "__main__":
    sys.exit(main())
