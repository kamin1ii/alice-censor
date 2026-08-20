import pytest
from PIL import Image

from alice_censor.project import CensorLayer, LayerType
from alice_censor.rendering import (
    RenderError,
    rect_to_pixels,
    rect_to_pixels_unclamped,
    render_layers,
)


def _layer(type_, rect, params=None, enabled=True, id_="l1"):
    return CensorLayer(id=id_, type=type_, rect=rect, params=params or {}, enabled=enabled)


def test_rect_to_pixels_basic():
    assert rect_to_pixels((0.25, 0.25, 0.5, 0.5), (100, 200)) == (25, 50, 75, 150)


def test_rect_to_pixels_clamps_out_of_bounds():
    assert rect_to_pixels((-0.5, -0.5, 2.0, 2.0), (100, 100)) == (0, 0, 100, 100)


def test_rect_to_pixels_degenerate_collapses_to_empty_not_negative():
    # w/h that would push right < left collapses to a zero-area box rather
    # than an invalid (right < left) one.
    assert rect_to_pixels((0.9, 0.9, -0.5, -0.5), (100, 100)) == (90, 90, 90, 90)


def test_solid_layer_fills_region_with_opaque_color():
    base = Image.new("RGB", (100, 100), (255, 255, 255))
    layer = _layer(LayerType.SOLID, (0.0, 0.0, 0.5, 0.5), {"color": "#FF0000"})

    result = render_layers(base, [layer])

    assert result.getpixel((10, 10)) == (255, 0, 0)
    assert result.getpixel((90, 90)) == (255, 255, 255)  # untouched region


def test_solid_layer_respects_opacity():
    base = Image.new("RGB", (10, 10), (0, 0, 0))
    layer = _layer(LayerType.SOLID, (0.0, 0.0, 1.0, 1.0), {"color": "#FFFFFF", "opacity": 0.5})

    result = render_layers(base, [layer])

    # 50% white over black should land close to mid-gray.
    pixel = result.getpixel((5, 5))
    assert all(abs(c - 127) <= 2 for c in pixel[:3])


def test_invalid_color_raises_render_error():
    base = Image.new("RGB", (10, 10), (0, 0, 0))
    layer = _layer(LayerType.SOLID, (0, 0, 1, 1), {"color": "not-a-color"})
    with pytest.raises(RenderError):
        render_layers(base, [layer])


def test_disabled_layer_is_skipped():
    base = Image.new("RGB", (10, 10), (0, 0, 0))
    layer = _layer(LayerType.SOLID, (0, 0, 1, 1), {"color": "#FFFFFF"}, enabled=False)

    result = render_layers(base, [layer])

    assert result.getpixel((5, 5))[:3] == (0, 0, 0)


def test_multiple_layers_apply_in_order():
    base = Image.new("RGB", (10, 10), (0, 0, 0))
    layers = [
        _layer(LayerType.SOLID, (0, 0, 1, 1), {"color": "#FF0000"}, id_="l1"),
        _layer(LayerType.SOLID, (0, 0, 1, 1), {"color": "#0000FF"}, id_="l2"),
    ]

    result = render_layers(base, layers)

    # the second (blue) layer should end up on top
    assert result.getpixel((5, 5))[:3] == (0, 0, 255)


