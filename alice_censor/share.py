"""Packaging a project so someone else can use it.

Called a "shared project" in the interface. "Bundle" is the term used
here for the zip itself, to keep it distinct from the ProjectState it
is built from.

A project file holds the part that took the time, meaning which images
were reviewed, what they were marked as, and every censor region drawn on
them. It does not hold the images, so it is small and worth passing around.

Two things stop a project file being shareable on its own.

Its paths are absolute and point at one machine. Every one of them,
including where alice.exe lives and where the archive sits, so opening
someone else's project on your own disk finds nothing.

Its overlay layers name stickers that live in that machine's sticker
library. Without the sticker files those layers cannot render at all, and
on a real project that is most of the work. In one Rance 03 project, 668
of 771 layers were overlays.

A bundle is a zip holding the project with its paths stripped, plus the
stickers those layers actually reference. Applying it needs a project of
your own, made from the same archive, which supplies the paths.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .project import ImageRecord, ImageStatus, ProjectState

PROJECT_ENTRY = "project.acproj.json"
STICKER_DIR = "stickers"

# Paths belong to whoever made the bundle, never to whoever opens it.
_LOCAL_PATH_FIELDS = (
    "alice_exe_path",
    "archive_path",
    "extract_dir",
    "output_dir",
    "sticker_dir",
    "manifest_path",
)


class BundleError(ValueError):
    """Raised when a bundle cannot be read or applied."""


@dataclass
class Bundle:
    """A shared project, read but not yet applied."""

    images: dict[str, ImageRecord] = field(default_factory=dict)
    archive_name: str = ""  # for a sanity check against the target project
    archive_format: str = ""
    stickers: list[str] = field(default_factory=list)
    schema_version: int = 0

    @property
    def edited_count(self) -> int:
        return sum(1 for rec in self.images.values() if rec.layers)

    @property
    def layer_count(self) -> int:
        return sum(len(rec.layers) for rec in self.images.values())


def referenced_stickers(project: ProjectState) -> list[str]:
    """Sticker names the project's overlay layers actually use.

    Only these go in a bundle. A sticker library accumulates whatever was
    tried and discarded, and there is no reason to ship the rejects.
    Absolute references are skipped, since they came from before the
    library existed and name a file on one machine.
    """
    names = set()
    for record in project.images.values():
        for layer in record.layers:
            ref = layer.params.get("sticker")
            if ref and not Path(ref).is_absolute():
                names.add(ref)
    return sorted(names)


def export_bundle(project: ProjectState, dest: str | Path) -> list[str]:
    """Write a shareable zip. Returns the sticker names included.

    Stickers a layer names but which are missing from the library are left
    out rather than failing the export, so a project with one broken
    reference still shares. apply_bundle reports them at the other end.
    """
    dest = Path(dest)
    data = project.to_dict()
    for key in _LOCAL_PATH_FIELDS:
        data.pop(key, None)
    # Keep the archive's bare name. It is not a path and reveals nothing
    # about the sender's disk, and it is the only way the other end can
    # notice a bundle built from a different archive.
    data["archive_name"] = Path(project.archive_path).name

    sticker_dir = Path(project.sticker_dir) if project.sticker_dir else None
    included: list[str] = []
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(PROJECT_ENTRY, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        for name in referenced_stickers(project):
            if sticker_dir is None:
                break
            source = sticker_dir / name
            if source.is_file():
                zf.write(source, f"{STICKER_DIR}/{name}")
                included.append(name)
    return included


def read_bundle(path: str | Path) -> Bundle:
    """Parse a bundle without touching any project."""
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if PROJECT_ENTRY not in names:
                raise BundleError(f"{path.name} is not an Alice Censor bundle, it has no project in it")
            data = json.loads(zf.read(PROJECT_ENTRY).decode("utf-8"))
            stickers = sorted(
                n[len(STICKER_DIR) + 1 :] for n in names
                if n.startswith(f"{STICKER_DIR}/") and not n.endswith("/")
            )
    except zipfile.BadZipFile as e:
        raise BundleError(f"{path.name} is not a readable zip: {e}") from e

    return Bundle(
        images={p: ImageRecord.from_dict(r) for p, r in data.get("images", {}).items()},
        archive_name=data.get("archive_name", ""),
        archive_format=data.get("archive_format", ""),
        stickers=stickers,
        schema_version=data.get("schema_version", 0),
    )


@dataclass
class ApplyResult:
    applied: list[str] = field(default_factory=list)  # paths that took layers or status
    unmatched: list[str] = field(default_factory=list)  # in the bundle, not in this project
    missing_stickers: list[str] = field(default_factory=list)  # named by a layer, not in the zip
    stickers_copied: list[str] = field(default_factory=list)


def apply_bundle(
    bundle_path: str | Path, project: ProjectState, *, overwrite: bool = True
) -> ApplyResult:
    """Copy a bundle's review work onto `project`, matching by image path.

    The images themselves are never touched. What transfers is status and
    layers, keyed by the path each image has inside the archive, which is
    why both sides must come from the same archive.

    Stickers are unpacked into this project's own library so the overlay
    layers resolve locally. A name already in the library is left alone
    rather than overwritten, since it may be a different picture the user
    picked deliberately.
    """
    bundle = read_bundle(bundle_path)
    result = ApplyResult()

    sticker_dir = Path(project.sticker_dir) if project.sticker_dir else None
    if sticker_dir is not None and bundle.stickers:
        sticker_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(bundle_path) as zf:
            for name in bundle.stickers:
                target = sticker_dir / name
                if target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(f"{STICKER_DIR}/{name}"))
                result.stickers_copied.append(name)

    available = set(bundle.stickers)
    for path, incoming in bundle.images.items():
        if not incoming.layers and incoming.status == ImageStatus.UNREVIEWED:
            continue  # carries nothing, so its absence is not worth reporting
        if path not in project.images:
            result.unmatched.append(path)
            continue
        target = project.images[path]
        if target.layers and not overwrite:
            continue
        target.status = incoming.status
        target.layers = list(incoming.layers)
        target.notes = incoming.notes or target.notes
        result.applied.append(path)
        for layer in incoming.layers:
            ref = layer.params.get("sticker")
            if ref and not Path(ref).is_absolute() and ref not in available:
                if ref not in result.missing_stickers:
                    result.missing_stickers.append(ref)

    result.unmatched.sort()
    result.missing_stickers.sort()
    return result
