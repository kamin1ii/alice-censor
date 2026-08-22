"""Finding a font to draw text layers with.

Fonts are taken from the machine rather than bundled, which keeps the
download small and avoids shipping someone else's licence. Every family
below lists several files because the same typeface is named differently
across Windows versions, and because a project shared with someone else has
to land on a font they actually have.

Japanese first in every list. These are Japanese games, so a caption is as
likely to be Japanese as English, and a font that cannot draw it renders
empty boxes rather than failing in any way the user can see.
"""

from __future__ import annotations

from functools import lru_cache

from PIL import ImageFont

# Family name as the user sees it, then the files to try in order.
FAMILIES: dict[str, tuple[str, ...]] = {
    "Gothic": ("meiryo.ttc", "YuGothR.ttc", "YuGothM.ttc", "msgothic.ttc",
               "segoeui.ttf", "arial.ttf"),
    "Mincho": ("msmincho.ttc", "YuMincho-Regular.ttf", "times.ttf", "georgia.ttf"),
    "Sans": ("segoeui.ttf", "arial.ttf", "meiryo.ttc", "msgothic.ttc"),
}

DEFAULT_FAMILY = "Gothic"

# Below this a font stops being legible and Pillow starts refusing sizes.
MIN_SIZE = 6


@lru_cache(maxsize=1)
def available() -> tuple[str, ...]:
    """The families that can actually be loaded on this machine."""
    return tuple(name for name in FAMILIES if _first_present(name) is not None)


def load(family: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """A font of roughly `size` pixels, falling back rather than failing.

    An unknown family, or one whose files are all missing, falls back to
    the default family and then to whatever Pillow has built in. Text drawn
    in the wrong face is a smaller problem than a render that stops.
    """
    return _load(family if family in FAMILIES else DEFAULT_FAMILY, max(MIN_SIZE, int(size)))


@lru_cache(maxsize=64)
def _load(family: str, size: int):
    for candidate in (family, DEFAULT_FAMILY):
        path = _first_present(candidate)
        if path is not None:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


@lru_cache(maxsize=8)
def _first_present(family: str) -> str | None:
    for filename in FAMILIES.get(family, ()):
        try:
            ImageFont.truetype(filename, 12)
        except OSError:
            continue
        return filename
    return None
