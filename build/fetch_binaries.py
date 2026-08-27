"""
Download the binaries Riplox ships with into bin/.

They are not kept in the repository: together they are ~180 MB, and all of them
are maintained upstream far more often than this app is. Run this once before
building.

    python build\\fetch_binaries.py
"""

import hashlib
import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin"

# The folder build, not the single .exe. The single file unpacks itself into
# a temp directory every time it runs - 2.2 seconds before a single request
# goes out, against 0.77 for the folder, measured on this machine. Riplox
# starts yt-dlp for every paste, every job and every watch check, so it is
# worth the extra 12 MB in the installer. --update-to still works on it.
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_win.zip"

# yt-dlp needs an external JavaScript runtime for YouTube. Deno is the one it
# prefers, but the Windows build is over 100 MB; QuickJS does the same job in
# two. Pinned and checksummed, because this ships inside the installer.
QJS_VERSION = "v0.16.1"
QJS_URL = ("https://github.com/quickjs-ng/quickjs/releases/download/"
           f"{QJS_VERSION}/qjs-windows-x86_64.exe")
QJS_SHA = "55a1b69cd4fdb6b0d3f8fdd910d0e89519f5330e408462084140c7b3b964fdae"

# Shared build: small executables plus their DLLs, roughly a third of the size
# of the static build. Every file must land in the same folder.
FFMPEG_URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
              "ffmpeg-master-latest-win64-gpl-shared.zip")

# ffplay is a media player we never invoke; skipping it saves ~17 MB.
FFMPEG_SKIP = {"ffplay.exe"}

# The WebView2 Evergreen Bootstrapper - ~1.7 MB, and the reason it is here is a
# real bug rather than a nicety.
#
# Setup used to show a message box when the Runtime was missing and wait for an
# answer. A silent install has nobody to answer it, so the installer HUNG:
# Chocolatey's verifier ran it on Windows Server 2019 (no WebView2) and killed
# it after 45 minutes. winget installs silently too, so the same hang would
# have reached every user on a machine without the Runtime.
#
# Microsoft permits shipping this and documents the switches:
#   "download the bootstrapper and package it with your WebView2 app"
#   MicrosoftEdgeWebview2Setup.exe /silent /install
#   https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution
#
# The other two options were weighed and rejected: the Standalone Installer is
# ~130 MB, and Microsoft's own docs say the Fixed Version binaries are "over
# 250 MB". Against a 71 MB installer, only the bootstrapper is affordable.
#
# ⚠ It downloads the Runtime at install time, so it needs a network. That is
# acceptable here - Riplox is a downloader - and Setup no longer blocks when it
# fails, it carries on and lets the app explain itself.
#
# Not checksummed on purpose: Microsoft revises this stub, and a pinned hash
# would turn every one of their updates into a failed build. The link is theirs
# and it is served over TLS.
WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"


def download(url: str) -> bytes:
    print(f"  {url}")
    with urllib.request.urlopen(url) as response:
        return response.read()


def fetch_ytdlp() -> None:
    target = BIN / "ytdlp"
    print("yt-dlp:")
    archive = zipfile.ZipFile(io.BytesIO(download(YTDLP_URL)))

    # Replaced rather than merged: a stale file left behind from an older
    # build inside _internal is exactly the kind of thing that works on this
    # machine and fails on someone else's.
    shutil.rmtree(target, ignore_errors=True)
    archive.extractall(target)

    # The old single-file copy, if this repo was built before the switch.
    (BIN / "yt-dlp.exe").unlink(missing_ok=True)

    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    print(f"  -> {target.name}\\ ({size / 1e6:.1f} MB)")


def fetch_qjs() -> None:
    # yt-dlp only recognises this runtime under the name "qjs".
    target = BIN / "qjs.exe"
    print(f"quickjs {QJS_VERSION}:")
    body = download(QJS_URL)

    digest = hashlib.sha256(body).hexdigest()
    if digest != QJS_SHA:
        raise SystemExit(f"  checksum mismatch - refusing to write\n"
                         f"  expected {QJS_SHA}\n  got      {digest}")

    target.write_bytes(body)
    print(f"  -> {target.name} ({target.stat().st_size / 1e6:.1f} MB, sha256 ok)")


def fetch_ffmpeg() -> None:
    print("ffmpeg:")
    archive = zipfile.ZipFile(io.BytesIO(download(FFMPEG_URL)))

    written = 0
    for member in archive.infolist():
        name = Path(member.filename).name
        if member.is_dir() or "/bin/" not in member.filename:
            continue
        if name in FFMPEG_SKIP:
            continue
        with archive.open(member) as source, open(BIN / name, "wb") as target:
            shutil.copyfileobj(source, target)
        written += 1
    print(f"  -> {written} files")


def fetch_webview2() -> None:
    target = BIN / "MicrosoftEdgeWebview2Setup.exe"
    print("webview2 bootstrapper:")
    body = download(WEBVIEW2_URL)

    # A redirect that lands on an error page would otherwise be written out as
    # an "installer" that Setup then tries to run.
    if len(body) < 500_000 or body[:2] != b"MZ":
        raise SystemExit(f"  that is not a Windows executable "
                         f"({len(body)} bytes) - refusing to write")

    target.write_bytes(body)
    print(f"  -> {target.name} ({target.stat().st_size / 1e6:.1f} MB)")


def main() -> None:
    BIN.mkdir(parents=True, exist_ok=True)
    try:
        fetch_ytdlp()
        fetch_qjs()
        fetch_ffmpeg()
        fetch_webview2()
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"download failed: {exc}")

    total = sum(f.stat().st_size for f in BIN.rglob("*") if f.is_file())
    print(f"\nbin/ is ready - {total / 1e6:.0f} MB")


if __name__ == "__main__":
    sys.exit(main())
