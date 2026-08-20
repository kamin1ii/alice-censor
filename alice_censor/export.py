"""Renders per-image censor layers to a dedicated output folder and builds
a manifest that points `ar pack` at it, so repacking actually reflects
edits made in the region editor.

Deliberately does NOT touch the original extraction folder. Those files
stay pristine so the editor keeps rendering fresh from the original every
time. Baking edits into the extracted PNGs directly would make repeated
edits compound on top of each other (e.g. blur-on-top-of-already-blurred)
instead of cleanly replacing the previous render, the opposite of the
non-destructive design layers are meant to have.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .manifest import Manifest, write_manifest
from .paths import resolve_fs_path
from .project import ProjectState
from .rendering import RenderError, render_layers

EXPORT_MANIFEST_NAME = "manifest.txt"
EXPORT_CACHE_DIRNAME = "alice-tools-cache"


@dataclass
class ExportResult:
    rendered_paths: list[str] = field(default_factory=list)  # had enabled layers, re-rendered
    copied_paths: list[str] = field(default_factory=list)  # no layers, copied through as-is
    # Subset of copied_paths whose original bytes were seeded into the
    # pack cache, so they go into the archive exactly as they came out
    # rather than being decoded and re-encoded.
    preserved_paths: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)  # path -> error message
    manifest_path: Path | None = None


def render_export(
    project: ProjectState,
    manifest: Manifest,
    *,
    sticker_resolver=None,
    on_progress=None,
) -> ExportResult:
    """Render every image with enabled layers into project.output_dir,
    copy every other listed file through unchanged, and write a manifest
    there pointing at it. Returns paths/errors for the caller to report;
    does not raise on a per-image failure (one bad image shouldn't block
    exporting everything else), so check `result.errors`.
    """
    src_dir = manifest.resolved_src_dir()
    # Raw original bytes for every entry, as dumped by `ar extract --cache`
    # (ar_extract.c calls ar_extract_all with AR_RAW for this directory).
    # Seeding these into the export cache is what lets untouched files be
    # packed exactly as they came out, see _seed_cache_entry.
    raw_cache_dir = manifest.resolved_cache_dir()
    # Resolve to absolute regardless of what's stored in project.output_dir.
    # The export manifest written below embeds this as --src-dir, and (as
    # with AliceTools.extract(), see its docstring) a relative value
    # there gets re-resolved against the *manifest's own* directory on
    # reparse, silently doubling the path if this wasn't already absolute.
    out_dir = Path(project.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = out_dir / EXPORT_CACHE_DIRNAME
    # alice-tools' own cache-write path (pack.c:alicepack_to_file_list)
    # doesn't create this directory itself the way it does for regular
    # converted output. Confirmed against the source, a missing cache
    # dir there just produces a harmless-but-noisy WARNING per file (the
    # actual PNG->QNT conversion that gets packed happens in memory
    # regardless, so this never affected correctness, only made repack
    # output confusing and skipped the caching speedup).
    cache_dir.mkdir(parents=True, exist_ok=True)

    result = ExportResult()
    for entry in manifest.entries:
        path = entry.path
        if on_progress:
            on_progress(path)

        src_file = resolve_fs_path(src_dir, path)
        dst_file = resolve_fs_path(out_dir, path)
        dst_file.parent.mkdir(parents=True, exist_ok=True)

        if not src_file.exists():
            result.errors[path] = f"source file missing: {src_file}"
            continue

        record = project.images.get(path)
        layers = [layer for layer in record.layers if layer.enabled] if record else []

        cache_file = _cache_path(cache_dir, entry)

        if not layers:
            try:
                if not dst_file.exists() or dst_file.stat().st_mtime < src_file.stat().st_mtime:
                    shutil.copy2(src_file, dst_file)
                if _seed_cache_entry(raw_cache_dir, cache_file, dst_file):
                    result.preserved_paths.append(path)
            except OSError as e:
                result.errors[path] = str(e)
                continue
            result.copied_paths.append(path)
            continue

        try:
            with Image.open(src_file) as opened:
                base = opened.copy()
                base.load()
            rendered = render_layers(base, layers, sticker_resolver=sticker_resolver)
            rendered.save(dst_file, "PNG")
            # Delete any cached original for this entry. A cache file that
            # is newer than its source is packed verbatim, so leaving one
            # here would silently drop the edit from the archive. This is
            # the single most important line in the function.
            cache_file.unlink(missing_ok=True)
        except (OSError, UnidentifiedImageError, RenderError) as e:
            result.errors[path] = str(e)
            continue
        result.rendered_paths.append(path)

    manifest_path = out_dir / EXPORT_MANIFEST_NAME
    write_manifest(
        manifest,
        manifest_path,
        src_dir=out_dir,
        cache_dir=cache_dir,
    )
    result.manifest_path = manifest_path
    return result


def _cache_path(cache_dir: Path, entry) -> Path:
    """Where `ar pack` looks for a cached conversion of this row.

    manifest_parser.c builds it as the cache directory joined with the
    row's own path, with the extension swapped for the destination
    format's. A row with no destination format is packed straight off
    disk and never consults the cache.
    """
    if not entry.dst_format:
        return cache_dir / entry.path
    stem = entry.path.rsplit(".", 1)[0] if "." in entry.path else entry.path
    return resolve_fs_path(cache_dir, f"{stem}.{entry.dst_format.lower()}")


def _seed_cache_entry(raw_cache_dir: Path | None, cache_file: Path, source_file: Path) -> bool:
    """Put an untouched entry's original bytes where `ar pack` will find
    them, so it packs those bytes rather than re-encoding the PNG.

    pack.c takes the cache only when it is strictly newer than the source
    in whole seconds, so the timestamp is set forward deliberately rather
    than left to chance. Returns whether the entry will be preserved.

    Seeding is skipped when the original is not already in the format the
    row packs to. That happens for AJP, which alice-tools cannot encode
    and deliberately rewrites to QNT, and the raw bytes would be wrong
    under the name the row now carries.
    """
    if raw_cache_dir is None:
        return False
    raw_file = resolve_fs_path(raw_cache_dir, cache_file.name)
    if not raw_file.exists():
        return False
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(raw_file, cache_file)
    stamp = max(time.time(), source_file.stat().st_mtime + 2)
    os.utime(cache_file, (stamp, stamp))
    return True
