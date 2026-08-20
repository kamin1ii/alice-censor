"""Folder tree sidebar for the gallery, a real expandable hierarchy
(unlike a flat indented dropdown) over AliceSoft's fake "directories".
See paths.py's module docstring for why these aren't real directories at
all, just a naming convention.

An "All Images" node is always pinned at the top, selected by default, so
clearing whatever folder you've drilled into is always one click away.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget, QTreeWidgetItem

ALL_IMAGES_LABEL = "All Images"
NO_FOLDER_LABEL = "(no folder)"


class FolderTree(QTreeWidget):
    folder_selected = Signal(object)  # str folder path, or None for "all images"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setColumnCount(1)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        # currentItemChanged, not itemSelectionChanged or selectedItems().
        # Qt's "current item" concept is what stays consistent between a
        # real mouse click and a programmatic setCurrentItem() call; driving
        # this off selectedItems() instead turned out fragile (an ad-hoc
        # item.setSelected(True) doesn't reliably enforce single-selection
        # exclusivity by itself, so a stale previously-selected item could
        # still show up in the list ahead of the new one).
        self.currentItemChanged.connect(self._on_current_item_changed)

    def set_folders(self, folder_counts: dict[str, int], total_count: int) -> None:
        """`folder_counts` maps every directory prefix (at every nesting
        level, "" for root-level files) to its *recursive* image count.
        See GalleryModel.folder_tree_counts(). Rebuilds the whole tree and
        resets selection to "All Images"."""
        self.blockSignals(True)
        self.clear()
        items: dict[str, QTreeWidgetItem] = {}

        all_item = QTreeWidgetItem([f"{ALL_IMAGES_LABEL} ({total_count})"])
        all_item.setData(0, Qt.ItemDataRole.UserRole, None)
        self.addTopLevelItem(all_item)

        root_count = folder_counts.get("", 0)
        if root_count:
            root_item = QTreeWidgetItem([f"{NO_FOLDER_LABEL} ({root_count})"])
            root_item.setData(0, Qt.ItemDataRole.UserRole, "")
            self.addTopLevelItem(root_item)

        # Process shallowest-first so a prefix's parent always already
        # exists in `items` by the time the prefix itself is handled.
        ordered = sorted((p for p in folder_counts if p), key=lambda p: p.count("/"))
        for prefix in ordered:
            parts = prefix.split("/")
            name = parts[-1]
            count = folder_counts[prefix]
            item = QTreeWidgetItem([f"{name} ({count})"])
            item.setData(0, Qt.ItemDataRole.UserRole, prefix)

            parent_prefix = "/".join(parts[:-1])
            parent_item = items.get(parent_prefix) if parent_prefix else None
            if parent_item is not None:
                parent_item.addChild(item)
            else:
                self.addTopLevelItem(item)
            items[prefix] = item

        self.blockSignals(False)
        self.setCurrentItem(all_item)

    def _on_current_item_changed(self, current: QTreeWidgetItem | None, _previous) -> None:
        if current is None:
            return
        self.folder_selected.emit(current.data(0, Qt.ItemDataRole.UserRole))
