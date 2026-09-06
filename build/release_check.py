"""Check a published release before anyone is pointed at it.

The website's download buttons are plain links to

    github.com/<repo>/releases/latest/download/<stable name>

with no API call behind them, because the API allows 60 requests an hour per
IP and behind a mobile carrier thousands of people share one - the button
would start failing at exactly the moment a promotion was working.

The cost of that is this file. GitHub resolves that URL against whatever the
newest release is, so every release has to carry a copy under the stable name
as well as the versioned one. Forget it once and the button 404s, which is
worse than what it replaced. So it is checked rather than remembered.

    python build\\release_check.py

Reads nothing secret and changes nothing; it only looks and reports.
"""

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Where the site checkout sits when it is beside this one. Not required: the
# script still runs without it, on the list below.
SITE = Path(__file__).resolve().parents[2] / "XniperBuildsSite"

LINK = re.compile(
    r"github\.com/([^/\"'\s>]+/[^/\"'\s>]+)/releases/latest/download/([^\"'\s>]+)")

# (repo, the name that must never change between releases)
#
# ONLY a fallback now. The real list is read from the site's own HTML, because
# the failure this file exists to catch already happened once in the other
# direction: a portable link was added to the page and this list did not know
# about it, so the check passed while half the page 404ed. A hand-kept copy of
# what the site links to will drift again. The site is the source of truth for
# what the site promises.
STABLE = [
    ("xniperbuilds/riplox-desktop", "Riplox_Setup.exe"),
    ("xniperbuilds/riplox-desktop", "Riplox_Portable.zip"),
    ("xniperbuilds/riplox",         "Riplox.apk"),
    ("xniperbuilds/riplox-tt",      "RiploxTT.apk"),
    ("xniperbuilds/riplox-ig",      "RiploxIG.apk"),
]


def from_site():
    """
    Every stable-name link the site actually publishes, and the page it is on.

    Returns None when the site is not checked out beside this repo, which is
    the normal case for anyone who is not the author - the fallback list runs
    instead and the report says so.
    """
    if not SITE.is_dir():
        return None
    found = {}
    for page in sorted(SITE.rglob("*.html")):
        try:
            html = page.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for repo, name in LINK.findall(html):
            found.setdefault((repo, name), set()).add(
                page.relative_to(SITE).as_posix())
    return found or None


UA = {"User-Agent": "riplox-release-check", "Accept": "application/vnd.github+json"}


def latest(repo):
    url = "https://api.github.com/repos/%s/releases/latest" % repo
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.load(r)


def _versioned_twin(stable, tag, assets):
    """The v-numbered asset this stable name is meant to be a copy of.

    Riplox_Setup.exe -> Riplox_Setup_v1.6.0.exe, by the release's own tag, and
    only when that file is actually on the release. Returns "" when there is
    nothing to compare against - a release that carries no versioned twin is
    not a case this can judge, and guessing would be worse than saying nothing.
    """
    stem, dot, ext = stable.rpartition(".")
    if not dot:
        return ""
    want = "%s_v%s.%s" % (stem, tag.lstrip("v"), ext)
    return want if want in assets else ""


def stale_reason(stable, tag, assets):
    """Why this stable copy is not this release's build, or "".

    Same tag, same build, so the same number of bytes. A size costs nothing to
    read and is enough to tell a fresh copy from one left behind - which is the
    case that passes every other check in this file and still hands somebody
    the previous release.
    """
    twin = _versioned_twin(stable, tag, assets)
    if not twin:
        return ""
    mine, theirs = assets[stable].get("size"), assets[twin].get("size")
    if mine == theirs:
        return ""
    return ("%s is %s bytes and %s is %s - the stable copy is not this "
            "release's build" % (stable, mine, twin, theirs))


def reachable(repo, name):
    """Ask for the first byte the way a visitor's browser would."""
    url = "https://github.com/%s/releases/latest/download/%s" % (repo, name)
    req = urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"],
                                               "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.headers.get("content-disposition") or ""
    except urllib.error.HTTPError as exc:
        return exc.code, ""


def main():
    found = from_site()
    if found:
        targets = sorted(found)
        print("checking the %d stable link(s) the site actually publishes"
              % len(targets))
    else:
        targets = STABLE
        print("no site checkout beside this repo - using the built-in list")
    print()

    bad = []
    seen = {}          # one API call per repo: the allowance is 60 an hour
    for repo, name in targets:
        try:
            if repo not in seen:
                seen[repo] = latest(repo)
            rel = seen[repo]
        except Exception as exc:                       # noqa: BLE001
            print("%-30s COULD NOT CHECK  %s" % (repo, str(exc)[:40]))
            bad.append(repo)
            continue

        tag = rel.get("tag_name", "?")
        assets = {a["name"]: a for a in rel.get("assets", [])}
        names = list(assets)
        listed = name in assets

        status, disp = reachable(repo, name)
        # 206 because the request asked for one byte; 200 is fine too.
        ok = listed and status in (200, 206) and "attachment" in disp

        # 🔴 And the check this file did not have. Present, resolving and
        # downloading is what it tested - all three of which a copy left over
        # from the PREVIOUS release passes, while handing somebody software
        # two days out of date. That is not a 404; it is quietly wrong, which
        # is worse, and it happened on the 1.6.0 release.
        #
        # The versioned twin is the answer: same tag, same build, so the same
        # number of bytes. A size is enough to catch a stale copy and costs no
        # download; SHA256SUMS.txt now carries both names for anyone checking
        # by hand.
        stale = stale_reason(name, tag, assets) if listed else ""
        if stale:
            ok = False

        print("%-30s %-9s %s" % (repo, tag, "OK" if ok else "PROBLEM"))
        if stale:
            print("      " + stale)
            print("      re-upload it from this build:")
            print("      gh release upload %s <the v%s file renamed to %s> "
                  "--clobber --repo %s" % (tag, tag.lstrip("v"), name, repo))
        if not listed:
            print("      the stable copy is missing. Upload it:")
            print("      gh release upload %s <file renamed to %s> --repo %s"
                  % (tag, name, repo))
            print("      assets present: %s" % (", ".join(names) or "none"))
            if found:
                print("      linked from: %s"
                      % ", ".join(sorted(found[(repo, name)])))
        elif status not in (200, 206):
            print("      listed on the release but the link answered %s" % status)
        elif "attachment" not in disp:
            print("      the link works but does not download - it would open "
                  "in the browser instead")
        if not ok:
            bad.append(repo)

    print()
    if bad:
        print("NOT READY - the website's download button would fail for: %s"
              % ", ".join(bad))
        return 1
    print("Every download button on the site resolves to a real file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
