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

# Lines that describe the machinery rather than the app. They were worth
# writing - they say what the release actually did - but somebody opening
# What's new wants to know what changed for THEM, and "the relay keeps a log of
# its own health" is not that. Matched on a fragment, so the wording can be
# tidied later without this list quietly going stale.
INTERNAL = (
    "relay no longer lets other websites",
    "relay now paces each device",
    "What's new writes itself",
)

# Lines that were true when the commit was written and are not true now,
# because the change was taken back out. History is not rewritten for this -
# the commits happened - but a panel that announces a room nobody can find is
# worse than one that says nothing, and it is the app's own voice saying it.
#
# The whole 1.5 redesign was reverted to the classic interface on 30 August.
# These eight lines describe screens that no longer exist. Matched on a
# fragment, like INTERNAL, so tidying the wording later cannot silently
# resurrect one.
# Checked one at a time against the interface that is actually shipping, not
# assumed from the commit they came in: every line here names something that
# was built on a redesigned screen and has no home in the classic one. Three
# lines from that same range survived the check and are still in the panel -
# the shortcut telling you which keys Windows granted, a download's own log,
# and the rail's live panel, which was rebuilt on the classic rail afterwards.
#
# So this list is not "everything from those commits". A line leaves it the
# moment the feature exists again, or the panel starts lying in the other
# direction - hiding something the app really does.
WITHDRAWN = (
    "Riplox has been redesigned",
    "Queue and Failed are one room",
    "Converting to audio opens over your library",
    "Press Ctrl+K anywhere",
    "Insights shows what your library already knows",
    "Insights can write your library out",
    "says what it left out",
    "asked three questions on first run",
    "already in your library",
    "when each channel is next checked",
    "whether you are a bot",
    "Links held by ask-before-starting",
    "lists what still works",
    "download window is drawn as a bar",
    "whether the download engine is the current one",
    "filters by what a file is",
)


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
    """
    The most recent RELEASE tag, or "" to mean the whole history.

    Only tags shaped like a version count. The repository holds other kinds -
    a tag marking the classic interface, for one - and "the newest tag" quietly
    became one of those the moment such a tag existed: the range turned into
    nothing, and the panel came out empty with no complaint. What this wants is
    the last release, so that is what it asks for.
    """
    return git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*")


def collect() -> list:
    """The trailer lines from newest commit to oldest."""
    tag = since()
    span = f"{tag}..HEAD" if tag else "HEAD"
    # A separator no commit message will contain, because a message can hold
    # blank lines and any newline-based split would cut them in half.
    raw = git("log", span, "--format=%B%x00")
    if not raw:
        return []

    items, hidden, gone = [], 0, 0
    for message in raw.split("\x00"):
        for found in TRAILER.finditer(message):
            line = " ".join(found.group(1).split())
            if not line or line.lower() == "skip" or line in items:
                continue
            if any(mark in line for mark in INTERNAL):
                hidden += 1          # real work, but nothing a user can act on
                continue
            if any(mark in line for mark in WITHDRAWN):
                gone += 1            # shipped in a commit, taken back out since
                continue
            items.append(line)

    # The panel lists features and nothing else - Nazim, 28 Aug: "whatsnew man
    # sirf features daalna, fixes khatam kr do". The plumbing used to get one
    # summary line here; it is a changelog's job, not this panel's. `hidden`
    # is still counted so the run says how many lines it left out.
    if hidden:
        print(f"  {hidden} internal line(s) left out - this panel is features only")
    if gone:
        print(f"  {gone} line(s) left out - the change was reverted since")
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
