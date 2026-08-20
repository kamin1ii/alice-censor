"""Confirmation dialog for propagating a source image's current layers to
the rest of its scene group in one action."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class BatchApplyDialog(QDialog):
    def __init__(
        self,
        group_key: str,
        member_paths: list[str],
        layer_count: int,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Apply to Scene Group")
        self.setMinimumWidth(440)

        plural = "" if layer_count == 1 else "s"
        info = QLabel(
            f'Add the current {layer_count} layer{plural} to every checked image below, '
            f'all in scene group "{group_key}".\n'
            f"Existing layers on those images are kept, not replaced. Images that don't "
            f"line up perfectly can be nudged afterward by opening them individually."
        )
        info.setWordWrap(True)

        self.list_widget = QListWidget()
        for path in member_paths:
            item = QListWidgetItem(path)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.list_widget.addItem(item)

        select_all_button = QPushButton("Select All")
        select_none_button = QPushButton("Select None")
        select_all_button.clicked.connect(lambda: self._set_all_checked(True))
        select_none_button.clicked.connect(lambda: self._set_all_checked(False))
        select_row = QHBoxLayout()
        select_row.addWidget(select_all_button)
        select_row.addWidget(select_none_button)
        select_row.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addLayout(select_row)
        layout.addWidget(self.list_widget, 1)
        layout.addWidget(buttons)

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(state)

    def selected_paths(self) -> list[str]:
        return [
            self.list_widget.item(i).text()
            for i in range(self.list_widget.count())
            if self.list_widget.item(i).checkState() == Qt.Checked
        ]
