"""Regenerate the app icon from a source image.

    python make_icon.py "C:\\path\\to\\artwork.png"

Writes alice_censor/assets/icon.ico (the one Windows and Qt actually use)
and icon.png (a 512px master). Rebuild the exe afterwards to pick it up.

Two details that are easy to get wrong by hand and are why this exists.

An .ico frame has to be square, so a source that is not gets padded onto a
transparent canvas rather than squashed, which would distort it, or
cropped, which would cut parts off.

Pillow silently drops any requested size larger than the image it is
saving from, so saving from a small frame yields a single-frame .ico that
looks fine in a file listing and blurry everywhere else. Saving from the
largest frame avoids that.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ASSETS = Path(__file__).resolve().parent / "alice_censor" / "assets"

# Windows picks a frame per context. 16 for the title bar and small
# listings, 32 for the taskbar and alt-tab, 256 for the largest Explorer
# view. 256 is the maximum the format supports.
SIZES = [16, 24, 32, 48, 64, 128, 256]
MASTER_PNG_SIZE = 512


def square_canvas(image: Image.Image) -> Image.Image:
    """Centre `image` on a transparent square, preserving aspect ratio."""
    side = max(image.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(image, ((side - image.width) // 2, (side - image.height) // 2), image)
    return canvas


def build(source: Path) -> None:
    src = Image.open(source).convert("RGBA")
    square = square_canvas(src)

    master = square.resize((256, 256), Image.LANCZOS)
    master.save(ASSETS / "icon.ico", format="ICO", sizes=[(s, s) for s in SIZES])
    square.resize((MASTER_PNG_SIZE, MASTER_PNG_SIZE), Image.LANCZOS).save(
        ASSETS / "icon.png", "PNG"
    )

    written = sorted(Image.open(ASSETS / "icon.ico").info["sizes"])
    print(f"source {src.size} squared to {square.size}")
    print(f"wrote {ASSETS / 'icon.ico'} with frames {[w for w, _ in written]}")
    print(f"wrote {ASSETS / 'icon.png'} at {MASTER_PNG_SIZE}px")
    if len(written) != len(SIZES):
        raise SystemExit(f"expected {len(SIZES)} frames, got {len(written)}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    path = Path(sys.argv[1])
    if not path.is_file():
        raise SystemExit(f"no such file: {path}")
    build(path)
