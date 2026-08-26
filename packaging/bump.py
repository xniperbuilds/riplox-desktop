"""Point the package-manager listings at a new Riplox release.

Every release has to be re-published to winget, Scoop and Chocolatey, and each
one wants the same three facts: the version, the installer URL and its SHA-256.
Getting one of them wrong is not a small mistake - winget refuses the manifest
and Chocolatey refuses the install - so this reads all three from the GitHub
release itself instead of anyone retyping them.

    python packaging/bump.py            # the newest release
    python packaging/bump.py 1.4.0      # a particular one

Then follow the steps it prints. Nothing here publishes anything.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

REPO = "xniperbuilds/riplox-desktop"
HERE = os.path.dirname(os.path.abspath(__file__))
ASSET = "Riplox_Setup_v{version}.exe"
# Scoop takes the portable ZIP instead. Unpacking a folder is what Scoop
# is for, and it means a Scoop user keeps their settings across an update:
# the app writes into Data\ beside the exe, and the manifest persists it.
PORTABLE = "Riplox_Portable_v{version}.zip"


def api(path):
    req = urllib.request.Request(
        "https://api.github.com/" + path,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "riplox-bump"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        # 403 here is nearly always the rate limit: GitHub allows 60 requests an
        # hour per IP unauthenticated, and behind a mobile carrier that hour is
        # shared with everyone else on the same address. Release day is exactly
        # when a stack trace is least useful.
        if e.code == 403:
            raise SystemExit(
                "ERROR: GitHub refused the request (403). Unauthenticated calls are\n"
                "limited to 60 an hour per IP address. Wait an hour, or run\n"
                "'gh auth status' and try again from a network that is not shared."
            )
        if e.code == 404:
            raise SystemExit("ERROR: no such release or repository: %s" % path)
        raise SystemExit("ERROR: GitHub returned HTTP %s for %s" % (e.code, path))
    except urllib.error.URLError as e:
        raise SystemExit("ERROR: could not reach GitHub (%s). Check the connection." % e.reason)


def sha256_of(url):
    """Fall back to hashing the file when the release predates GitHub's digest field."""
    print("  digest missing from the API - downloading to hash it")
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "riplox-bump"})
    with urllib.request.urlopen(req, timeout=300) as r:
        for chunk in iter(lambda: r.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write(relpath, text):
    """Write UTF-8 with no BOM. Other tools read these files; a BOM breaks them."""
    path = os.path.join(HERE, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)
    print("  wrote " + relpath)


def sub(text, *pairs):
    for pattern, replacement in pairs:
        text, n = re.subn(pattern, replacement, text, flags=re.M)
        if n == 0:
            raise SystemExit("ERROR: nothing matched %r - the template changed" % pattern)
    return text


def read(relpath):
    with open(os.path.join(HERE, relpath), encoding="utf-8") as f:
        return f.read()


def main():
    want = sys.argv[1].lstrip("v") if len(sys.argv) > 1 else None
    release = api("repos/%s/releases/tags/v%s" % (REPO, want) if want else "repos/%s/releases/latest" % REPO)
    version = release["tag_name"].lstrip("v")
    date = release["published_at"][:10]

    def pick(pattern):
        """The named asset's url, sha256 and size - or stop, saying what is there."""
        name = pattern.format(version=version)
        asset = next((a for a in release["assets"] if a["name"] == name), None)
        if asset is None:
            raise SystemExit(
                "ERROR: release v%s has no asset named %s.\nIt has: %s"
                % (version, name, ", ".join(a["name"] for a in release["assets"]))
            )
        found = asset["browser_download_url"]
        digest = (asset.get("digest") or "").replace("sha256:", "")
        return found, (digest or sha256_of(found)).upper(), asset["size"]

    url, sha, size = pick(ASSET)
    # Looked up before anything is written, so a release that forgot the ZIP
    # stops here rather than leaving Scoop pointing at the previous version
    # while winget and Chocolatey have moved on.
    zip_url, zip_sha, zip_size = pick(PORTABLE)

    print("Riplox v%s  (%s)" % (version, date))
    print("  %s" % url)
    print("  sha256 %s" % sha)
    print("  %.1f MB" % (size / 1048576))
    print("  %s" % zip_url)
    print("  sha256 %s" % zip_sha)
    print("  %.1f MB" % (zip_size / 1048576))

    scoop = json.loads(read("scoop/riplox.json"))
    scoop["version"] = version
    scoop["architecture"]["64bit"]["url"] = zip_url
    scoop["architecture"]["64bit"]["hash"] = zip_sha.lower()

    # Every substitution runs before anything is written. A template that has
    # drifted makes sub() exit, and it has to exit while all six files still
    # agree with each other - a run that rewrites two of them and then stops
    # leaves winget claiming one version and Chocolatey another, which is worse
    # than not running at all because nothing on screen says the set is now
    # mixed.
    staged = {
        "winget/XniperBuilds.Riplox.yaml": sub(
            read("winget/XniperBuilds.Riplox.yaml"),
            (r"^PackageVersion: .+$", "PackageVersion: " + version),
        ),
        "winget/XniperBuilds.Riplox.installer.yaml": sub(
            read("winget/XniperBuilds.Riplox.installer.yaml"),
            (r"^PackageVersion: .+$", "PackageVersion: " + version),
            (r"^ReleaseDate: .+$", "ReleaseDate: " + date),
            (r"(?m)^  InstallerUrl: .+$", "  InstallerUrl: " + url),
            (r"(?m)^  InstallerSha256: .+$", "  InstallerSha256: " + sha),
        ),
        "winget/XniperBuilds.Riplox.locale.en-US.yaml": sub(
            read("winget/XniperBuilds.Riplox.locale.en-US.yaml"),
            (r"^PackageVersion: .+$", "PackageVersion: " + version),
            (r"^ReleaseNotesUrl: .+$", "ReleaseNotesUrl: https://github.com/%s/releases/tag/v%s" % (REPO, version)),
        ),
        "scoop/riplox.json": json.dumps(scoop, indent=4) + "\n",
        "chocolatey/riplox.nuspec": sub(
            read("chocolatey/riplox.nuspec"),
            (r"<version>.+?</version>", "<version>%s</version>" % version),
            (r"<releaseNotes>.+?</releaseNotes>", "<releaseNotes>https://github.com/%s/releases/tag/v%s</releaseNotes>" % (REPO, version)),
        ),
        "chocolatey/tools/chocolateyinstall.ps1": sub(
            read("chocolatey/tools/chocolateyinstall.ps1"),
            (r"(?m)^(\s*url64bit\s*=\s*)'.+'$", r"\g<1>'%s'" % url),
            (r"(?m)^(\s*checksum64\s*=\s*)'.+'$", r"\g<1>'%s'" % sha),
        ),
        # ⚠️ The extension ships inside the installer and the portable ZIP, so
        # its version is the app's version. Nothing used to bump it and it sat
        # at 1.3.0 while the app moved on - which the Chrome Web Store would
        # have caught as a listing that does not match the build it belongs to.
        "../browser-extension/manifest.json": sub(
            read("../browser-extension/manifest.json"),
            (r'(?m)^(\s*"version":\s*)"[^"]+"', r'\g<1>"%s"' % version),
        ),
    }
    for relpath, text in staged.items():
        write(relpath, text)

    # winget validate only checks the schema, so a manifest that points at the
    # stable download name instead of the versioned one passes it cleanly - and
    # then pins a hash to a URL whose contents change at the next release, which
    # every installed user sees as a checksum mismatch. Nothing else catches
    # that, so check it here, along with every file agreeing on one version.
    #
    # ⚠️ The names are worked out here, not inside pick(). This loop used to
    # reach for `name`, which only ever existed as a local inside pick(), so it
    # raised NameError the first time anything got this far - AFTER all seven
    # manifests had already been written. The 24 Aug dry run stopped earlier,
    # on a missing asset, and never reached this code at all: a check that has
    # never once run is not a check.
    versioned = {url: ASSET.format(version=version),
                 zip_url: PORTABLE.format(version=version)}
    for relpath, text in staged.items():
        for one_url, one_name in versioned.items():
            if one_url in text and one_name not in text:
                raise SystemExit(
                    "ERROR: %s carries the download URL but not the versioned "
                    "asset name %s" % (relpath, one_name))
        if "releases/latest/download/" in text:
            raise SystemExit(
                "ERROR: %s points at a stable download name. What sits behind "
                "that name changes at the next release, so the hash pinned "
                "beside it turns into a checksum mismatch for every user."
                % relpath)
    versions = {relpath: version in text for relpath, text in staged.items()}
    missing = [r for r, ok in versions.items() if not ok]
    if missing:
        raise SystemExit("ERROR: these files do not mention version %s: %s" % (version, ", ".join(missing)))

    print("\nChecking the winget manifest")
    try:
        r = subprocess.run(
            ["winget", "validate", "--manifest", os.path.join(HERE, "winget")],
            capture_output=True, text=True, shell=True,
        )
        print("  " + (r.stdout.strip() or r.stderr.strip() or "no output"))
        if r.returncode != 0:
            raise SystemExit("winget validate failed - fix the manifest before sending it")
    except FileNotFoundError:
        print("  winget not on PATH - skipped")

    print(
        """
Next, by hand:

  winget       fork microsoft/winget-pkgs, copy packaging/winget/* into
               manifests/x/XniperBuilds/Riplox/{v}/ and open a PR
               (or: wingetcreate update XniperBuilds.Riplox -u "{u}" -v {v} -s)

  Chocolatey   cd packaging/chocolatey && choco pack && choco push riplox.{v}.nupkg
               (needs the API key from community.chocolatey.org)

  Scoop        copy packaging/scoop/riplox.json into the bucket repo and push

  Also update  SourceForge files + AlternativeTo version, and the download
               links on xniperbuilds.com/riplox-desktop/
""".format(v=version, u=url)
    )


if __name__ == "__main__":
    main()
