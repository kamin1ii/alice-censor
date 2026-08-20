"""Reconciles a manifest against a project's saved state and the files
actually on disk, so re-opening a project (or re-running after a game
update) only surfaces what's new or changed rather than requiring a full
re-review.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .grouping import GroupInfo, compute_groups
from .manifest import Manifest
from .paths import resolve_fs_path
from .project import ProjectState


@dataclass
class ScanResult:
    new_paths: list[str] = field(default_factory=list)
    changed_paths: list[str] = field(default_factory=list)
    missing_paths: list[str] = field(default_factory=list)
    groups: dict[str, GroupInfo] = field(default_factory=dict)


def scan_and_sync(
    project: ProjectState, manifest: Manifest, *, ald_gap_threshold: int = 1
) -> ScanResult:
    """Bring `project.images` up to date with `manifest` and the extracted
    files on disk. Mutates `project` in place (new entries added, size/mtime
    refreshed) but does not save it. Callers should save afterward.

    A path is "changed" if its size or mtime on disk differs from what was
    recorded the last time it was scanned (never for a brand-new path,
    which is reported via `new_paths` instead).
    """
    groups = compute_groups(manifest, ald_gap_threshold=ald_gap_threshold)
    group_keys = {member: g.key for g in groups.values() for member in g.members}

    current_paths = manifest.paths()
    new_paths, missing_paths = project.sync_with_paths(current_paths, group_keys)
    new_path_set = set(new_paths)

    src_dir = manifest.resolved_src_dir()
    changed_paths: list[str] = []
    for path in current_paths:
        record = project.images[path]
        fs_path = resolve_fs_path(src_dir, path)
        try:
            stat = fs_path.stat()
        except OSError:
            continue

        if path not in new_path_set and record.size is not None and record.mtime is not None:
            if record.size != stat.st_size or record.mtime != stat.st_mtime:
                changed_paths.append(path)

        record.size = stat.st_size
        record.mtime = stat.st_mtime

    return ScanResult(
        new_paths=new_paths, changed_paths=changed_paths, missing_paths=missing_paths, groups=groups
    )
