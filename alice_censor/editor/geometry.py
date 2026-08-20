"""Pure geometry helpers for the region editor.

Kept free of any mouse-event/widget code so the math that's actually easy
to get subtly wrong, namely resizing a rect from one of 8 handles and
converting between image-pixel rects and the fractional rects CensorLayer
stores, can be unit tested directly, without needing to simulate real Qt
mouse drags (which can't be exercised interactively in this environment).

Canvas convention. Scene coordinates are image pixel coordinates (the base
image is placed at scene position (0, 0), unscaled; view zoom is a
QGraphicsView-level transform that Qt already maps mouse positions through
via mapToScene, so none of this needs to know about zoom at all).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF

MIN_REGION_SIZE = 4.0

# Each handle position is a set of these direction chars; resize_rect
# applies the delta to whichever rect edges the position's chars name.
_POSITIONS = {"n", "s", "e", "w", "ne", "nw", "se", "sw"}


def resize_rect(
    start_rect: QRectF, position: str, delta: QPointF, min_size: float = MIN_REGION_SIZE
) -> QRectF:
    """Return a new rect with the edge(s) named by `position` moved by
    `delta` (in the same local coordinate space as `start_rect`), leaving
    the opposite edge(s) fixed. `position` is one of n/s/e/w/ne/nw/se/sw.

    If the result would collapse below `min_size` in either dimension, the
    original `start_rect` is returned unchanged rather than an invalid or
    inverted rect.
    """
    if position not in _POSITIONS:
        raise ValueError(f"Unknown handle position: {position!r}")

    left, top, right, bottom = start_rect.left(), start_rect.top(), start_rect.right(), start_rect.bottom()
    if "n" in position:
        top += delta.y()
    if "s" in position:
        bottom += delta.y()
    if "w" in position:
        left += delta.x()
    if "e" in position:
        right += delta.x()

    # Deliberately not using QRectF.normalized() here. For a drag that
    # crosses the opposite edge (e.g. dragging "se" up past "nw"),
    # normalizing would silently flip the rect into a large box on the
    # other side instead of rejecting the drag, the same bug class as
    # "too small", just inverted, so one width/height check below covers
    # both (a negative size is always < min_size for a positive min_size).
    if (right - left) < min_size or (bottom - top) < min_size:
        return QRectF(start_rect)
    return QRectF(left, top, right - left, bottom - top)


def image_rect_to_fraction(
    rect: QRectF, image_size: tuple[int, int]
) -> tuple[float, float, float, float]:
    """Convert a rect in image-pixel coordinates to the (x, y, w, h)
    fraction tuple CensorLayer.rect stores.

    Deliberately NOT clamped to [0, 1]. A region (most commonly a sticker)
    is allowed to extend past the image edge so it can be placed
    out-of-bounds on purpose, without shrinking it to fit. Rendering is
    responsible for clipping to the actual image canvas."""
    width, height = image_size
    if width <= 0 or height <= 0:
        return (0.0, 0.0, 0.0, 0.0)

    x = rect.left() / width
    y = rect.top() / height
    right = rect.right() / width
    bottom = rect.bottom() / height
    return (x, y, right - x, bottom - y)


def fraction_to_image_rect(
    fraction: tuple[float, float, float, float], image_size: tuple[int, int]
) -> QRectF:
    """Inverse of image_rect_to_fraction."""
    width, height = image_size
    x, y, w, h = fraction
    return QRectF(x * width, y * height, w * width, h * height)
