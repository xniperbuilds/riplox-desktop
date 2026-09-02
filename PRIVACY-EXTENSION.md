# Privacy policy — Riplox browser extension

*Last updated: 2 September 2026*

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

The extension holds **no access to any website**. It does not request host
permissions, and it does not include a content script, so it cannot read the
contents of any page you visit.

It also does not request the `tabs` permission. It uses `activeTab`, which the
browser grants for one tab, once, in answer to a click of yours — so it cannot
see your other tabs, your other windows, or your browsing history.

## What it stores

One setting, in your browser's own extension storage: whether you want the
count of running downloads shown on the toolbar icon. Nothing else.

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
