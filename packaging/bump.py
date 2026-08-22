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
import urllib.request

REPO = "xniperbuilds/riplox-desktop"
HERE = os.path.dirname(os.path.abspath(__file__))
ASSET = "Riplox_Setup_v{version}.exe"


def api(path):
    req = urllib.request.Request(
        "https://api.github.com/" + path,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "riplox-bump"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


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

    name = ASSET.format(version=version)
    asset = next((a for a in release["assets"] if a["name"] == name), None)
    if asset is None:
        raise SystemExit(
            "ERROR: release v%s has no asset named %s.\nIt has: %s"
            % (version, name, ", ".join(a["name"] for a in release["assets"]))
        )

    url = asset["browser_download_url"]
    digest = (asset.get("digest") or "").replace("sha256:", "")
    sha = (digest or sha256_of(url)).upper()

    print("Riplox v%s  (%s)" % (version, date))
    print("  %s" % url)
    print("  sha256 %s" % sha)
    print("  %.1f MB" % (asset["size"] / 1048576))

    write(
        "winget/XniperBuilds.Riplox.yaml",
        sub(read("winget/XniperBuilds.Riplox.yaml"), (r"^PackageVersion: .+$", "PackageVersion: " + version)),
    )
    write(
        "winget/XniperBuilds.Riplox.installer.yaml",
        sub(
            read("winget/XniperBuilds.Riplox.installer.yaml"),
            (r"^PackageVersion: .+$", "PackageVersion: " + version),
            (r"^ReleaseDate: .+$", "ReleaseDate: " + date),
            (r"(?m)^  InstallerUrl: .+$", "  InstallerUrl: " + url),
            (r"(?m)^  InstallerSha256: .+$", "  InstallerSha256: " + sha),
        ),
    )
    write(
        "winget/XniperBuilds.Riplox.locale.en-US.yaml",
        sub(
            read("winget/XniperBuilds.Riplox.locale.en-US.yaml"),
            (r"^PackageVersion: .+$", "PackageVersion: " + version),
            (r"^ReleaseNotesUrl: .+$", "ReleaseNotesUrl: https://github.com/%s/releases/tag/v%s" % (REPO, version)),
        ),
    )

    scoop = json.loads(read("scoop/riplox.json"))
    scoop["version"] = version
    scoop["architecture"]["64bit"]["url"] = url
    scoop["architecture"]["64bit"]["hash"] = sha.lower()
    write("scoop/riplox.json", json.dumps(scoop, indent=4) + "\n")

    write(
        "chocolatey/riplox.nuspec",
        sub(
            read("chocolatey/riplox.nuspec"),
            (r"<version>.+?</version>", "<version>%s</version>" % version),
            (r"<releaseNotes>.+?</releaseNotes>", "<releaseNotes>https://github.com/%s/releases/tag/v%s</releaseNotes>" % (REPO, version)),
        ),
    )
    write(
        "chocolatey/tools/chocolateyinstall.ps1",
        sub(
            read("chocolatey/tools/chocolateyinstall.ps1"),
            (r"(?m)^(\s*url64bit\s*=\s*)'.+'$", r"\g<1>'%s'" % url),
            (r"(?m)^(\s*checksum64\s*=\s*)'.+'$", r"\g<1>'%s'" % sha),
        ),
    )

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
