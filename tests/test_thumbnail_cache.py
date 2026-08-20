import os
import time

from PIL import Image

from alice_censor.gallery.thumbnail_cache import ThumbnailCache
from alice_censor.project import CensorLayer, LayerType


def _make_image(path, size=(400, 300), color=(255, 0, 0)):
    Image.new("RGB", size, color).save(path, "PNG")


def test_get_or_create_generates_thumbnail(tmp_path):
    src = tmp_path / "source.png"
    _make_image(src)
    cache = ThumbnailCache(tmp_path / "thumbs")

    thumb_path = cache.get_or_create(src)

    assert thumb_path is not None
    assert thumb_path.exists()
    with Image.open(thumb_path) as im:
        assert im.width <= 192 and im.height <= 192


def test_get_or_create_reuses_cached_thumbnail(tmp_path):
    src = tmp_path / "source.png"
    _make_image(src)
    cache = ThumbnailCache(tmp_path / "thumbs")

    first = cache.get_or_create(src)
    first_mtime = first.stat().st_mtime
    second = cache.get_or_create(src)

    assert second == first
    assert second.stat().st_mtime == first_mtime


def test_get_or_create_regenerates_when_source_changes(tmp_path):
    src = tmp_path / "source.png"
    _make_image(src, color=(255, 0, 0))
    cache = ThumbnailCache(tmp_path / "thumbs")
    first = cache.get_or_create(src)
    first_mtime = first.stat().st_mtime

    time.sleep(0.05)
    _make_image(src, color=(0, 255, 0))
    future = time.time() + 5
    os.utime(src, (future, future))

    second = cache.get_or_create(src)
    assert second.stat().st_mtime >= first_mtime


def test_get_or_create_missing_source_returns_none(tmp_path):
    cache = ThumbnailCache(tmp_path / "thumbs")
    assert cache.get_or_create(tmp_path / "nope.png") is None


def test_get_or_create_unreadable_file_returns_none(tmp_path):
    bad = tmp_path / "not_an_image.png"
    bad.write_bytes(b"not actually a png")
    cache = ThumbnailCache(tmp_path / "thumbs")
    assert cache.get_or_create(bad) is None


def test_transparency_is_preserved_not_flattened_onto_a_guessed_color(tmp_path):
    # A transparent RGBA image whose underlying (normally-invisible) RGB
    # data is pure black. Naively dropping the alpha channel (plain
    # convert("RGB")) would bake that black in -- but flattening onto any
    # *fixed* color is also wrong in general (e.g. white text on a
    # transparent background becomes illegible if flattened onto white).
    # The only generally-correct answer is to keep the real alpha channel
    # and let the consumer (the gallery grid) composite it.
    src = tmp_path / "source.png"
    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    im.save(src, "PNG")
    cache = ThumbnailCache(tmp_path / "thumbs")

    thumb_path = cache.get_or_create(src)

    assert thumb_path is not None
    assert thumb_path.suffix == ".png"  # JPEG can't carry alpha at all
    with Image.open(thumb_path) as thumb:
        assert thumb.mode == "RGBA"
        pixel = thumb.getpixel((thumb.width // 2, thumb.height // 2))
    assert pixel[3] == 0  # still fully transparent, not baked into any color


def test_opaque_region_of_rgba_image_is_preserved(tmp_path):
    src = tmp_path / "source.png"
    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    im.paste((10, 20, 30, 255), (0, 0, 100, 100))
    im.save(src, "PNG")
    cache = ThumbnailCache(tmp_path / "thumbs")

    thumb_path = cache.get_or_create(src)

    with Image.open(thumb_path) as thumb:
        pixel = thumb.getpixel((thumb.width // 2, thumb.height // 2))
    assert pixel == (10, 20, 30, 255)  # PNG is lossless -- exact match


def _solid_layer(color="#00FF00"):
    return CensorLayer(id="l1", type=LayerType.SOLID, rect=(0.0, 0.0, 1.0, 1.0), params={"color": color})


def test_thumbnail_reflects_censor_layers_not_just_the_original(tmp_path):
    src = tmp_path / "source.png"
    _make_image(src, color=(255, 0, 0))
    cache = ThumbnailCache(tmp_path / "thumbs")

    plain_thumb = cache.get_or_create(src)
    censored_thumb = cache.get_or_create(src, layers=[_solid_layer()])

    assert plain_thumb != censored_thumb  # distinct cache entries
    with Image.open(plain_thumb) as im:
        assert im.getpixel((im.width // 2, im.height // 2))[:3] == (255, 0, 0)
    with Image.open(censored_thumb) as im:
        assert im.getpixel((im.width // 2, im.height // 2))[:3] == (0, 255, 0)


def test_disabled_layers_are_ignored_for_thumbnail(tmp_path):
    src = tmp_path / "source.png"
    _make_image(src, color=(255, 0, 0))
    cache = ThumbnailCache(tmp_path / "thumbs")

    layer = _solid_layer()
    layer.enabled = False
    thumb = cache.get_or_create(src, layers=[layer])

    with Image.open(thumb) as im:
        assert im.getpixel((im.width // 2, im.height // 2))[:3] == (255, 0, 0)


def test_different_layer_params_produce_different_cache_entries(tmp_path):
    src = tmp_path / "source.png"
    _make_image(src, color=(255, 255, 255))
    cache = ThumbnailCache(tmp_path / "thumbs")

    red_thumb = cache.get_or_create(src, layers=[_solid_layer("#FF0000")])
    blue_thumb = cache.get_or_create(src, layers=[_solid_layer("#0000FF")])

    assert red_thumb != blue_thumb
    with Image.open(blue_thumb) as im:
        assert im.getpixel((im.width // 2, im.height // 2))[:3] == (0, 0, 255)
