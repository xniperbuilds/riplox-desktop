"""
Riplox Send's launcher icon.

Same Riplox mark, with one difference added on purpose: a badge in the corner
carrying a send arrow. Someone who already has Riplox installed must not
confuse the two - so the family is still obvious at a glance, and which one
you are looking at is obvious too.

The badge is violet rather than Riplox's teal, has its own dark ring so it
reads as a separate object, and sits bottom-right where launchers do not crop.
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
# Riplox's own brand mark, one level up in the same repository.
MASTER = os.path.normpath(os.path.join(HERE, "..", "..", "build", "brand",
                                       "riplox_1024.png"))
OUT = os.path.join(HERE, "..", "res")

# mdpi, hdpi, xhdpi, xxhdpi, xxxhdpi
DENSITIES = [("mdpi", 48), ("hdpi", 72), ("xhdpi", 96),
             ("xxhdpi", 144), ("xxxhdpi", 192)]

VIOLET = (124, 92, 255, 255)
RING = (7, 12, 21, 255)
WHITE = (255, 255, 255, 255)


def badged(size: int) -> Image.Image:
    """The mark at `size`, with the send badge over its bottom-right corner."""
    icon = Image.open(MASTER).convert("RGBA").resize((size, size), Image.LANCZOS)

    # Drawn at 4x and shrunk, so the arrow's diagonal stays clean at 48px.
    scale = 4
    big = size * scale
    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    r = int(big * 0.21)                      # badge radius
    cx = cy = big - r - int(big * 0.045)
    ring = max(2, int(big * 0.018))

    draw.ellipse([cx - r - ring, cy - r - ring, cx + r + ring, cy + r + ring], fill=RING)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=VIOLET)

    # A paper-plane-ish arrow: a shaft going up-right, with a head.
    arm = int(r * 0.52)
    width = max(2, int(r * 0.20))
    draw.line([cx - arm, cy + arm, cx + arm, cy - arm], fill=WHITE,
              width=width, joint="curve")
    head = int(r * 0.62)
    draw.polygon(
        [(cx + arm + width // 2, cy - arm - width // 2),
         (cx + arm - head, cy - arm - width // 2),
         (cx + arm + width // 2, cy - arm + head)],
        fill=WHITE)

    layer = layer.resize((size, size), Image.LANCZOS)
    icon.alpha_composite(layer)
    return icon


def main() -> None:
    for name, size in DENSITIES:
        folder = os.path.join(OUT, "mipmap-" + name)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "ic_launcher.png")
        badged(size).save(path, "PNG", optimize=True)
        print("%-8s %3dpx  %5d bytes" % (name, size, os.path.getsize(path)))

    preview = os.path.join(OUT, "..", "build", "icon-preview.png")
    badged(512).save(preview, "PNG", optimize=True)
    print("preview  512px  %5d bytes" % os.path.getsize(preview))


if __name__ == "__main__":
    main()
