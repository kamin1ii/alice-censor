"""Path helpers for archive-internal names.

AliceSoft archive entries are stored as a single opaque string per file,
using the fullwidth solidus '／' (U+FF0F) as a visual, "directory-like"
separator (e.g. an "event" folder, a character subfolder). Confirmed against
a real extraction, alice-tools does NOT create real nested subdirectories
for this. Every file lands flat, directly inside the output directory,
with the entire manifest path string (every '／' included) as one literal
filename. Ordinary '/' or '\\' are also accepted as equivalent separators
here for robustness, but in practice only '／' has been observed.

Two different needs follow from this, and must not be conflated.
- Grouping (grouping.py) wants a directory-ish prefix split out of the
  name, treating '／'/'/'/'\\' as equivalent. See split_dir_and_stem.
- Locating the actual file on disk must NOT split on '／' at all, since
  there's no real subdirectory to descend into. See resolve_fs_path.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

SEPARATORS = ("/", "\\", "／")


def normalize_separators(path: str) -> str:
    """Replace all recognized separator characters with a plain '/'."""
    out = path
    for sep in SEPARATORS[1:]:
        out = out.replace(sep, "/")
    return out


def split_dir_and_stem(path: str) -> tuple[str, str, str]:
    """Split a manifest path into (dir_path, stem, extension).

    `dir_path` uses '/' as the separator regardless of what the source used.
    `extension` includes the leading dot and is lowercased; it is empty if
    there is none.
    """
    normalized = normalize_separators(path)
    pure = PurePosixPath(normalized)
    dir_path = str(pure.parent) if str(pure.parent) != "." else ""
    stem = pure.stem
    ext = pure.suffix.lower()
    return dir_path, stem, ext


def basename(path: str) -> str:
    return PurePosixPath(normalize_separators(path)).name


def resolve_fs_path(src_dir: Path, manifest_path: str) -> Path:
    """Locate a manifest entry's file on disk. See the module docstring.
    This is a plain join, deliberately *not* going through
    split_dir_and_stem or normalize_separators."""
    return src_dir / manifest_path


# Runs of digits, captured so re.split marks them at odd indices.
_DIGIT_RUN_RE = re.compile(r"(\d+)")


def natural_sort_key(path: str) -> tuple:
    r"""Sort key that orders embedded numbers by value, not by digit.

    Archives list files in whatever order they were packed, which puts a
    scene's frames in no useful sequence at all. Sorting by name fixes
    that, but plain text ordering breaks as soon as the numbering is not
    zero padded to a fixed width, putting 幅９０ after 幅１２８ because the
    character 9 is greater than 1.

    Fullwidth digits come out right without special handling. `\d` matches
    any Unicode decimal, and int() reads them, so ０１ and 01 both become 1.

    A digit run is identified by its position rather than by isdigit(),
    which is true for characters like ² that int() then refuses.
    """
    normalized = normalize_separators(path)
    key: list[tuple[int, int, str]] = []
    for i, part in enumerate(_DIGIT_RUN_RE.split(normalized)):
        if not part:
            continue
        # Uniform tuple shape so no comparison ever comes down to int vs str.
        key.append((1, int(part), "") if i % 2 else (0, 0, part))
    # Numbers compare by value, so a1 and a01 tie. Break it on the original
    # text, otherwise two distinct files order by luck of the input.
    key.append((2, 0, normalized))
    return tuple(key)
