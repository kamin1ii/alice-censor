import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication

from alice_censor.editor.canvas import RegionCanvas


def _mouse_event(event_type, view_pos: QPoint, button=Qt.LeftButton, buttons=None):
    return QMouseEvent(
        event_type,
        QPointF(view_pos),
        QPointF(view_pos),  # scenePosition arg (Qt6 5-arg ctor uses local+scene+global... )
        button,
        buttons if buttons is not None else button,
        Qt.NoModifier,
    )


def _press(canvas, pos):
    canvas.mousePressEvent(_mouse_event(QEvent.MouseButtonPress, pos))


def _move(canvas, pos):
    canvas.mouseMoveEvent(_mouse_event(QEvent.MouseMove, pos, buttons=Qt.LeftButton))


def _release(canvas, pos):
    canvas.mouseReleaseEvent(_mouse_event(QEvent.MouseButtonRelease, pos))


def _make_canvas(qapp, size=(200, 100)):
    canvas = RegionCanvas()
    canvas.resize(400, 400)
    canvas.show()
    QApplication.processEvents()  # without this, the viewport never gets
    # real geometry and mapToScene()/itemAt() produce garbage -- resize()
    # alone isn't enough outside a running event loop.
    pixmap = QPixmap(*size)
    pixmap.fill(Qt.white)
    canvas.set_image(pixmap)
    return canvas


def test_set_image_sets_scene_rect(qapp):
    canvas = _make_canvas(qapp, (300, 150))
    assert canvas.image_size() == (300, 150)
    assert canvas._scene.sceneRect() == QRectF(0, 0, 300, 150)


def test_add_and_remove_region(qapp):
    canvas = _make_canvas(qapp)
    canvas.add_region("layer-1", QRectF(10, 10, 50, 50))
    assert canvas.region_rect("layer-1") == QRectF(10, 10, 50, 50)

    canvas.remove_region("layer-1")
    assert canvas.region_rect("layer-1") is None


def test_select_region_updates_item_selection_and_emits_signal(qapp):
    canvas = _make_canvas(qapp)
    canvas.add_region("layer-1", QRectF(0, 0, 20, 20))
    canvas.add_region("layer-2", QRectF(50, 50, 20, 20))

    received = []
    canvas.selection_changed.connect(received.append)

    canvas._region_items["layer-2"].setSelected(True)
    assert received == ["layer-2"]

    canvas.clear_selection()
    assert received == ["layer-2", None]


def test_select_region_programmatically(qapp):
    canvas = _make_canvas(qapp)
    canvas.add_region("layer-1", QRectF(0, 0, 20, 20))
    canvas.add_region("layer-2", QRectF(50, 50, 20, 20))

    canvas.select_region("layer-1")

    assert canvas._region_items["layer-1"].isSelected() is True
    assert canvas._region_items["layer-2"].isSelected() is False


def test_drawing_on_empty_canvas_creates_a_region(qapp):
    canvas = _make_canvas(qapp)
    created = []
    canvas.region_created.connect(created.append)

    # QGraphicsView center-aligns a scene smaller than its viewport by
    # default, so raw view-pixel coordinates don't reliably land inside a
    # small test image -- derive view points from the scene points we
    # actually want to drag between (the inverse of what the production
    # code does with mapToScene).
    scene_start = QPointF(10, 10)
    scene_end = QPointF(80, 60)
    start = canvas.mapFromScene(scene_start)
    end = canvas.mapFromScene(scene_end)

    _press(canvas, start)
    _move(canvas, end)
    _release(canvas, end)

    assert len(created) == 1
    rect = created[0]
    expected = QRectF(scene_start, scene_end).normalized()
    assert rect.topLeft() == expected.topLeft()
    assert rect.bottomRight() == expected.bottomRight()


def test_pressing_inside_existing_region_does_not_start_a_new_draw(qapp):
    canvas = _make_canvas(qapp)
    canvas.add_region("layer-1", QRectF(10, 10, 100, 60))

    created = []
    canvas.region_created.connect(created.append)

    inside_point = canvas.mapFromScene(QPointF(30, 30))
    _press(canvas, inside_point)
    assert canvas._drawing is False

    _move(canvas, canvas.mapFromScene(QPointF(40, 40)))
    _release(canvas, canvas.mapFromScene(QPointF(40, 40)))

    assert created == []  # existing item handled the drag, not a new region


def test_drag_past_image_edge_emits_the_raw_unclamped_rect(qapp):
    # Canvas image is 200x100. Dragging from inside the image to a point
    # past its right edge (scene x=250) should NOT clamp the emitted rect
    # back to the image bounds -- e.g. for a sticker placed on purpose
    # partly out of bounds, the caller needs the true extent, not a
    # rect squeezed to fit on-canvas.
    canvas = _make_canvas(qapp)
    created = []
    canvas.region_created.connect(created.append)

    scene_start = QPointF(150, 10)
    scene_end = QPointF(250, 60)  # 50px past the 200-wide image's right edge
    start = canvas.mapFromScene(scene_start)
    end = canvas.mapFromScene(scene_end)

    _press(canvas, start)
    _move(canvas, end)
    _release(canvas, end)

    assert len(created) == 1
    rect = created[0]
    assert rect.right() == pytest.approx(250)
    assert rect.left() == pytest.approx(150)


def test_drag_with_too_small_a_visible_overlap_is_rejected(qapp):
    # Canvas image is 200x100. A drag that's mostly off-canvas, leaving
    # less than MIN_REGION_SIZE actually over the image, should still be
    # rejected -- unlike the fully-out-of-bounds case above, this isn't a
    # deliberately placed sticker, just a drag that barely touches the
    # image at all.
    canvas = _make_canvas(qapp)
    created = []
    canvas.region_created.connect(created.append)

    scene_start = QPointF(199, 10)
    scene_end = QPointF(250, 60)  # only 1px of overlap with the image
    start = canvas.mapFromScene(scene_start)
    end = canvas.mapFromScene(scene_end)

    _press(canvas, start)
    _move(canvas, end)
    _release(canvas, end)

    assert created == []


def test_tiny_drag_does_not_create_a_degenerate_region(qapp):
    canvas = _make_canvas(qapp)
    created = []
    canvas.region_created.connect(created.append)

    # Both points safely inside the 200x100 image (see mapFromScene note
    # in test_drawing_on_empty_canvas_creates_a_region above) but only 1px
    # apart -- below MIN_REGION_SIZE.
    start = canvas.mapFromScene(QPointF(50, 50))
    end = canvas.mapFromScene(QPointF(51, 51))
    _press(canvas, start)
    _move(canvas, end)
    _release(canvas, end)

    assert created == []
