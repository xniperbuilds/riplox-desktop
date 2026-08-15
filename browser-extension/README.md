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
| Sites | Optionally limit it to sites you pick. Empty means every site |
| Badge | How many downloads Riplox has running. Blank when Riplox is closed |
| In-page button | Off by default, and draggable. See below — this is the one that asks for access |

## How the handoff works

Two routes, tried in that order.

**The native host.** Riplox's installer registers a small program with Chrome,
Edge and Brave, and the extension speaks to it directly. This route is silent
and instant, opens no tab, and asks nothing — but it only exists where Riplox
was installed by its own installer, and it cannot start Riplox when Riplox is
closed.

**The `riplox://` link**, when the host does not answer. Windows knows which
program owns that scheme, so there is still no port to find and no token to
keep — and this route *can* start Riplox. Its cost is that the browser asks
first, in a tab that opens and closes itself. Current versions of Chrome ask
**every time**: the "always allow" tick that used to make this silent is no
longer offered. That is the browser's decision, not a setting here.

The popup says which route was used, because they feel different and a tab
appearing deserves an explanation.

## The in-page button, and why it is off

Turning it on adds a small Riplox button to video pages. That needs permission
to run on pages, which the extension does **not** hold when you install it:

- the access is declared as *optional*, so installing grants nothing;
- ticking the box makes the browser itself ask you;
- unticking it hands the access back;
- and if the browser refuses, the tick returns to off — a switch that is on
  while its work cannot happen is a lie.

**It can be dragged.** It starts in the bottom-right corner, which on YouTube
is directly over the suggestion list. Drag it anywhere; where you put it is
remembered and it comes back there on the next page. The position is stored as
a fraction of the window rather than in pixels, so moving to a smaller screen
does not leave it off the edge — and a window that shrinks pulls it back inside.
A short press is still a click: only a real drag moves it, and finishing a drag
does not send the page.

Everything else in the extension works without it.

## What it deliberately does not do

- **No `webRequest`.** The API older downloaders used to intercept video streams
  was removed in Manifest V3, and this design never needed it.
- **No `tabs`.** That would hand over the address of every tab in every window,
  all the time.
- **No reading pages by default.** Page access is optional, off, and only for
  the in-page button.
- **It never claims a download happened.** A scheme handoff gives nothing back,
  so the popup says *handed to Riplox* and nothing stronger. If Riplox is not
  running, nothing opens — and the popup says that is what to look for.

## Permissions, and why

| Permission | Why |
|---|---|
| `activeTab` | reads the address of the tab you are on — and only when you click the icon |
| `contextMenus` | the two right-click entries |
| `storage` | remembers the quality, the site list and the toggles |
| `nativeMessaging` | the silent route to Riplox, and where the badge count comes from |
| `alarms` | wakes the badge every 30 seconds |
| `scripting` | registers the in-page button, and only while it is switched on |
| `*://*/*` *(optional)* | page access for the in-page button. Not granted at install |

`activeTab` is the reason there is no `tabs` permission: it grants one tab,
once, in answer to a click of yours.

## Installing it

Riplox ships this folder. Until it is loaded for you:

1. Open `chrome://extensions`
2. Turn on **Developer mode**
3. **Load unpacked** → pick this folder

Works in Chrome, Edge and Brave. Firefox needs a small manifest change and is not
supported yet.

The badge and the silent route both need Riplox installed by its installer —
that is what registers the native host. Loading the extension on a machine
without it still works; every send just takes the `riplox://` route.

## Files

| File | What it is |
|---|---|
| `manifest.json` | Manifest V3 declaration |
| `background.js` | the right-click menu, the handoff, the site filter, the badge |
| `content.js` | the in-page button, injected only when it is turned on |
| `popup.html/.css/.js` | the toolbar popup |
| `icons/` | Riplox's own mark, resized — the app, the installer and this share one image |
