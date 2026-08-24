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
import sys
import urllib.error
import urllib.request

# (repo, the name that must never change between releases)
#
# A list rather than a mapping because one release can owe more than one stable
# name: the site's page has a Download button AND a portable link, and the
# portable one was added without this noticing - a check that passes while half
# the page 404s is worse than no check.
STABLE = [
    ("xniperbuilds/riplox-desktop", "Riplox_Setup.exe"),
    ("xniperbuilds/riplox-desktop", "Riplox_Portable.zip"),
    ("xniperbuilds/riplox",         "Riplox.apk"),
    ("xniperbuilds/riplox-tt",      "RiploxTT.apk"),
    ("xniperbuilds/riplox-ig",      "RiploxIG.apk"),
]

UA = {"User-Agent": "riplox-release-check", "Accept": "application/vnd.github+json"}


def latest(repo):
    url = "https://api.github.com/repos/%s/releases/latest" % repo
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.load(r)


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
    bad = []
    seen = {}          # one API call per repo: the allowance is 60 an hour
    for repo, name in STABLE:
        try:
            if repo not in seen:
                seen[repo] = latest(repo)
            rel = seen[repo]
        except Exception as exc:                       # noqa: BLE001
            print("%-30s COULD NOT CHECK  %s" % (repo, str(exc)[:40]))
            bad.append(repo)
            continue

        tag = rel.get("tag_name", "?")
        names = [a["name"] for a in rel.get("assets", [])]
        listed = name in names

        status, disp = reachable(repo, name)
        # 206 because the request asked for one byte; 200 is fine too.
        ok = listed and status in (200, 206) and "attachment" in disp

        print("%-30s %-9s %s" % (repo, tag, "OK" if ok else "PROBLEM"))
        if not listed:
            print("      the stable copy is missing. Upload it:")
            print("      gh release upload %s <file renamed to %s> --repo %s"
                  % (tag, name, repo))
            print("      assets present: %s" % (", ".join(names) or "none"))
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