def test_pixelate_produces_uniform_blocks():
    # A checkerboard pattern, pixelated with a block size larger than the
    # checker squares, should come out as flat blocks (no more per-pixel
    # alternation) rather than preserving the fine pattern.
    base = Image.new("RGB", (40, 40))
    pixels = base.load()
    for y in range(40):
        for x in range(40):
            pixels[x, y] = (255, 255, 255) if (x // 2 + y // 2) % 2 == 0 else (0, 0, 0)

    layer = _layer(LayerType.PIXELATE, (0, 0, 1, 1), {"block_size": 10})
    result = render_layers(base, [layer])

    # sample a 10x10 block: every pixel in it should now be identical
    block = [result.getpixel((x, y))[:3] for x in range(0, 10) for y in range(0, 10)]
    assert len(set(block)) == 1


def test_blur_softens_a_hard_edge():
    base = Image.new("RGB", (40, 40), (0, 0, 0))
    pixels = base.load()
    for y in range(40):
        for x in range(20, 40):
            pixels[x, y] = (255, 255, 255)

    layer = _layer(LayerType.BLUR, (0, 0, 1, 1), {"radius": 6})
    result = render_layers(base, [layer])

    # right at the old hard edge, blur should produce an intermediate value
    # rather than a pure black/white step.
    value = result.getpixel((20, 20))[0]
    assert 20 < value < 235


def test_overlay_stretch_fills_entire_box():
    base = Image.new("RGB", (100, 100), (255, 255, 255))
    sticker = Image.new("RGBA", (10, 5), (0, 255, 0, 255))

    def resolver(_ref):
        return sticker_path

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        sticker_path = Path(tmp) / "sticker.png"
        sticker.save(sticker_path, "PNG")

        layer = _layer(
            LayerType.OVERLAY,
            (0.0, 0.0, 0.5, 0.5),
            {"sticker": "sticker.png", "fit": "stretch", "opacity": 1.0},
        )
        result = render_layers(base, [layer], sticker_resolver=resolver)

        assert result.getpixel((25, 25))[:3] == (0, 255, 0)
        assert result.getpixel((75, 75))[:3] == (255, 255, 255)  # outside the box


def test_overlay_contain_preserves_aspect_and_centers():
    base = Image.new("RGB", (100, 100), (255, 255, 255))
    sticker = Image.new("RGBA", (10, 10), (0, 255, 0, 255))  # square sticker

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        sticker_path = Path(tmp) / "sticker.png"
        sticker.save(sticker_path, "PNG")

        # box is wide (100x50) but sticker is square -- "contain" should
        # center a square inside it, leaving the box's left/right empty.
        layer = _layer(
            LayerType.OVERLAY,
            (0.0, 0.0, 1.0, 0.5),
            {"sticker": "sticker.png", "fit": "contain"},
        )
        result = render_layers(base, [layer], sticker_resolver=lambda _r: sticker_path)

        assert result.getpixel((50, 25))[:3] == (0, 255, 0)  # center: covered
        assert result.getpixel((2, 25))[:3] == (255, 255, 255)  # far left: empty


def test_overlay_opacity_blends_with_background():
    base = Image.new("RGB", (10, 10), (255, 255, 255))
    sticker = Image.new("RGBA", (10, 10), (0, 0, 0, 255))

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        sticker_path = Path(tmp) / "sticker.png"
        sticker.save(sticker_path, "PNG")

        layer = _layer(
            LayerType.OVERLAY,
            (0.0, 0.0, 1.0, 1.0),
            {"sticker": "sticker.png", "fit": "stretch", "opacity": 0.5},
        )
        result = render_layers(base, [layer], sticker_resolver=lambda _r: sticker_path)

        pixel = result.getpixel((5, 5))
        assert all(abs(c - 127) <= 2 for c in pixel[:3])


def test_rect_to_pixels_unclamped_does_not_clamp():
    assert rect_to_pixels_unclamped((-0.5, -0.5, 2.0, 2.0), (100, 100)) == (-50, -50, 150, 150)


def test_overlay_partly_off_right_edge_is_clipped_not_squeezed():
    # A 40x40 box straddling the right edge of a 100-wide image (only the
    # left 20px on-canvas). A stretch-fit sticker should still be sized as
    # if the box were the full 40x40 -- i.e. the on-canvas half should show
    # exactly the sticker's left half, not the whole sticker squeezed into
    # 20px (which would show the sticker's full left-to-right content).
    base = Image.new("RGB", (100, 100), (255, 255, 255))
    # Left half green, right half blue -- squeezing vs. clipping produce
    # visibly different results at the on-canvas edge.
    sticker = Image.new("RGBA", (40, 40), (0, 255, 0, 255))
    sticker_pixels = sticker.load()
    for y in range(40):
        for x in range(20, 40):
            sticker_pixels[x, y] = (0, 0, 255, 255)

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        sticker_path = Path(tmp) / "sticker.png"
        sticker.save(sticker_path, "PNG")

        # box: x in [80, 120) -> only [80, 100) is on-canvas (first 20 of 40 px)
        layer = _layer(
            LayerType.OVERLAY,
            (0.80, 0.0, 0.40, 0.40),
            {"sticker": "sticker.png", "fit": "stretch", "opacity": 1.0},
        )
        result = render_layers(base, [layer], sticker_resolver=lambda _r: sticker_path)

        # If squeezed to fit the 20px-wide visible remainder, this pixel
        # (at the box's on-canvas midpoint) would land in the sticker's
        # blue half. Clipped correctly, the on-canvas strip is entirely
        # from the sticker's green half.
        assert result.getpixel((90, 10))[:3] == (0, 255, 0)
        # untouched region well past the box
        assert result.getpixel((10, 90))[:3] == (255, 255, 255)


def test_overlay_entirely_off_canvas_draws_nothing():
    base = Image.new("RGB", (10, 10), (255, 255, 255))
    layer = _layer(
        LayerType.OVERLAY,
        (1.5, 1.5, 0.5, 0.5),
        {"sticker": "sticker.png", "fit": "stretch"},
    )
    # sticker_resolver deliberately raises if called -- an entirely
    # off-canvas region shouldn't even try to load the sticker file.
    def resolver(_ref):
        raise AssertionError("sticker should not be loaded for an off-canvas region")

    result = render_layers(base, [layer], sticker_resolver=resolver)
    assert result.getpixel((5, 5))[:3] == (255, 255, 255)


def test_overlay_missing_sticker_raises_render_error():
    base = Image.new("RGB", (10, 10), (255, 255, 255))
    layer = _layer(LayerType.OVERLAY, (0, 0, 1, 1), {"sticker": "does_not_exist.png"})
    with pytest.raises(RenderError):
        render_layers(base, [layer], sticker_resolver=lambda r: r)


def test_unknown_layer_type_is_rejected_at_construction():
    # Used to be caught by render_layers. CensorLayer now coerces its type
    # on construction, so an unknown one fails while the project file that
    # carried it is still in hand rather than at render time.
    with pytest.raises(ValueError):
        CensorLayer(id="l1", type="not-a-real-type", rect=(0, 0, 1, 1))


def test_known_layer_type_given_as_a_string_is_coerced():
    layer = CensorLayer(id="l1", type="blur", rect=(0, 0, 1, 1))
    assert layer.type is LayerType.BLUR


def test_render_does_not_mutate_input_image():
    base = Image.new("RGB", (10, 10), (255, 255, 255))
    layer = _layer(LayerType.SOLID, (0, 0, 1, 1), {"color": "#000000"})

    render_layers(base, [layer])

    assert base.getpixel((5, 5)) == (255, 255, 255)


def test_transparent_source_preserves_alpha_outside_layers():
    base = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    layer = _layer(LayerType.SOLID, (0.0, 0.0, 0.5, 1.0), {"color": "#FF0000"})

    result = render_layers(base, [layer])

    assert result.getpixel((2, 5)) == (255, 0, 0, 255)
    assert result.getpixel((8, 5))[3] == 0  # still transparent outside the layer
