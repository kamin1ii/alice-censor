"""Qt model backing the thumbnail gallery.

Virtualization comes for free from QAbstractListModel plus QListView. Qt only
calls data() for rows that are actually visible (plus a small buffer), so
thumbnails are only requested for what's on screen. Filtering rebuilds a
row-index list (`_filtered`) rather than the whole dataset, so switching
filters is cheap even with 1000+ entries.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QRect,
    QSize,
    QThreadPool,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

from ..grouping import GroupInfo
from ..manifest import Manifest
from ..paths import basename, resolve_fs_path, split_dir_and_stem
from ..project import ImageRecord, ImageStatus, ProjectState
from .thumbnail_cache import THUMBNAIL_SIZE, ThumbnailCache
from .thumbnail_worker import ThumbnailSignals, ThumbnailTask

PathRole = Qt.UserRole + 1
StatusRole = Qt.UserRole + 2
GroupRole = Qt.UserRole + 3
FolderRole = Qt.UserRole + 4
HasLayersRole = Qt.UserRole + 5

# Status -> (border/badge color, badge glyph). Unreviewed gets neither, so
# the default look stays neutral and reviewed items visibly stand out.
_STATUS_DECORATION: dict[ImageStatus, tuple[QColor, str]] = {
    ImageStatus.FLAGGED: (QColor(0xE5, 0x39, 0x35), "⚑"),      # red, flag
    ImageStatus.CLEAN: (QColor(0x43, 0xA0, 0x47), "✓"),        # green, check
    ImageStatus.NEEDS_EDIT: (QColor(0xFB, 0x8C, 0x00), "✎"),   # orange, pencil
}
_BORDER_WIDTH = 5
_BADGE_SIZE = 22

# "Has censor layers" is orthogonal to status (e.g. an image can be
# FLAGGED with no layers drawn yet, or CLEAN with layers left over from
# before you decided it didn't need them). Shown as a separate
# bottom-left badge so it's visible independent of status, without going
# to the editor or another tab to check.
_HAS_LAYERS_COLOR = QColor(0x5E, 0x35, 0xB1)  # purple, distinct from all status colors
_HAS_LAYERS_GLYPH = "▤"

# Stand-in for a path the project has no record for yet, which happens
# between a manifest listing a file and the scan that files it away. It is
# read-only by convention. Nothing hands it out past this module, and
# every field already carries the right answer for an unreviewed image.
_MISSING_RECORD = ImageRecord()


class GalleryModel(QAbstractListModel):
    project_dirty = Signal()

    def __init__(
        self,
        project: ProjectState,
        manifest: Manifest,
        groups: dict[str, GroupInfo],
        thumbnail_cache_dir: str | Path,
        parent: QObject | None = None,
        sticker_resolver=None,
    ):
        super().__init__(parent)
        self._project = project
        self._sticker_resolver = sticker_resolver
        self._src_dir = manifest.resolved_src_dir()
        self._path_to_group: dict[str, str] = {
            member: g.key for g in groups.values() for member in g.members
        }
        self._all_paths: list[str] = manifest.paths()
        self._filtered: list[str] = list(self._all_paths)
        self._row_of: dict[str, int] = {p: i for i, p in enumerate(self._filtered)}

        self._pixmaps: dict[str, QPixmap] = {}
        self._pending: set[str] = set()
        self._placeholder = QPixmap(*THUMBNAIL_SIZE)
        self._placeholder.fill(Qt.gray)

        self._thumb_cache = ThumbnailCache(thumbnail_cache_dir)
        # A pool of this model's own rather than the global one, so that
        # shutting this gallery down can drop its queued work without
        # touching anything else that happens to share the process.
        self._pool = QThreadPool()
        self._cancelled = threading.Event()
        self._signals = ThumbnailSignals()
        self._signals.ready.connect(self._on_thumbnail_ready)
        self._signals.failed.connect(self._on_thumbnail_failed)

        self._filter_folder: str | None = None
        self._filter_status: ImageStatus | None = None
        self._filter_group_substr: str = ""
        self._filter_search: str = ""
        self._filter_has_edits: bool | None = None

    # ===== Qt model interface

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._filtered)

    def _record(self, path: str) -> ImageRecord:
        """The image's record, or a blank stand-in if the project has no
        entry for it yet. A path with no record and a path with a default
        record mean the same thing everywhere in this model, so answering
        with a default rather than None keeps every reader below from
        repeating the same fallback.
        """
        return self._project.images.get(path) or _MISSING_RECORD

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        path = self._filtered[index.row()]

        if role == Qt.DisplayRole:
            return basename(path)
        if role == Qt.DecorationRole:
            pixmap = self._pixmaps.get(path)
            if pixmap is None:
                self._request_thumbnail(path)
                pixmap = self._placeholder
            record = self._record(path)
            return self._decorated_pixmap(pixmap, record.status, bool(record.layers))
        if role == Qt.ToolTipRole:
            record = self._record(path)
            group = self._path_to_group.get(path, "")
            return (
                f"{path}\nstatus: {record.status.value}\n"
                f"group: {group}\nlayers: {len(record.layers)}"
            )
        if role == PathRole:
            return path
        if role == StatusRole:
            return self._record(path).status
        if role == GroupRole:
            return self._path_to_group.get(path, "")
        if role == FolderRole:
            return split_dir_and_stem(path)[0]
        if role == HasLayersRole:
            return bool(self._record(path).layers)
        return None

    # ===== status decoration

    @staticmethod
    def _decorated_pixmap(
        pixmap: QPixmap, status: ImageStatus | None, has_layers: bool = False
    ) -> QPixmap:
        """Draw a colored border + top-right status badge, and/or a
        bottom-left "has censor layers" badge, over `pixmap`. Both are
        visible directly in the grid. Without this, status and edits were
        only visible via tooltip or by switching tabs, which made it easy
        to miss whether an edit actually took."""
        status_decoration = _STATUS_DECORATION.get(status) if status is not None else None
        if status_decoration is None and not has_layers:
            return pixmap

        decorated = QPixmap(pixmap.size())
        decorated.fill(Qt.transparent)
        painter = QPainter(decorated)
        painter.drawPixmap(0, 0, pixmap)

        if status_decoration is not None:
            color, glyph = status_decoration
            pen = QPen(color)
            pen.setWidth(_BORDER_WIDTH)
            painter.setPen(pen)
            half = _BORDER_WIDTH // 2
            painter.drawRect(decorated.rect().adjusted(half, half, -half - 1, -half - 1))

            badge_rect = QRect(
                decorated.width() - _BADGE_SIZE - _BORDER_WIDTH,
                _BORDER_WIDTH,
                _BADGE_SIZE,
                _BADGE_SIZE,
            )
            painter.setBrush(color)
            painter.setPen(Qt.white)
            painter.drawEllipse(badge_rect)
            painter.drawText(badge_rect, Qt.AlignCenter, glyph)

        if has_layers:
            badge_rect = QRect(
                _BORDER_WIDTH,
                decorated.height() - _BADGE_SIZE - _BORDER_WIDTH,
                _BADGE_SIZE,
                _BADGE_SIZE,
            )
            painter.setBrush(_HAS_LAYERS_COLOR)
            painter.setPen(Qt.white)
            painter.drawEllipse(badge_rect)
            painter.drawText(badge_rect, Qt.AlignCenter, _HAS_LAYERS_GLYPH)

        painter.end()
        return decorated

    def notify_layers_changed(self, path: str) -> None:
        """Call after an image's layers change outside the model (e.g. the
        region editor saved). Evicts the cached thumbnail pixmap, which was
        rendered from the *previous* layer state, and a layer edit never
        touches the source file's mtime (by design), so nothing else would
        notice it's stale, then re-requests a fresh one so the grid
        actually shows the censored result instead of just updating the
        badge/border over a stale image."""
        self._pixmaps.pop(path, None)
        self._pending.discard(path)
        self._notify_row_changed(
            path, [Qt.DecorationRole, Qt.ToolTipRole, HasLayersRole]
        )
        self._request_thumbnail(path)

    # ===== thumbnails

    def shutdown(self) -> None:
        """Stop thumbnailing and let the running tasks finish.

        Called when this model stops being the one on screen, either
        because a project was reloaded or because the window is closing.
        Without it, tasks still decoding when the model is destroyed emit
        into a deleted object, which Qt reports as a traceback per task
        from a thread the user cannot see.

        Ordering matters. The flag goes up first so nothing new is
        delivered, then the queue is dropped, then the few tasks already
        running are waited on. Only those can still be mid-decode, so the
        wait is bounded by one thumbnail rather than by the whole gallery.
        """
        self._cancelled.set()
        self._pool.clear()
        self._pool.waitForDone(5000)

    def _request_thumbnail(self, path: str) -> None:
        if self._cancelled.is_set():
            return
        if path in self._pending or path in self._pixmaps:
            return
        self._pending.add(path)
        fs_path = resolve_fs_path(self._src_dir, path)
        record = self._project.images.get(path)
        layers = list(record.layers) if record else None
        task = ThumbnailTask(
            path, fs_path, self._thumb_cache, self._signals,
            layers=layers, sticker_resolver=self._sticker_resolver,
            cancelled=self._cancelled,
        )
        self._pool.start(task)

    def _on_thumbnail_ready(self, key: str, image) -> None:
        self._pending.discard(key)
        self._pixmaps[key] = self._fit_to_cell(QPixmap.fromImage(image))
        self._notify_row_changed(key, [Qt.DecorationRole])

    @staticmethod
    def _fit_to_cell(pixmap: QPixmap) -> QPixmap:
        """Pad an aspect-preserved thumbnail onto a fixed THUMBNAIL_SIZE
        canvas, centered. Necessary because the grid uses
        setUniformItemSizes(True) for performance, which *requires* every
        DecorationRole pixmap to actually be the same size. Aspect-preserving
        thumbnailing produces different sizes per image (e.g. a wide
        background image comes out ~192x81, not 192x192), and Qt's
        layout/paint/hit-test caching for that mode gets inconsistent once
        that assumption is violated. Confirmed in practice as stale grey
        rendering, wrong click hitboxes ("holes"), and some images
        rendering far larger on screen than others."""
        if pixmap.size() == QSize(*THUMBNAIL_SIZE):
            return pixmap
        canvas = QPixmap(*THUMBNAIL_SIZE)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        x = (THUMBNAIL_SIZE[0] - pixmap.width()) // 2
        y = (THUMBNAIL_SIZE[1] - pixmap.height()) // 2
        painter.drawPixmap(x, y, pixmap)
        painter.end()
        return canvas

    def _on_thumbnail_failed(self, key: str) -> None:
        self._pending.discard(key)

    def _notify_row_changed(self, path: str, roles: list[int]) -> None:
        row = self._row_of.get(path)
        if row is None:
            return
        idx = self.index(row, 0)
        self.dataChanged.emit(idx, idx, roles)

    # ===== filtering

    def total_count(self) -> int:
        return len(self._all_paths)

    def folder_tree_counts(self) -> dict[str, int]:
        """Every unique directory prefix at every nesting level (not just
        full leaf paths), mapped to the *recursive* count of images at or
        below it, so "イベント" maps to every image nested under it, no
        matter how deep. "" is the root-level bucket (files with no
        folder prefix at all).

        AliceSoft archives have no real directories at all (see paths.py's
        module docstring), so every distinct naming prefix technically
        becomes its own "folder" here. Unlike the old flat dropdown this
        fed, a real tree (see gallery/folder_tree.py) doesn't need these
        filtered down to hide near-singleton entries. A folder with only
        one image just tucks quietly under its parent instead of
        cluttering a flat list."""
        # Walk each image's own ancestry once and add it to every prefix
        # above it. Counting the other way around, asking each prefix which
        # paths sit under it, rescans the whole list once per prefix, and
        # archives that nest a few levels deep have nearly as many prefixes
        # as images.
        counts: dict[str, int] = {"": 0}
        for path in self._all_paths:
            dir_path = split_dir_and_stem(path)[0]
            if not dir_path:
                counts[""] += 1
                continue
            prefix = ""
            for part in dir_path.split("/"):
                prefix = f"{prefix}/{part}" if prefix else part
                counts[prefix] = counts.get(prefix, 0) + 1
        return counts

    def set_filters(
        self,
        *,
        folder: str | None = None,
        status: ImageStatus | None = None,
        group_substr: str = "",
        search: str = "",
        has_edits: bool | None = None,
    ) -> None:
        self._filter_folder = folder
        self._filter_status = status
        self._filter_group_substr = group_substr
        self._filter_search = search
        self._filter_has_edits = has_edits
        self._apply_filters()

    def _apply_filters(self) -> None:
        folder = self._filter_folder
        status = self._filter_status
        group_substr = self._filter_group_substr.lower().strip()
        search = self._filter_search.lower().strip()
        has_edits = self._filter_has_edits

        def matches(path: str) -> bool:
            if folder is not None:
                dir_path = split_dir_and_stem(path)[0]
                if dir_path != folder and not dir_path.startswith(folder + "/"):
                    return False
            record = self._project.images.get(path)
            if status is not None:
                if record is None or record.status != status:
                    return False
            if has_edits is not None:
                if bool(record.layers if record else False) != has_edits:
                    return False
            if group_substr and group_substr not in self._path_to_group.get(path, "").lower():
                return False
            if search and search not in path.lower():
                return False
            return True

        self.beginResetModel()
        self._filtered = [p for p in self._all_paths if matches(p)]
        self._row_of = {p: i for i, p in enumerate(self._filtered)}
        self.endResetModel()

    # ===== status editing

    def path_at(self, row: int) -> str:
        return self._filtered[row]

    def paths_with_layers(self, paths: list[str]) -> list[str]:
        """Of `paths`, the ones that actually carry censor layers, without
        repeats and in the order given.

        Lets a caller count and describe what a bulk edit would touch
        before asking the user to confirm it. Deduplicated because the
        count goes in front of the user, and a path listed twice would
        promise to change more images than clearing them actually does.
        """
        seen: set[str] = set()
        out = []
        for path in paths:
            if path in seen or not self._record(path).layers:
                continue
            seen.add(path)
            out.append(path)
        return out

    def clear_layers_for_paths(self, paths: list[str]) -> int:
        """Delete every censor layer on each path, returning how many
        images changed.

        Paths with nothing on them are skipped rather than counted, so the
        number handed back is what the user would recognise as the number
        of images affected. Thumbnails are re-requested because the cached
        one still shows the censored render.
        """
        cleared = 0
        for path in paths:
            record = self._project.images.get(path)
            if record is None or not record.layers:
                continue
            record.layers = []
            cleared += 1
            self.notify_layers_changed(path)
        if cleared:
            # A "has edits" filter is now wrong about these rows, and they
            # should drop out of the view rather than linger as stale
            # entries the user can still click.
            if self._filter_has_edits is not None:
                self._apply_filters()
            self.project_dirty.emit()
        return cleared

    def set_status_for_paths(self, paths: list[str], status: ImageStatus) -> None:
        touched = False
        for path in paths:
            record = self._project.images.get(path)
            if record is None:
                continue
            record.status = status
            touched = True
            self._notify_row_changed(path, [StatusRole, Qt.DecorationRole, Qt.ToolTipRole])
        if touched:
            self.project_dirty.emit()
