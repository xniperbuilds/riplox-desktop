"""Build the two PWA icons the relay's manifest needs, as base64 for worker.js.

Chrome only installs a page as a real app when its manifest names an icon, and
a share target only exists for an installed app - which is why the share sheet
never showed Riplox with "icons": [].

Paths are worked out from this file's own location, so it runs from a clone
anywhere.
"""
import base64
import io
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "build" / "brand" / "riplox_1024.png"
OUT = ROOT / "relay" / "icons.js"

HEAD = """\
/**
 * The app icons the manifest points at, base64 so the relay stays one deploy
 * with no bucket behind it.
 *
 * These are not decoration. Chrome installs a page as a real app only when its
 * manifest names an icon, and a share target exists only for an installed app -
 * so with "icons": [] the Riplox entry never appeared in Android's share sheet
 * at all. Built by build/make_icons.py from the 1024px brand master.
 */

"""


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"brand mark not found: {SRC}")

    master = Image.open(SRC).convert("RGBA")
    print("source", master.size)

    lines = []
    for size in (192, 512):
        canvas = Image.new("RGBA", (size, size), (7, 12, 21, 255))
        pad = int(size * 0.14)                       # maskable safe zone
        art = master.resize((size - pad * 2, size - pad * 2), Image.LANCZOS)
        canvas.paste(art, (pad, pad), art)

        buf = io.BytesIO()
        canvas.convert("RGB").save(buf, "PNG", optimize=True)
        raw = buf.getvalue()
        encoded = base64.b64encode(raw).decode("ascii")
        print(size, len(raw), "bytes ->", len(encoded), "b64 chars")
        lines.append('export const ICON_%d = "%s";\n' % (size, encoded))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="ascii", newline="\n") as fh:
        fh.write(HEAD + "\n".join(lines))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
