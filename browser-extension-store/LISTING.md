# Chrome Web Store listing — copy for the dashboard

Everything the submission form asks for, written out. Nothing here is
guesswork about the extension: every claim below is something the code in this
folder actually does, which is the only kind of answer a review can be given.

---

## Item name

```
Riplox — Send to your download manager
```

*(38 characters.)*

> **Settled.** Brand first, then the words people actually type. "Riplox" alone
> is a name nobody searches for, and being found is the entire reason this
> listing exists — while a name that is only keywords throws away the brand and
> reads as stuffing. The comparable that works best on this store, *Free
> Download Manager*, gets both for free because its brand happens to be the
> search term; this is the same thing spelled out.

## Summary (132 characters max)

```
Send a link to Riplox, the download manager on your PC. The download happens in Riplox, not in the browser.
```

*(108 characters.)*

## Detailed description

```
Riplox is a download manager for Windows. This extension is the shortcut to it.

Click the toolbar button, or right-click a page or a link, and the address is handed to Riplox on your PC. That is the whole job. Nothing is fetched here and nothing is stored anywhere.

WHAT IT DOES
• Toolbar button — sends the page you are looking at
• Right-click a page — "Send this page to Riplox"
• Right-click a link — "Send this link to Riplox"
• An optional button on the page itself, which you can drag anywhere and dismiss on any site you do not want it
• A count on the icon showing how many downloads Riplox has on the go

WHAT IT DOES NOT DO
• It does not download anything itself
• It does not read the pages you visit. The optional in-page button draws itself and sends the address of the page it is on — nothing more
• It does not talk to the internet at all
• It does not collect, store or send any data anywhere

ACCESS TO PAGES IS OFF UNTIL YOU ASK FOR IT
Installing this grants no access to any website. That access is optional, and turning on the in-page button is what makes the browser ask you for it. Turning the button off hands it straight back.

HOW IT REACHES RIPLOX
Riplox's installer registers a small program with your browser, and the extension speaks to that program directly — no server, no account, no port to configure. If that route is not available, it falls back to a riplox:// link, which your browser will ask you to allow.

The settings stay in Riplox. Where files are saved, and at what quality, are decided there — this extension sends the address and nothing else, so whatever you have set in Riplox is what you get.

REQUIREMENTS
Riplox must already be installed on the same computer. Without it, this extension has nothing to send to.
```

## Category

```
Tools
```

> The dashboard picks this one from the package by itself, and it is the right
> one: a download-manager companion is a utility, and the category is a signal
> a reviewer reads. Workflow & Planning would also pass; nothing media-shaped
> should ever be chosen here.

## A note on pasting the description

The field keeps every newline exactly as given, so the block above is written
with each paragraph on **one long line**. It was hard-wrapped once, and the
listing came out breaking mid-sentence - "Without it," then "this" alone on the
line below. Do not re-wrap it to make this file tidier.

## Language

```
English
```

---

# Privacy tab

## Single purpose

```
Send the address of the page or link the user chooses to the Riplox download
manager installed on their own computer.
```

## Permission justifications

**activeTab**
```
Reads the address of the tab the user is currently looking at, and only at the
moment they click the extension's toolbar button. That address is what gets
handed to the Riplox application. The extension deliberately does not request
the "tabs" permission, so it has no way to see any other tab.
```

**contextMenus**
```
Adds the two right-click entries the extension is used through: "Send this page
to Riplox" and "Send this link to Riplox".
```

**nativeMessaging**
```
Hands the chosen address to the Riplox application already installed on the
user's computer, and reads back how many downloads Riplox currently has so the
count can be shown on the toolbar icon. This is a local connection to a program
registered by the Riplox installer. No network request is made.
```

**storage**
```
Remembers one setting: whether the user wants the download count shown on the
toolbar icon.
```

**alarms**
```
Schedules the periodic check that refreshes the download count on the toolbar
icon.
```

**scripting**
```
Registers the optional in-page button, and only while the user has switched it
on. The button is off by default; switching it off unregisters the script
again. Nothing is injected into any page until the user asks for it.
```

**Host permissions (optional, "*://*/*")**
```
Requested only at the moment the user switches on the optional in-page button,
never at install. The button is drawn by the extension and does nothing to the
page it sits on: when clicked it reads the address of that page and passes it
to Riplox, which is the same thing the toolbar button does. It does not read
page content, does not search the page for anything, and sends nothing but that
address. Switching the button off removes the permission.

The pattern is broad because the button is a general shortcut rather than
something aimed at particular sites — the user decides where they want it, and
can dismiss it per site.
```

## Remote code

```
No, I am not using remote code.
```

All logic is in the files in this package. Nothing is fetched or evaluated at
runtime.

## Data usage

Tick **nothing**. Then the certifications.

Google defines collection as transmitting data off the user's device, and this
extension transmits nothing off the device — the address goes to a program on
the same computer, over the browser's own native messaging channel. There is no
server, no analytics and no network request anywhere in the code.

The three certifications are all true and can be ticked:
- does not sell or transfer user data to third parties outside approved use cases
- does not use or transfer user data for purposes unrelated to the item's single purpose
- does not use or transfer user data to determine creditworthiness or for lending purposes

## Privacy policy URL

```
https://github.com/xniperbuilds/riplox-desktop/blob/main/PRIVACY-EXTENSION.md
```

Written and waiting in the repository root. **It has to be pushed and publicly
reachable before submitting** — a privacy policy URL that 404s is a rejection,
and it is the kind that costs a whole review cycle.

---

# Screenshots

Three, all 1280×800, in `screenshots/`. Upload in this order — the first is the
one shown on the card.

| File | Shows |
|---|---|
| `01-send-1280x800.png` | the popup mid-use, with Riplox working on two |
| `02-ways-1280x800.png` | the three ways in, and the "Riplox is not open" state |
| `03-access-1280x800.png` | what the extension is allowed to see, options open |

The popup in each is the **real** `popup.html`, loaded in an iframe from the
real files — so the pictures cannot drift away from the thing they are pictures
of. Regenerate them with `screenshots/compose.html` plus the render script if
the popup ever changes.

# Before submitting — the last check

1. **Push `PRIVACY-EXTENSION.md`** so the policy URL resolves. A 404 there
   costs a whole review cycle.
2. **Load this folder unpacked once** and confirm there is no Errors button on
   `chrome://extensions`. It can sit alongside the bundled build without a
   fight: this manifest has no `key`, so Chrome derives its ID from the folder
   path instead, and the two IDs differ.

   **Expect the silent route to fail in that test, and do not treat it as a
   bug.** `native-host.json` lists the bundled build's ID in `allowed_origins`
   and nothing else, so Chrome will refuse the native connection for this one
   and the extension will fall back to `riplox://` — a tab opening with a
   permission question in it is the *correct* behaviour here. The popup will
   also say the helper is not answering, for the same reason. Both go away once
   the published ID is added to `allowed_origins`.

# After it is published

The published item gets an ID of its own, and it will **not** be the bundled
build's `eceoennjnigbildembfcpdlmiaahocnm` — the store assigns its own. Two
things then have to be updated, and the silent route is broken for
store-installed users until they are:

- `native-host.json` → add the new ID to `allowed_origins`, **keeping the
  existing one** so the bundled build carries on working
- the installer → the registry key and the "open Chrome at the listing" step
  both need the published ID and the listing URL

Chrome also refuses a `key` field in a first upload, which is why this build's
manifest has none. After the first upload the public key can be taken from the
dashboard's Package tab and added, so every later upload keeps the same ID.
