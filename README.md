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
| Quality | 4K, 1440p, 1080p, 720p, 480p, 360p, or MP3 audio with cover art and tags |
| Playlists | The whole playlist in one click, or tick only the ones you want — filter by title, sort by length or name, take the first N, invert, shift-click ranges, and a download button on each row |
| Channels | A channel link opens its sections — Videos, Shorts, Live — and any one of them behaves as an ordinary playlist |
| Queue | Several at once, with live speed and ETA, real pause and resume, and a speed limit |
| Clipboard | Copy a link anywhere and Riplox catches it, even while hidden. Optionally queue it without asking |
| Shortcut | A global hotkey downloads whatever link you just copied, without switching windows |
| Drag and paste | Drop a link on the window, or paste twenty at once |
| Trim | Cut a section out by timestamp, with exact-cut frames when you need them |
| Subtitles | Download or embed them, keep chapters, or skip sponsor segments |
| Convert | Turn anything already on the disk into MP3, M4A, OPUS, FLAC or WAV — remuxed rather than re-encoded when the codec already fits |
| Watch | Follow a channel or playlist and be told what is new. It never downloads on its own |
| Send to Riplox | Share a link from your phone and this PC downloads it — see below |
| Tray | Closing the window keeps downloads running, with progress on the taskbar button and a notification when each file lands |
| Sign-in | Sign in through your own browser for private, members-only and age-gated videos |
| Library | Every finished file, with search, sort, a filter per site, play and show-in-folder |
| Backup | Export and import your settings; export your links as txt, csv or json |
| Themes | Light and dark, or whichever the system is using |

### Send to Riplox

Pair a phone once and share a link to it from any app; the download starts on
this PC, on this PC's connection and disk.

The PC **never opens a port**. It dials out to a relay and holds one request
open, so nothing on the internet can reach the machine. Every message is
AES-GCM ciphertext with a nonce and a timestamp: the relay carries it without
being able to read it, and a captured message cannot be replayed. Only a link
travels — never a video.

The phone generates its own key during pairing, so the pairing code is spent
the moment it is used; a code left in a chat afterwards opens nothing. Codes
last two minutes and work once.

Each paired device can be paused, renamed, limited (per day, largest file,
total GB, quality cap, which sites) and given a folder of its own, or removed
outright. A removed phone is told it was removed rather than left guessing.

Two ways in on the phone: a web page that needs nothing installed, or
**Riplox Send**, a small Android app that takes a share without opening
anything at all. Both are offered from the pairing link. The relay is in
[`relay/`](relay/).

### Watching a channel

Add a channel or a playlist and Riplox checks it on a timer and lists what it
has not seen before. **It never downloads by itself** — every download is still
a button you press.

Repeated automated requests are what sites answer with "are you a bot", so this
is kept deliberately small: the newest 30 items only, one check at a time, at
least 90 seconds apart, once every 12 hours per item by default. The screen
says all of this before it can be switched on, and carries the fix if it ever
happens.

### AI-upscaled video

YouTube generates higher-resolution versions of older uploads with AI and
offers them beside the original. Riplox skips them by default, because
"download this video" means the video. A rung that only exists as an upscale
is therefore not offered at all — and if you turn them on in Settings, the
quality chip says what it really is: *1080p · AI-upscaled from 480p*.

### Cookies

Chrome, Edge and Brave encrypt their cookie database so that only the browser
itself can read it (Chrome 127, July 2024). Nothing here tries to break that.
Instead, **Sign in with your browser** opens the browser you already have on a
profile of its own; once you have signed in and closed the window, Riplox asks
that browser for its cookies over the DevTools protocol — the browser's own
supported interface. Your normal browser profile is never opened or copied.

What is captured is stored encrypted with DPAPI, is written out in the clear
only into a temp file for the length of one download, and is only ever handed to
the site it came from. Firefox still works directly, and an exported
`cookies.txt` can be used instead.

Signing in is optional — public videos do not need it. Downloading heavily
while signed in can get that account limited by the site, so a spare account is
the safer choice.

### YouTube

YouTube expects requests to carry a token proving they came from a real player,
and answers "Sign in to confirm you're not a bot" when one is missing. Riplox
deals with this in three layers, none of which involve an account:

- A JavaScript runtime (QuickJS, 2 MB) ships with the app. Without one, YouTube's
  newer streaming path hands back formats that have no download URL at all.
- Requests are paced, and a refusal is retried automatically on a different
  player client. Most of these clear on their own.
- An optional helper that generates the token locally can be downloaded from
  Settings. It is off by default, checked against a pinned SHA-256 before it is
  run, listens only on 127.0.0.1, and can be removed with one click.

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

`fetch_binaries.py` pulls `yt-dlp.exe`, a QuickJS build and an ffmpeg shared
build into `bin\`. They are not committed to this repository — together they are
around 180 MB and all of them are updated upstream far more often than this app.
QuickJS is pinned and checksummed there, because it ships inside the installer.

The build lands in `dist\Riplox`. To produce the installer, copy `bin\` beside
the executable and compile `build\installer.iss` with Inno Setup 6.

`build\make_icons.py` is a different script: it rebuilds the two PWA icons the
relay serves, which are what make the phone page installable — and therefore
what puts it in Android's share sheet at all.

### Running the interface during development

```
python src\app.py --dev
```

This serves the UI at `http://127.0.0.1:5010` in a normal browser instead of
the app window.

## How it works

The interface is HTML rendered in a native window. A local server on a random
port drives it, and every request carries a token issued at startup so nothing
else on the machine can drive it. The single exception is the call that raises
the window: launching Riplox while it is hidden in the tray hands the running
copy back instead of starting a second one, and a starting copy cannot know the
token. That endpoint only shows a window.

Downloads are handled by `yt-dlp`, and merging and audio conversion by
`ffmpeg`. Both ship with the app.

Settings, history and the updatable engine live in
`%LOCALAPPDATA%\RiploxDesktop`. Pairing keys are kept in their own file there,
never in the settings, so a settings backup cannot carry one machine's paired
phones onto another.

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
- [`QuickJS-NG`](https://github.com/quickjs-ng/quickjs) — MIT

The optional YouTube helper is **not** bundled. If you turn it on, Riplox
downloads [`bgutil-ytdlp-pot-provider-rs`](https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs)
(GPL-3.0) from its own release page and verifies it before running it.

`build\fetch_binaries.py` shows exactly which builds are used and where they
come from.
