# -*- coding: utf-8 -*-
"""Build the zip that gets uploaded to the Chrome Web Store.

Named rather than swept. A zip built by "everything in this folder" would carry
LISTING.md, the screenshot sources and this script itself into the review - and
a package containing files the extension does not use is a question nobody
wants asked during a review that already takes days.
"""
import pathlib
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "dist" / "riplox-extension.zip"

SHIP = [
    "manifest.json",
    "background.js",
    "popup.html",
    "popup.css",
    "popup.js",
    "icons/icon16.png",
    "icons/icon32.png",
    "icons/icon48.png",
    "icons/icon128.png",
]

missing = [name for name in SHIP if not (HERE / name).is_file()]
if missing:
    raise SystemExit("missing, nothing written: " + ", ".join(missing))

OUT.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
    for name in SHIP:
        zf.write(HERE / name, name)

print("wrote", OUT)
for name in SHIP:
    print("  %-22s %6d b" % (name, (HERE / name).stat().st_size))
print("total %d b" % OUT.stat().st_size)
