"""The host's version must be the app's version.

native_host.py is built into its own executable from that one file, so it
cannot import the app to ask. It carries a copy instead - and a copy that
nobody checks is a copy that goes stale the first time a release happens in a
hurry. The extension reads that number to decide whether to tell somebody their
Riplox is too old, so a stale copy here becomes a wrong message there.
"""
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import native_host                                          # noqa: E402


def app_version() -> str:
    """Read app.py's VERSION without importing it - importing app.py starts
    building an application, and this test wants one line out of a file."""
    text = (SRC / "app.py").read_text(encoding="utf-8")
    found = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M)
    return found.group(1) if found else ""


PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          (" | " + detail) if detail else ""))


print("\n-- the host knows which Riplox it belongs to " + "-" * 22)
app = app_version()
check("app.py has a VERSION", bool(app), app)
check("native_host.VERSION matches it",
      native_host.VERSION == app,
      "host=%s app=%s" % (native_host.VERSION, app))

print("\n-- and it reports it " + "-" * 45)
reply = {"ok": True, "version": native_host.VERSION}
check("the status answer carries a version", bool(reply.get("version")),
      reply["version"])

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
print("=" * 68)
sys.exit(1 if FAIL else 0)
