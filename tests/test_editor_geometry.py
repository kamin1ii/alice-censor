import pytest
from PySide6.QtCore import QPointF, QRectF

from alice_censor.editor.geometry import (
    fraction_to_image_rect,
    image_rect_to_fraction,
    resize_rect,
)


def _rect(l, t, r, b):
    return QRectF(l, t, r - l, b - t)


# -- resize_rect: each handle moves only the edges it names -----------------


def test_resize_se_grows_bottom_right_keeps_top_left():
    start = _rect(10, 10, 50, 50)
    result = resize_rect(start, "se", QPointF(20, 5))
    assert (result.left(), result.top(), result.right(), result.bottom()) == (10, 10, 70, 55)


def test_resize_nw_moves_top_left_keeps_bottom_right():
    start = _rect(10, 10, 50, 50)
    result = resize_rect(start, "nw", QPointF(-5, -5))
    assert (result.left(), result.top(), result.right(), result.bottom()) == (5, 5, 50, 50)


def test_resize_n_only_moves_top_edge():
    start = _rect(10, 10, 50, 50)
    result = resize_rect(start, "n", QPointF(999, 8))  # x delta should be ignored entirely
    assert (result.left(), result.top(), result.right(), result.bottom()) == (10, 18, 50, 50)


def test_resize_e_only_moves_right_edge():
    start = _rect(10, 10, 50, 50)
    result = resize_rect(start, "e", QPointF(15, 999))
    assert (result.left(), result.top(), result.right(), result.bottom()) == (10, 10, 65, 50)


def test_resize_w_only_moves_left_edge():
    start = _rect(10, 10, 50, 50)
    result = resize_rect(start, "w", QPointF(-15, 999))
    assert (result.left(), result.top(), result.right(), result.bottom()) == (-5, 10, 50, 50)


def test_resize_s_only_moves_bottom_edge():
    start = _rect(10, 10, 50, 50)
    result = resize_rect(start, "s", QPointF(999, -10))
    assert (result.left(), result.top(), result.right(), result.bottom()) == (10, 10, 50, 40)


@pytest.mark.parametrize("position", ["n", "s", "e", "w", "ne", "nw", "se", "sw"])
def test_resize_all_eight_handles_are_recognized(position):
    start = _rect(0, 0, 100, 100)
    result = resize_rect(start, position, QPointF(1, 1))
    assert isinstance(result, QRectF)


def test_resize_unknown_position_raises():
    with pytest.raises(ValueError):
        resize_rect(_rect(0, 0, 10, 10), "bogus", QPointF(1, 1))


def test_resize_dragging_past_opposite_edge_does_not_invert_or_collapse():
    # Dragging the "se" handle far up-left, past the top-left corner,
    # would otherwise produce an inverted (right < left) rect.
    start = _rect(10, 10, 50, 50)
    result = resize_rect(start, "se", QPointF(-1000, -1000))
    # Falls back to the unchanged start rect rather than collapsing/inverting.
    assert result == start


def test_resize_refuses_to_collapse_below_min_size():
    start = _rect(0, 0, 100, 100)
    result = resize_rect(start, "e", QPointF(-99, 0), min_size=4.0)
    assert result == start  # would have produced width=1, rejected


def test_resize_allows_shrinking_down_to_min_size():
    start = _rect(0, 0, 100, 100)
    result = resize_rect(start, "e", QPointF(-96, 0), min_size=4.0)
    assert result.width() == pytest.approx(4.0)


# -- fraction <-> pixel rect conversion -------------------------------------


def test_image_rect_to_fraction_basic():
    rect = _rect(25, 50, 75, 150)
    assert image_rect_to_fraction(rect, (100, 200)) == pytest.approx((0.25, 0.25, 0.5, 0.5))


def test_fraction_to_image_rect_is_inverse_of_image_rect_to_fraction():
    original = _rect(10, 20, 90, 180)
    image_size = (200, 200)
    fraction = image_rect_to_fraction(original, image_size)
    round_tripped = fraction_to_image_rect(fraction, image_size)
    assert round_tripped.left() == pytest.approx(original.left())
    assert round_tripped.top() == pytest.approx(original.top())
    assert round_tripped.right() == pytest.approx(original.right())
    assert round_tripped.bottom() == pytest.approx(original.bottom())


def test_image_rect_to_fraction_does_not_clamp_out_of_bounds():
    # A region drawn partly off the image (e.g. a sticker placed out of
    # bounds on purpose) must round-trip its true extent, not get silently
    # clipped back to [0, 1] -- rendering is responsible for clipping later.
    rect = _rect(-50, -50, 150, 250)
    x, y, w, h = image_rect_to_fraction(rect, (100, 200))
    assert x == pytest.approx(-0.5)
    assert y == pytest.approx(-0.25)
    assert x + w == pytest.approx(1.5)
    assert y + h == pytest.approx(1.25)


def test_image_rect_to_fraction_zero_size_image_does_not_divide_by_zero():
    assert image_rect_to_fraction(_rect(0, 0, 10, 10), (0, 0)) == (0.0, 0.0, 0.0, 0.0)
