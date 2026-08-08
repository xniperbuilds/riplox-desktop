"""
Download the two binaries Riplox ships with into bin/.

They are not kept in the repository: together they are ~180 MB, and both are
maintained upstream far more often than this app is. Run this once before
building.

    python build\\fetch_binaries.py
"""

import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin"

YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"

# Shared build: small executables plus their DLLs, roughly a third of the size
# of the static build. Every file must land in the same folder.
FFMPEG_URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
              "ffmpeg-master-latest-win64-gpl-shared.zip")

# ffplay is a media player we never invoke; skipping it saves ~17 MB.
FFMPEG_SKIP = {"ffplay.exe"}


def download(url: str) -> bytes:
    print(f"  {url}")
    with urllib.request.urlopen(url) as response:
        return response.read()


def fetch_ytdlp() -> None:
    target = BIN / "yt-dlp.exe"
    print("yt-dlp:")
    target.write_bytes(download(YTDLP_URL))
    print(f"  -> {target.name} ({target.stat().st_size / 1e6:.1f} MB)")


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


def main() -> None:
    BIN.mkdir(parents=True, exist_ok=True)
    try:
        fetch_ytdlp()
        fetch_ffmpeg()
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"download failed: {exc}")

    total = sum(f.stat().st_size for f in BIN.iterdir() if f.is_file())
    print(f"\nbin/ is ready - {total / 1e6:.0f} MB")


if __name__ == "__main__":
    sys.exit(main())
