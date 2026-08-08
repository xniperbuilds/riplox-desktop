"""
Build the Windows icon from the original Riplox brand mark.

The source is the same 1024px launcher artwork the mobile apps use, so the
desktop app is visibly the same product rather than a lookalike.
"""

import shutil
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "src" / "static" / "img"
ICO = IMG / "riplox.ico"
PNG = IMG / "riplox.png"

SOURCE = ROOT / "build" / "brand" / "riplox_1024.png"

SIZES = [16, 24, 32, 48, 64, 128, 256]


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"brand mark not found: {SOURCE}")

    IMG.mkdir(parents=True, exist_ok=True)
    master = Image.open(SOURCE).convert("RGBA")

    # In-app mark: 128px is plenty for a 32px header slot on any DPI.
    master.resize((128, 128), Image.LANCZOS).save(PNG, format="PNG")

    # Windows icon: every size resampled from the master, not from each other.
    frames = [master.resize((n, n), Image.LANCZOS) for n in SIZES]
    frames[-1].save(ICO, format="ICO", sizes=[(n, n) for n in SIZES])

    print("wrote", ICO)
    print("wrote", PNG)


if __name__ == "__main__":
    main()
