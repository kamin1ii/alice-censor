"""Sticker library, a small managed folder of overlay images the
image-overlay layer type can pick from via a thumbnail picker.

An overlay layer's `params["sticker"]` value can be EITHER a bare filename
relative to the project's sticker_dir (stickers picked from the library)
OR an absolute path (backward compatible with layers created before this
existed, which stored whatever path the file browser returned directly).
make_sticker_resolver handles both transparently, so nothing needs
migrating.
"""

from __future__ import annotations

import shutil
from pathlib import Path

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def make_sticker_resolver(sticker_dir: str | Path | None):
    """Returns a callable(ref) -> Path for rendering.render_layers'
    sticker_resolver parameter. An already-absolute ref is used as-is;
    otherwise it's resolved relative to sticker_dir."""
    base = Path(sticker_dir) if sticker_dir else None

    def resolver(ref: str) -> Path:
        candidate = Path(ref)
        if candidate.is_absolute() or base is None:
            return candidate
        return base / ref

    return resolver


def list_stickers(sticker_dir: str | Path) -> list[str]:
    """Every image filename in the sticker library, sorted. Empty (not an
    error) if the library folder doesn't exist yet, since it's created
    lazily on the first add_sticker() call."""
    base = Path(sticker_dir)
    if not base.exists():
        return []
    return sorted(
        p.name for p in base.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


def add_sticker(sticker_dir: str | Path, source_path: str | Path) -> str:
    """Copy `source_path` into the sticker library (creating the folder if
    needed), returning the filename it was stored under. A name collision
    with an existing library file gets a numeric suffix rather than
    silently overwriting it."""
    base = Path(sticker_dir)
    base.mkdir(parents=True, exist_ok=True)
    source_path = Path(source_path)

    dest = base / source_path.name
    counter = 1
    while dest.exists():
        dest = base / f"{source_path.stem}_{counter}{source_path.suffix}"
        counter += 1

    shutil.copy2(source_path, dest)
    return dest.name


def remove_sticker(sticker_dir: str | Path, filename: str) -> None:
    """Delete a sticker from the library. No-op if it's already gone."""
    path = Path(sticker_dir) / filename
    if path.exists():
        path.unlink()
