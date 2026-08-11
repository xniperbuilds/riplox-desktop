# Riplox Send

A share-sheet target for Android and nothing else. Share a link from any app
and the PC running [Riplox Desktop](https://xniperbuilds.com/riplox-desktop/)
downloads it.

**81 KB.** No libraries, no analytics, no accounts, and one permission —
`INTERNET`. It cannot read your clipboard, your storage or anything else,
because it never needs to.

## Why it exists

A web share target *always* opens the page it belongs to. The platform offers
no way not to, so sharing a video meant watching a browser window appear, do
its job and sit there waiting to be dismissed.

`ShareActivity` uses `Theme.NoDisplay` and calls `finish()` in `onCreate`, so
the share sheet closes and nothing else happens on screen. The only thing you
see is a toast saying what your PC did with the link.

## How it talks to the PC

Through the same relay the web page uses — a postbox that cannot read what it
carries. Every message is AES-GCM with a 12-byte nonce and a 128-bit tag, and
the key never leaves this phone and that PC.

The PC seals its own answer under the same key and leaves it on the way back,
so the toast can say *"Downloading on your PC"* or *"This phone is paused"*
rather than a hopeful *"Sent"*.

Three states a phone could not previously tell apart, and now can:

| What you see | What it means |
|---|---|
| Your PC is on and this phone is allowed | paired, running, not paused |
| Your PC removed this phone | access was revoked — paste a new code |
| No answer | the PC is off, or Riplox is not running |

The revoked case needed a change on the PC as well: a removed device's key is
kept for thirty days *purely so its owner can be told*. Without it, "revoked"
and "switched off" are both silence.

## Pairing

On the PC: **Riplox → Sharing → Show code → Copy code**. Paste it into the app.
The code lasts two minutes and works once.

The phone generates its own key and sends it sealed inside that first message,
so the code is spent the moment it is used — anyone who later finds it, in a
chat or a screenshot, holds something that opens nothing.

The code box stays available after pairing. When a PC revokes a phone the fix
is a new code, and that should never mean reinstalling the app.

## Building

```
build.ps1
```

aapt2 → javac → d8 → zipalign → apksigner, straight through the SDK tools.
Four classes and one layout with no dependencies at all; the Android Gradle
Plugin would download several hundred megabytes of Maven to produce the same
APK.

`build\pack_for_relay.py` then embeds the APK in the relay so the pairing page
can offer it as a download.

⚠️ Builds are signed with the **debug key**. That is fine for installing on
your own phone and wrong for handing to anyone else — a release key has to be
decided before this is distributed.
