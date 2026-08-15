# Riplox — browser extension

Send the page you are on to Riplox on this PC.

**It never touches a video.** It reads the address of the page and hands that
address to Riplox, which is already installed. Working out what is on the page,
choosing a format and fetching the bytes all happen in Riplox, where they always
have.

That is the whole design, and it is deliberate. An extension that fetched media
itself would need permissions this one does not ask for, would break every time a
site changed its player, and would be a downloader rather than a way to reach
one.

## What it does

| | |
|---|---|
| Toolbar button | Sends the page you are looking at |
| Right-click a page | **Send this page to Riplox** |
| Right-click a link | **Send this link to Riplox** |
| Quality | Chosen in the popup, remembered for next time |

## How the handoff works

The extension opens a `riplox://add?url=…&q=…` link. Windows already knows which
program owns that scheme, so:

- there is no port to find,
- there is no token to keep,
- and nothing is listening on this machine that a web page could reach.

The first time, Chrome asks whether Riplox may be opened. Ticking *always allow*
there is what makes every later send silent.

## What it deliberately does not do

- **No `webRequest`.** The API that older downloaders used to intercept video
  streams was removed in Manifest V3, and this design never needed it.
- **No host permissions.** It does not read the contents of any page.
- **No site names anywhere.** The extension does not know or care which site you
  are on; it forwards an address and stops there.
- **It never claims a download happened.** A scheme handoff gives nothing back,
  so the popup says *handed to Riplox* and nothing stronger. If Riplox is not
  running, nothing opens — and the popup says that is what to look for.

## Permissions, and why

| Permission | Why |
|---|---|
| `activeTab` | reads the address of the tab you are on — and only when you click the icon |
| `contextMenus` | the two right-click entries |
| `storage` | remembers the quality you picked |

That is all of them, and `activeTab` is the reason there is no `tabs`
permission. `tabs` would hand over the address of every tab in every window,
all of the time. `activeTab` grants one tab, once, in answer to a click of
yours — so the extension can only ever see the page you asked it about.

## Installing it

Riplox ships this folder. Until it is loaded for you:

1. Open `chrome://extensions`
2. Turn on **Developer mode**
3. **Load unpacked** → pick this folder

Works in Chrome, Edge and Brave. Firefox needs a small manifest change and is not
supported yet.

## Files

| File | What it is |
|---|---|
| `manifest.json` | Manifest V3 declaration |
| `background.js` | the right-click menu and the handoff |
| `popup.html/.css/.js` | the toolbar popup |
| `icons/` | Riplox's own mark, resized — the app, the installer and this share one image |
