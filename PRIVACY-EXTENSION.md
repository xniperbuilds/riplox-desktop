# Privacy policy — Riplox browser extension

*Last updated: 3 September 2026*

The Riplox browser extension collects nothing, stores nothing about you, and
sends nothing anywhere.

This is not a promise about intent. It is a description of what the code can
do, and the extension is small enough that the claim is checkable: the whole of
it is a few files, and they are published in this repository under
`browser-extension-store/`.

## What the extension handles

One thing: **the address of a page or link you choose to send.**

You choose it by clicking the extension's toolbar button, or by using one of
its two right-click entries. At that moment the extension reads the address of
that page or link and hands it to the Riplox application on the same computer.

That address is not stored, not logged, and not transmitted anywhere else.

## What it cannot see

**When you install it, the extension holds no access to any website at all.**
Website access is declared as *optional*, which means installing grants none of
it. It also does not request the `tabs` permission: it uses `activeTab`, which
the browser grants for one tab, once, in answer to a click of yours — so it
cannot see your other tabs, your other windows, or your browsing history.

## The one thing that changes that, and only if you ask for it

There is a setting called **"Show a button on every page"**. It is off.

Turning it on is what makes the browser ask you for access to pages, because a
button that appears on a page has to be put there by something running on that
page. Untick it and that access is handed straight back. If you never turn it
on, nothing is ever injected into any page you visit.

What that button does when it is on is deliberately narrow: it draws itself,
and when you click it, it reads the address of the page it is on and passes
that to Riplox — the same address the toolbar button would send. **It does not
read the page.** It does not look at the text, the media, the forms or the
markup; it does not search the page for anything; and it sends nothing but the
address you clicked it on. You can drag it anywhere, and dismiss it on any site
with its cross, which is remembered.

## What it stores

Three things, in your browser's own extension storage, and nothing else:
whether to show the count of running downloads, whether the in-page button is
on, and — if you have used it — where you dragged that button to and the list
of sites where you dismissed it.

## Network

The extension makes no network requests of any kind. There is no server behind
it, no analytics, no error reporting and no update ping of its own.

The only channel it uses is the browser's native messaging, which is a
connection to a program on your own computer that the Riplox installer
registered. If that program is not available, the extension falls back to a
`riplox://` link, which your browser asks you to allow and which is likewise
handled entirely on your computer.

## What Riplox itself does

The Riplox application is separate software, and once an address reaches it,
what happens next is Riplox's business rather than this extension's. Riplox
runs on your computer and its downloads are yours.

## Children

The extension is not directed at children and collects no data from anyone,
including children.

## Changes

If this ever stops being accurate, this page changes before the extension does,
and the date at the top changes with it.

## Contact

Questions about this policy: open an issue on this repository.
