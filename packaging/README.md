# Packaging

Riplox is listed in three Windows package managers. Each one keeps its own copy
of the version, the installer URL and its SHA-256, so **every release has to be
re-published to all three** — otherwise `winget install Riplox` keeps handing
people an old build.

## On every release

```
python packaging/bump.py
```

It reads the newest GitHub release, takes the installer's SHA-256 from the
release itself (no retyping, no local file that might be a different build),
rewrites all six manifest files and runs `winget validate`. Pass a version to
target an older one: `python packaging/bump.py 1.4.0`.

Then publish, in whatever order:

| | |
|---|---|
| **winget** | `wingetcreate update XniperBuilds.Riplox -u <installer-url> -v <version> -s` — or fork `microsoft/winget-pkgs` by hand and copy `winget/*` into `manifests/x/XniperBuilds/Riplox/<version>/`. A bot validates the PR; a moderator merges it. |
| **Chocolatey** | `cd packaging/chocolatey && choco pack && choco push riplox.<version>.nupkg` — needs the API key from community.chocolatey.org. |
| **Scoop** | Copy `scoop/riplox.json` into the bucket repo and push. `checkver`/`autoupdate` are set, so a bucket with the standard GitHub Action will bump itself. |

The listings that are not package managers — SourceForge files, the
AlternativeTo version, the download links on xniperbuilds.com — still need
updating by hand. `bump.py` prints that reminder at the end.

## Why the numbers come from GitHub, not from disk

The installer sitting in `dist_installer/` (or on someone's Desktop) can be a
later local build with the same file name as the published one. Its hash will
not match what people actually download, and winget refuses a manifest whose
hash is wrong. The release API is the only copy that is definitely the one being
served, so that is what `bump.py` reads.

## Why not the stable download link

Every release also carries a second copy under a name that never changes,
`Riplox_Setup.exe`, so the website's button can point at
`releases/latest/download/Riplox_Setup.exe` and never go stale. **Do not use
that URL here.** A package manager pins one version to one hash, and a URL whose
contents change on the next release would make every published manifest wrong at
once. These listings use the versioned file name on purpose.

## Notes on each manifest

**winget** — `InstallerType: inno`, so winget supplies
`/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-` itself; no switches are listed.
`Scope: user` because `installer.iss` sets `PrivilegesRequired=lowest`, which
also means no UAC prompt during an unattended install. `ProductCode` is the
Inno uninstall key (`{AppId}_is1`) and is what lets winget see the app as
already installed and offer an upgrade rather than a second copy.

**Chocolatey** — passes the same Inno switches explicitly, and declares yt-dlp
and FFmpeg in the description, which the moderators require for a package that
ships someone else's binaries.

**Scoop** — `innosetup: true` extracts the installer instead of running it.
**Not yet tested on a machine with Scoop.** Confirm the extracted folder
actually launches before pointing anyone at it.
