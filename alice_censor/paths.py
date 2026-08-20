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
