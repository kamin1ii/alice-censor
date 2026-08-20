"""QGraphicsView-based canvas. Shows the (live-rendered) image and hosts
one RegionItem per censor layer.

Drawing a *new* region is handled at the view level. Press-drag on empty
canvas (not on an existing item) rubber-bands a new rect, emitted via
region_created on release for the dialog to turn into a CensorLayer.
Everything else (move/resize/select an existing region) is delegated to
the items themselves via the normal Qt event chain.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from .geometry import MIN_REGION_SIZE
from .region_item import RegionItem

_DRAW_PEN = QPen(QColor("#00c8ff"), 2, Qt.DashLine)


class RegionCanvas(QGraphicsView):
    region_created = Signal(QRectF)
    region_changed = Signal(str)  # layer_id
    selection_changed = Signal(object)  # layer_id (str) or None

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.NoDrag)

        self._pixmap_item = None
        self._region_items: dict[str, RegionItem] = {}
        self._drawing = False
        self._draw_start = None
        self._temp_rect_item = None

        self._scene.selectionChanged.connect(self._on_selection_changed)

    # ===== image / regions

    def set_image(self, pixmap) -> None:
        self._scene.clear()
        self._region_items.clear()
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._pixmap_item.setZValue(-1)
        self._scene.setSceneRect(0, 0, pixmap.width(), pixmap.height())

    def update_image(self, pixmap) -> None:
        """Swap the displayed pixmap (e.g. after a live-preview re-render)
        without touching the region items on top of it."""
        if self._pixmap_item is not None:
            self._pixmap_item.setPixmap(pixmap)

    def image_size(self) -> tuple[int, int]:
        if self._pixmap_item is None:
            return (0, 0)
        pixmap = self._pixmap_item.pixmap()
        return (pixmap.width(), pixmap.height())

    def add_region(self, layer_id: str, rect: QRectF) -> RegionItem:
        item = RegionItem(rect, layer_id, on_changed=self.region_changed.emit)
        self._scene.addItem(item)
        self._region_items[layer_id] = item
        return item

    def remove_region(self, layer_id: str) -> None:
        item = self._region_items.pop(layer_id, None)
        if item is not None:
            self._scene.removeItem(item)

    def region_rect(self, layer_id: str) -> QRectF | None:
        """The region's rect in scene/image-pixel coordinates. Uses
        mapRectToScene rather than the item's raw rect(), since the item can be
        translated via its built-in movable-drag pos(), which rect() alone
        doesn't reflect; mapRectToScene folds pos() in without also pulling
        in the (slightly larger) bounding box of the resize-handle
        children the way sceneBoundingRect() would."""
        item = self._region_items.get(layer_id)
        return item.mapRectToScene(item.rect()) if item is not None else None

    def select_region(self, layer_id: str | None) -> None:
        self._scene.blockSignals(True)
        for lid, item in self._region_items.items():
            item.setSelected(lid == layer_id)
        self._scene.blockSignals(False)

    def clear_selection(self) -> None:
        self._scene.clearSelection()

    def fit_to_view(self) -> None:
        if self._pixmap_item is not None:
            self.fitInView(self._pixmap_item, Qt.KeepAspectRatio)

    # ===== selection

    def _on_selection_changed(self) -> None:
        selected = self._scene.selectedItems()
        if selected and isinstance(selected[0], RegionItem):
            self.selection_changed.emit(selected[0].layer_id)
        else:
            self.selection_changed.emit(None)

    # ===== drawing new regions

    def mousePressEvent(self, event) -> None:
        item = self.itemAt(event.position().toPoint())
        if item is None or item is self._pixmap_item:
            self._drawing = True
            self._draw_start = self.mapToScene(event.position().toPoint())
            self._temp_rect_item = self._scene.addRect(
                QRectF(self._draw_start, self._draw_start), _DRAW_PEN
            )
            self._scene.clearSelection()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drawing and self._temp_rect_item is not None:
            current = self.mapToScene(event.position().toPoint())
            rect = QRectF(self._draw_start, current).normalized()
            self._temp_rect_item.setRect(rect)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drawing:
            self._drawing = False
            rect = self._temp_rect_item.rect() if self._temp_rect_item is not None else QRectF()
            if self._temp_rect_item is not None:
                self._scene.removeItem(self._temp_rect_item)
                self._temp_rect_item = None

            # Emit the raw drawn rect, even if it extends past the image
            # edge (e.g. a sticker deliberately placed partly out of
            # bounds), since rendering clips to the canvas later and doesn't
            # need the stored rect to already fit inside it. Still require
            # the *visible* (on-canvas) portion to meet the minimum size,
            # so a drag that never actually touches the image can't create
            # an invisible region.
            scene_rect = self._scene.sceneRect()
            visible = rect.intersected(scene_rect)
            if visible.width() >= MIN_REGION_SIZE and visible.height() >= MIN_REGION_SIZE:
                self.region_created.emit(rect)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
            event.accept()
            return
        super().wheelEvent(event)
