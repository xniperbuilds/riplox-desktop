"""
Build the What's new list from the commits, so nobody has to remember it.

The panel used to be a hand-written list in the template, and it sat four
features out of date while the app shipped twice. Nothing made it wrong until
somebody looked, which is the kind of staleness that never gets caught.

A commit opts in by carrying a trailer:

    Whats-new: Send a key or a password from your phone, sealed until copied.

Only those lines appear. A commit without one contributes nothing - deliberately,
because falling back to the subject would fill the panel with "Fix flaky test"
the first time somebody forgot, which is worse than the list it replaced and
harder to notice. Forget every trailer and the panel is empty, and an empty
panel is at least visible.

Run before packaging; writes src/whatsnew.json. Reading git at runtime would
work only for whoever runs from source - a packaged app has no repository.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "whatsnew.json"

# Enough for a release. A first release with no tag would otherwise list the
# entire history, which is not what anyone opens this panel for.
MAX_ITEMS = 40

TRAILER = re.compile(r"^\s*Whats-new:\s*(.+?)\s*$", re.I | re.M)


def git(*args: str) -> str:
    """Run git in the repo. Empty string if it fails for any reason."""
    try:
        done = subprocess.run(("git",) + args, cwd=str(ROOT),
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except OSError:
        return ""                      # no git on this machine
    return done.stdout.strip() if done.returncode == 0 else ""


def since() -> str:
    """The most recent tag, or "" to mean the whole history."""
    return git("describe", "--tags", "--abbrev=0")


def collect() -> list:
    """The trailer lines from newest commit to oldest."""
    tag = since()
    span = f"{tag}..HEAD" if tag else "HEAD"
    # A separator no commit message will contain, because a message can hold
    # blank lines and any newline-based split would cut them in half.
    raw = git("log", span, "--format=%B%x00")
    if not raw:
        return []

    items = []
    for message in raw.split("\x00"):
        for found in TRAILER.finditer(message):
            line = " ".join(found.group(1).split())
            if line and line.lower() != "skip" and line not in items:
                items.append(line)
    return items[:MAX_ITEMS]


def main() -> int:
    if not (ROOT / ".git").exists():
        print("not a git checkout - leaving whatsnew.json alone")
        return 0

    items = collect()
    OUT.write_text(json.dumps({"since": since(), "items": items}, indent=2),
                   encoding="utf-8")
    print(f"whatsnew.json: {len(items)} item(s) since {since() or 'the start'}")
    if not items:
        # Said out loud rather than left to be noticed in the app, because the
        # empty panel is the honest outcome but a silent one is still a
        # surprise at release time.
        print("  no Whats-new: trailers in that range - the panel will be empty")
    return 0


if __name__ == "__main__":
    sys.exit(main())
