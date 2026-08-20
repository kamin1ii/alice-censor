from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..project import ImageStatus
from .folder_tree import FolderTree
from .gallery_model import GalleryModel, PathRole
from .thumbnail_cache import THUMBNAIL_SIZE

_STATUS_LABELS = {
    ImageStatus.UNREVIEWED: "Unreviewed",
    ImageStatus.FLAGGED: "Flagged for censor",
    ImageStatus.CLEAN: "Reviewed clean",
    ImageStatus.NEEDS_EDIT: "Needs manual edit",
}

_ALL_STATUSES = "(all statuses)"
_ANY_EDITS = "(any)"
_HAS_EDITS = "Has edits"
_NO_EDITS = "No edits"


class GalleryWidget(QWidget):
    """Virtualized thumbnail grid for reviewing every extracted image and
    tagging its censor status. Review and editing work the same for AFA and
    ALD projects, and for ALD, which has no naming signal at all, this is
    the only review workflow rather than a fallback.

    Repacking is a different matter. alice-tools has no .ald writer, so an
    ALD project can be extracted, reviewed and edited but never packed back
    up. See MainWindow._repack_blocked_reason."""

    status_changed = Signal()
    open_requested = Signal(str)  # manifest path, for a future editor hook
    auto_flag_requested = Signal()
    clear_edits_requested = Signal(list)  # selected manifest paths

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.model: GalleryModel | None = None
        self._current_folder: str | None = None  # None = "All Images"

        self.folder_tree = FolderTree()
        self.folder_tree.folder_selected.connect(self._on_folder_selected)

        self.status_combo = QComboBox()
        self.status_combo.addItem(_ALL_STATUSES, None)
        for status, label in _STATUS_LABELS.items():
            self.status_combo.addItem(label, status)

        # Separate from status. An image can be FLAGGED with no layers
        # drawn yet, or CLEAN with layers left over, so "reviewed" and "has
        # edits" are different facts and this needs its own filter. See the
        # purple "has edits" badge in the grid, which this complements.
        self.edits_combo = QComboBox()
        self.edits_combo.addItem(_ANY_EDITS, None)
        self.edits_combo.addItem(_HAS_EDITS, True)
        self.edits_combo.addItem(_NO_EDITS, False)

        self.group_filter = QLineEdit()
        self.group_filter.setPlaceholderText("Filter by scene group…")
        self.search_filter = QLineEdit()
        self.search_filter.setPlaceholderText("Filter by filename…")

        self.auto_flag_button = QPushButton("Auto-Flag Explicit Scenes…")
        self.auto_flag_button.setToolTip(
            "Flag unreviewed images matching the explicit-scene naming pattern "
            "(H01-H13, 挿入/射精, etc.). Only available for .afa-style archives "
            "with descriptive names. .ald archives have no naming signal to use."
        )
        self.auto_flag_button.clicked.connect(self.auto_flag_requested)

        filter_bar = QHBoxLayout()
        filter_bar.addWidget(QLabel("Status:"))
        filter_bar.addWidget(self.status_combo, 1)
        filter_bar.addWidget(QLabel("Edits:"))
        filter_bar.addWidget(self.edits_combo, 1)
        filter_bar.addWidget(self.group_filter, 1)
        filter_bar.addWidget(self.search_filter, 1)
        filter_bar.addWidget(self.auto_flag_button)

        self.list_view = QListView()
        self.list_view.setViewMode(QListView.IconMode)
        self.list_view.setFlow(QListView.LeftToRight)
        self.list_view.setWrapping(True)
        self.list_view.setResizeMode(QListView.Adjust)
        self.list_view.setUniformItemSizes(True)
        # SinglePass rather than Batched. Batched defers laying out
        # off-screen items into idle-time chunks, which showed up as an
        # intermittent rendering glitch, a stale grey rect covering an
        # item's title label until scrolling forced a repaint. Most likely
        # a partial-layout region not being fully invalidated after a filter
        # change resets the model. SinglePass computes the full layout
        # immediately instead; measurably slower only on far larger lists
        # than these galleries ever have (tested fine at 1300+ images).
        self.list_view.setLayoutMode(QListView.SinglePass)
        self.list_view.setIconSize(QSize(*THUMBNAIL_SIZE))
        self.list_view.setGridSize(QSize(THUMBNAIL_SIZE[0] + 16, THUMBNAIL_SIZE[1] + 36))
        self.list_view.setSpacing(4)
        self.list_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_view.customContextMenuRequested.connect(self._show_context_menu)
        self.list_view.doubleClicked.connect(self._on_double_clicked)

        status_bar = QHBoxLayout()
        status_bar.addWidget(QLabel("Set selected to:"))
        for status, label in _STATUS_LABELS.items():
            button = QPushButton(label)
            button.clicked.connect(lambda _checked, s=status: self._apply_status_to_selection(s))
            status_bar.addWidget(button)

        # Set apart from the status buttons on purpose. Those record an
        # opinion about an image, this one throws away drawn work.
        status_bar.addSpacing(24)
        self.clear_edits_button = QPushButton("Remove Edits…")
        self.clear_edits_button.setToolTip(
            "Delete every censor layer on the selected images. The images "
            "themselves are never touched, so this only discards the regions "
            "you drew."
        )
        self.clear_edits_button.clicked.connect(self._request_clear_edits)
        status_bar.addWidget(self.clear_edits_button)
        status_bar.addStretch(1)
        self.count_label = QLabel("")
        status_bar.addWidget(self.count_label)

        grid_side = QWidget()
        grid_layout = QVBoxLayout(grid_side)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.addLayout(filter_bar)
        grid_layout.addWidget(self.list_view, 1)
        grid_layout.addLayout(status_bar)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.folder_tree)
        splitter.addWidget(grid_side)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 800])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        self.status_combo.currentIndexChanged.connect(self._on_filters_changed)
        self.edits_combo.currentIndexChanged.connect(self._on_filters_changed)
        self.group_filter.textChanged.connect(self._on_filters_changed)
        self.search_filter.textChanged.connect(self._on_filters_changed)

    # ===== wiring

    def set_auto_flag_available(self, available: bool) -> None:
        self.auto_flag_button.setEnabled(available)

    def set_model(self, model: GalleryModel) -> None:
        # The outgoing model may still have hundreds of thumbnails in
        # flight. Stop them before it goes out of scope, or they finish
        # into an object that is being destroyed.
        if self.model is not None and self.model is not model:
            self.model.shutdown()
        self.model = model
        self.list_view.setModel(model)
        self.list_view.selectionModel().selectionChanged.connect(self._update_count_label)
        model.dataChanged.connect(self._update_count_label)
        model.modelReset.connect(self._update_count_label)
        # Extra insurance alongside the SinglePass layout mode change above
        # (see its comment) against the same stale-repaint class of glitch.
        # Force a full viewport repaint whenever a filter change resets the
        # model, rather than trusting Qt's own dirty-region tracking to
        # catch every case.
        model.modelReset.connect(self.list_view.viewport().update)
        model.project_dirty.connect(self.status_changed)

        # set_folders() resets selection to "All Images" and emits that via
        # folder_selected, which _on_folder_selected below turns into a
        # filter refresh, so this also handles the initial (unfiltered)
        # view for a freshly loaded/re-extracted project.
        self.folder_tree.set_folders(model.folder_tree_counts(), model.total_count())

        self._update_count_label()

    # ===== filters

    def _on_folder_selected(self, folder) -> None:
        self._current_folder = folder
        self._on_filters_changed()

    def _on_filters_changed(self) -> None:
        if self.model is None:
            return
        self.model.set_filters(
            folder=self._current_folder,
            status=self.status_combo.currentData(),
            group_substr=self.group_filter.text(),
            search=self.search_filter.text(),
            has_edits=self.edits_combo.currentData(),
        )
        self._update_count_label()

    # ===== status actions

    def _selected_paths(self) -> list[str]:
        if self.model is None:
            return []
        indexes = self.list_view.selectionModel().selectedIndexes()
        return [idx.data(PathRole) for idx in indexes]

    def _request_clear_edits(self) -> None:
        """Hand the selection to the window, which owns confirming and
        saving. Matches how auto-flagging works, and keeps this widget
        free of both dialogs and persistence."""
        paths = self._selected_paths()
        if paths:
            self.clear_edits_requested.emit(paths)

    def _apply_status_to_selection(self, status: ImageStatus) -> None:
        paths = self._selected_paths()
        if not paths or self.model is None:
            return
        self.model.set_status_for_paths(paths, status)

    def _show_context_menu(self, pos) -> None:
        if self.model is None or not self._selected_paths():
            return
        menu = QMenu(self)
        for status, label in _STATUS_LABELS.items():
            action = menu.addAction(f"Set status: {label}")
            action.triggered.connect(lambda _checked=False, s=status: self._apply_status_to_selection(s))
        menu.addSeparator()
        remove = menu.addAction("Remove all edits from selected…")
        remove.triggered.connect(lambda _checked=False: self._request_clear_edits())
        menu.exec(self.list_view.viewport().mapToGlobal(pos))

    def _on_double_clicked(self, index) -> None:
        path = index.data(PathRole)
        if path:
            self.open_requested.emit(path)

    def _update_count_label(self, *_args) -> None:
        if self.model is None:
            self.count_label.setText("")
            return
        shown = self.model.rowCount()
        total = self.model.total_count()
        selected = len(self.list_view.selectionModel().selectedIndexes()) if self.list_view.selectionModel() else 0
        self.count_label.setText(f"{shown} / {total} shown, {selected} selected")
