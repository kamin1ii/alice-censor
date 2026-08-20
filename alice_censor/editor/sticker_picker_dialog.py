"""Thumbnail picker for the sticker library, a small managed folder of
overlay images the image-overlay layer type can choose from, instead of
re-browsing to an external file on disk every time."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, UnidentifiedImageError
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..gui.qt_image import pil_to_qpixmap
from ..stickers import add_sticker, list_stickers, remove_sticker

THUMB_SIZE = (96, 96)


class StickerPickerDialog(QDialog):
    def __init__(
        self, sticker_dir: str | Path, current: str | None = None, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.sticker_dir = Path(sticker_dir)
        self.setWindowTitle("Choose Sticker")
        self.resize(480, 420)

        self.list_widget = QListWidget()
        self.list_widget.setViewMode(QListWidget.IconMode)
        self.list_widget.setIconSize(QSize(*THUMB_SIZE))
        self.list_widget.setResizeMode(QListWidget.Adjust)
        self.list_widget.setSpacing(6)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self.accept())

        add_button = QPushButton("Add Sticker…")
        add_button.clicked.connect(self._on_add)
        remove_button = QPushButton("Remove Selected")
        remove_button.clicked.connect(self._on_remove)
        button_row = QHBoxLayout()
        button_row.addWidget(add_button)
        button_row.addWidget(remove_button)
        button_row.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(button_row)
        layout.addWidget(self.list_widget, 1)
        layout.addWidget(buttons)

        self._reload(select=current)

    def _reload(self, select: str | None = None) -> None:
        self.list_widget.clear()
        for name in list_stickers(self.sticker_dir):
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, name)
            icon = self._load_icon(name)
            if icon is not None:
                item.setIcon(icon)
            self.list_widget.addItem(item)
            if name == select:
                self.list_widget.setCurrentItem(item)

    def _load_icon(self, name: str) -> QIcon | None:
        path = self.sticker_dir / name
        try:
            with Image.open(path) as im:
                im = im.convert("RGBA")
                im.thumbnail(THUMB_SIZE, Image.LANCZOS)
                return QIcon(pil_to_qpixmap(im))
        except (OSError, UnidentifiedImageError):
            return None

    def _on_add(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Add Sticker", filter="Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"
        )
        if not path:
            return
        name = add_sticker(self.sticker_dir, path)
        self._reload(select=name)

    def _on_remove(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        name = item.data(Qt.UserRole)
        reply = QMessageBox.question(
            self,
            "Remove Sticker",
            f'Remove "{name}" from the sticker library?\n\n'
            f"This deletes the file. Any layers already using it will fail to "
            f"render until you pick a different sticker.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        remove_sticker(self.sticker_dir, name)
        self._reload()

    def selected_filename(self) -> str | None:
        item = self.list_widget.currentItem()
        return item.data(Qt.UserRole) if item else None
