"""Draggable, resizable region rectangles on the editor canvas.

Each RegionItem represents one CensorLayer's region. Moving uses Qt's
built-in QGraphicsItem drag (ItemIsMovable), which is well-tested Qt
behavior rather than custom code. Resizing is driven by 8 child handle items whose actual delta
math lives in geometry.resize_rect (unit tested separately); the handles
here are thin event-forwarding wrappers around that.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QCursor, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsRectItem

from .geometry import resize_rect

HANDLE_SIZE = 8.0
HANDLE_POSITIONS = ("nw", "n", "ne", "e", "se", "s", "sw", "w")

_CURSOR_FOR_POSITION = {
    "n": Qt.SizeVerCursor,
    "s": Qt.SizeVerCursor,
    "e": Qt.SizeHorCursor,
    "w": Qt.SizeHorCursor,
    "ne": Qt.SizeBDiagCursor,
    "sw": Qt.SizeBDiagCursor,
    "nw": Qt.SizeFDiagCursor,
    "se": Qt.SizeFDiagCursor,
}


class ResizeHandle(QGraphicsRectItem):
    def __init__(self, position: str, parent: "RegionItem"):
        half = HANDLE_SIZE / 2
        super().__init__(-half, -half, HANDLE_SIZE, HANDLE_SIZE, parent)
        self.position = position
        self.setBrush(QBrush(QColor("white")))
        self.setPen(QPen(QColor("black"), 1))
        self.setZValue(10)
        self.setVisible(False)
        self.setCursor(QCursor(_CURSOR_FOR_POSITION[position]))
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        self._drag_start_local = None
        self._start_rect = None

    def mousePressEvent(self, event) -> None:
        parent = self.parentItem()
        self._drag_start_local = parent.mapFromScene(event.scenePos())
        self._start_rect = QRectF(parent.rect())
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_start_local is None:
            return
        parent = self.parentItem()
        current_local = parent.mapFromScene(event.scenePos())
        delta = current_local - self._drag_start_local
        new_rect = resize_rect(self._start_rect, self.position, delta)
        parent.setRect(new_rect)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_start_local = None
        self._start_rect = None
        self.parentItem().notify_changed()
        event.accept()


class RegionItem(QGraphicsRectItem):
    def __init__(self, rect: QRectF, layer_id: str, on_changed: Callable[[str], None] | None = None):
        super().__init__(rect)
        self.layer_id = layer_id
        self.on_changed = on_changed
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setPen(QPen(QColor("#00c8ff"), 2))
        self.setBrush(QBrush(QColor(0, 200, 255, 40)))
        self.setAcceptHoverEvents(True)

        self._handles: dict[str, ResizeHandle] = {
            position: ResizeHandle(position, self) for position in HANDLE_POSITIONS
        }
        self._update_handles()

    def notify_changed(self) -> None:
        if self.on_changed:
            self.on_changed(self.layer_id)

    def setRect(self, *args) -> None:  # noqa: N802 - Qt override naming
        super().setRect(*args)
        self._update_handles()

    def _update_handles(self) -> None:
        r = self.rect()
        self._handles["nw"].setPos(r.topLeft())
        self._handles["ne"].setPos(r.topRight())
        self._handles["se"].setPos(r.bottomRight())
        self._handles["sw"].setPos(r.bottomLeft())
        self._handles["n"].setPos((r.left() + r.right()) / 2, r.top())
        self._handles["s"].setPos((r.left() + r.right()) / 2, r.bottom())
        self._handles["e"].setPos(r.right(), (r.top() + r.bottom()) / 2)
        self._handles["w"].setPos(r.left(), (r.top() + r.bottom()) / 2)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedHasChanged:
            for handle in self._handles.values():
                handle.setVisible(bool(value))
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self.notify_changed()
