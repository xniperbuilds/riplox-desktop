# Riplox Desktop

A free video downloader for Windows. Paste a link, pick a quality, keep the file.

Works with YouTube, TikTok, Instagram, Facebook, X, Reddit and around a
thousand other sites.

- **No limits.** No download caps, no daily quota, no paid tier.
- **No bundled software.** The installer contains the app and nothing else.
- **Plays anywhere.** Files come out as H.264 MP4 by default, so they open in
  Windows Media Player, WhatsApp and on phones without extra codecs.
- **Updates itself.** Sites change how they serve video; the download engine
  can be updated from inside the app without waiting for a new release.

## Features

| | |
|---|---|
| Quality | 4K, 1440p, 1080p, 720p, 480p, 360p, or MP3 audio |
| Playlists | Queue an entire playlist or channel in one go |
| Queue | Several downloads at once, with live speed and ETA |
| Clipboard | Copy a link anywhere and the app offers to take it |
| Cookies | Optionally use your browser's cookies for private or age-gated videos |
| Library | Every finished file, with play and show-in-folder |

## Install

Download the setup file from the releases page and run it. It installs for the
current user, so it does not ask for administrator rights.

Windows may show a "Windows protected your PC" screen the first time, because
the installer is new and not yet widely downloaded. Choose **More info →
Run anyway**. Verify the SHA-256 checksum published with the release if you
want to confirm the file is untouched.

Requires Windows 10 or 11 with the Microsoft Edge WebView2 Runtime, which is
already present on Windows 11 and on any Windows 10 with a current Edge.

## Building from source

```
pip install -r requirements.txt
python build\fetch_binaries.py
python build\make_icon.py
pyinstaller build\riplox.spec --noconfirm
```

`fetch_binaries.py` pulls `yt-dlp.exe` and an ffmpeg shared build into `bin\`.
They are not committed to this repository — together they are around 180 MB and
both are updated upstream far more often than this app.

The build lands in `dist\Riplox`. To produce the installer, copy `bin\` beside
the executable and compile `build\installer.iss` with Inno Setup 6.

### Running the interface during development

```
python src\app.py --dev
```

This serves the UI at `http://127.0.0.1:5010` in a normal browser instead of
the app window.

## How it works

The interface is HTML rendered in a native window. A local server on a random
port drives it, and every request carries a token issued at startup so nothing
else on the machine can talk to it. Downloads are handled by `yt-dlp`, and
merging and audio conversion by `ffmpeg`. Both ship with the app.

Settings, history and the updatable engine live in
`%LOCALAPPDATA%\RiploxDesktop`.

## Notes

Downloading is subject to the terms of the site you download from and to the
copyright of the material. Use it for content you own, content licensed for
reuse, or content you have permission to keep.

## Licence

Riplox Desktop is distributed under the GPL-3.0 licence — see `LICENSE`.

It bundles two third-party programs, unmodified:

- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) — Unlicense (public domain)
- [`ffmpeg`](https://ffmpeg.org/) — a GPL build from
  [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds), whose source and
  build scripts are available at that repository

`build\fetch_binaries.py` shows exactly which builds are used and where they
come from.
