"""Disk-persisted thumbnail cache.

Games extract to 1000+ images; regenerating every thumbnail from the
full-size PNGs on every app launch would make the gallery slow to open.
Thumbnails are cached as small PNGs keyed by a hash of the source path
*and* the current censor layers, and regenerated only if the source file's
mtime moves past the cached thumbnail's mtime (covers edits and
re-extraction after a game update). Layer edits don't touch the source
file's mtime at all (by design, so the editor keeps rendering fresh from a
pristine original), so the layers themselves are folded into the cache key
instead, so any change to them naturally produces a different key rather than
silently reusing a stale (pre-edit) thumbnail.

Cached as PNG rather than JPEG specifically to preserve alpha. An earlier version
flattened transparency onto a solid white background to fix stray-black
artifacts (Image.convert("RGB") alone just drops alpha, keeping whatever
garbage RGB values sat underneath), but that just traded one bug for
another, since an image with a transparent background and *white* text on
it (e.g. a stylized location-name overlay) became illegible white-on-white.
There's no single flatten color that's correct for both dark- and
light-colored content, so this keeps the real alpha channel and lets Qt
composite it against the gallery's own (dark) background instead of
guessing.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from ..project import CensorLayer
from ..rendering import RenderError, render_layers

THUMBNAIL_SIZE = (192, 192)


def _layers_signature(layers: list[CensorLayer]) -> str:
    """Fingerprint the layers a thumbnail was rendered with, so any change
    to them lands on a different cache key. Empty for no layers, which is
    what keys an uncensored thumbnail.
    """
    if not layers:
        return ""
    payload = [
        {"type": layer.type.value, "rect": list(layer.rect), "params": layer.params}
        for layer in layers
    ]
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


class ThumbnailCache:
    def __init__(self, cache_dir: str | Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, source_path: Path, layers_signature: str) -> Path:
        key = hashlib.sha1(f"{source_path}|{layers_signature}".encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.png"

    def get_or_create(
        self,
        source_path: str | Path,
        *,
        layers: list[CensorLayer] | None = None,
        sticker_resolver=None,
    ) -> Path | None:
        """Return a path to a cached thumbnail for `source_path`, creating
        it if missing or stale. If `layers` has any enabled entries, the
        thumbnail is rendered from the *censored* image (via the same
        rendering.render_layers path the editor preview and export use)
        rather than the raw source, so the grid actually shows what got
        censored instead of just a "has edits" badge. Returns None if the
        source doesn't exist or can't be decoded as an image.
        """
        source_path = Path(source_path)
        if not source_path.exists():
            return None

        # Filter once, here. Disabled layers change neither the render nor
        # the cache key, so everything downstream can treat this list as
        # "the layers this thumbnail is of".
        enabled = [layer for layer in (layers or []) if layer.enabled]
        layers_signature = _layers_signature(enabled)
        cache_path = self._cache_path(source_path, layers_signature)
        try:
            if cache_path.exists() and cache_path.stat().st_mtime >= source_path.stat().st_mtime:
                return cache_path
        except OSError:
            pass

        try:
            with Image.open(source_path) as im:
                if enabled:
                    im = render_layers(im, enabled, sticker_resolver=sticker_resolver)
                im.thumbnail(THUMBNAIL_SIZE, Image.LANCZOS)
                im.save(cache_path, "PNG")
        except (OSError, UnidentifiedImageError, RenderError):
            return None
        return cache_path
