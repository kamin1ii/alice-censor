"""The text layer, and the optional box behind it.

Rendering text is hard to assert on directly, so these check the things
that actually matter: that ink lands only inside the box, that the box is
filled when asked and not when not, and that a caption too big for its box
is made to fit rather than spilling out of it.
"""

import pytest
from PIL import Image

from alice_censor import fonts
from alice_censor.project import CensorLayer, LayerType
from alice_censor.rendering import TEXT_SIZE, render_layers

BACKDROP = (90, 140, 90, 255)
BOX = (0.25, 0.4, 0.5, 0.2)  # x, y, w, h as fractions


def base(size=(400, 200)):
    return Image.new("RGBA", size, BACKDROP)


def text_layer(**params):
    settings = {"text": "CENSORED", "color": "#FFFFFF", "size": TEXT_SIZE,
                "background": True, "background_color": "#000000"}
    settings.update(params)
    return CensorLayer(id="t", type=LayerType.TEXT, rect=BOX, params=settings)


def pixels_in_box(image):
    left, top = round(BOX[0] * image.width), round(BOX[1] * image.height)
    right, bottom = left + round(BOX[2] * image.width), top + round(BOX[3] * image.height)
    return image.crop((left, top, right, bottom))


def changed_outside_box(image):
    """Anything painted where the layer had no business painting."""
    left, top = round(BOX[0] * image.width), round(BOX[1] * image.height)
    right, bottom = left + round(BOX[2] * image.width), top + round(BOX[3] * image.height)
    count = 0
    for y in range(image.height):
        for x in range(image.width):
            inside = left <= x < right and top <= y < bottom
            if not inside and image.getpixel((x, y)) != BACKDROP:
                count += 1
    return count


def test_a_caption_is_drawn_and_stays_inside_its_box():
    got = render_layers(base(), [text_layer()])

    assert changed_outside_box(got) == 0
    assert set(_rgba(pixels_in_box(got))) != {BACKDROP}, "something was drawn"


def test_the_background_fills_the_box():
    got = render_layers(base(), [text_layer(background_color="#123456")])

    corners = pixels_in_box(got)
    assert corners.getpixel((0, 0)) == (0x12, 0x34, 0x56, 255)
    assert corners.getpixel((corners.width - 1, corners.height - 1)) == (0x12, 0x34, 0x56, 255)


def test_without_a_background_only_the_glyphs_are_painted():
    got = render_layers(base(), [text_layer(background=False)])

    inside = pixels_in_box(got)
    colours = set(_rgba(inside))
    assert BACKDROP in colours, "the picture still shows through"
    assert len(colours) > 1, "and the text is on top of it"
    assert inside.getpixel((0, 0)) == BACKDROP, "no panel in the corner"


def test_a_background_with_no_text_is_just_a_box():
    """Which makes it a censor bar you can label later."""
    got = render_layers(base(), [text_layer(text="")])

    assert set(_rgba(pixels_in_box(got))) == {(0, 0, 0, 255)}


def test_opacity_lets_the_picture_through():
    solid = render_layers(base(), [text_layer()])
    faint = render_layers(base(), [text_layer(opacity=0.5)])

    assert pixels_in_box(solid).getpixel((0, 0)) == (0, 0, 0, 255)
    corner = pixels_in_box(faint).getpixel((0, 0))
    assert corner != (0, 0, 0, 255) and corner != BACKDROP


def test_a_caption_too_big_for_its_box_is_shrunk_to_fit():
    """Rather than being allowed to run outside the box that was drawn."""
    got = render_layers(base(), [text_layer(text="A VERY LONG CAPTION INDEED", size=0.4)])

    assert changed_outside_box(got) == 0


def test_a_long_caption_wraps_rather_than_being_cut_off():
    one_line = render_layers(base(), [text_layer(text="one", background=False)])
    many = render_layers(
        base(), [text_layer(text="a caption long enough to need several lines to fit",
                            background=False)]
    )

    assert _ink(many) > _ink(one_line), "more of it is on screen, not less"


def test_line_breaks_in_the_text_are_kept():
    single = render_layers(base(), [text_layer(text="AB", background=False)])
    split = render_layers(base(), [text_layer(text="A\nB", background=False)])

    assert _rows_with_ink(split) > _rows_with_ink(single)


def test_japanese_renders():
    """These are Japanese games, so a caption is as likely to be Japanese."""
    got = render_layers(base(), [text_layer(text="検閲済み", background=False)])

    assert _ink(got) > 0


@pytest.mark.parametrize("align", ["left", "center", "right"])
def test_alignment_moves_the_text_without_leaving_the_box(align):
    got = render_layers(base(), [text_layer(text="AB", align=align, background=False)])

    assert changed_outside_box(got) == 0
    assert _ink(got) > 0


def test_size_is_a_fraction_so_it_scales_with_the_image():
    """The same project applied to a bigger image keeps its proportions."""
    small = render_layers(base((400, 200)), [text_layer(background=False)])
    large = render_layers(base((800, 400)), [text_layer(background=False)])

    # Four times the area, so roughly four times the ink, give or take how
    # a glyph lands on a pixel grid.
    assert 2.5 < _ink(large) / max(1, _ink(small)) < 6


def test_an_unknown_font_falls_back_rather_than_failing():
    got = render_layers(base(), [text_layer(font="Nonexistent Face")])

    assert changed_outside_box(got) == 0
    assert set(_rgba(pixels_in_box(got))) != {BACKDROP}


def test_a_box_with_no_area_draws_nothing():
    layer = text_layer()
    layer.rect = (0.5, 0.5, 0.0, 0.0)

    got = render_layers(base(), [layer])

    assert got.tobytes() == base().tobytes()


# ===== the font lookup


def test_at_least_one_family_is_available():
    assert fonts.available(), "no usable font found on this machine"


def test_the_default_family_always_loads():
    assert fonts.load(fonts.DEFAULT_FAMILY, 24) is not None


def test_an_unknown_family_still_gives_a_font():
    assert fonts.load("Not A Font", 24) is not None


def test_a_silly_size_is_clamped_rather_than_refused():
    assert fonts.load(fonts.DEFAULT_FAMILY, -5) is not None


def _rgba(image):
    """Pixels as tuples, without the deprecated getdata."""
    raw = image.convert("RGBA").tobytes()
    return [tuple(raw[i:i + 4]) for i in range(0, len(raw), 4)]


def _ink(image):
    return sum(1 for p in _rgba(image) if p != BACKDROP and p != (0, 0, 0, 255))


def _rows_with_ink(image):
    rows = set()
    for y in range(image.height):
        for x in range(image.width):
            if image.getpixel((x, y)) not in (BACKDROP, (0, 0, 0, 255)):
                rows.add(y)
                break
    return len(rows)
