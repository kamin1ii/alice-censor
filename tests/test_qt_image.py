from PIL import Image

from alice_censor.gui.qt_image import pil_to_qpixmap


def test_pixel_colors_round_trip_correctly(qapp):
    # A width where RGB (3 bytes/pixel) rows would NOT be 4-byte aligned,
    # to make sure the RGBA-only conversion path actually avoids the
    # classic PIL/QImage stride-mismatch corruption.
    width, height = 37, 5
    im = Image.new("RGB", (width, height), (0, 0, 0))
    im.putpixel((0, 0), (255, 0, 0))
    im.putpixel((width - 1, height - 1), (0, 255, 0))
    im.putpixel((10, 2), (0, 0, 255))

    pixmap = pil_to_qpixmap(im)
    qimage = pixmap.toImage()

    assert qimage.width() == width and qimage.height() == height
    assert qimage.pixelColor(0, 0).getRgb()[:3] == (255, 0, 0)
    assert qimage.pixelColor(width - 1, height - 1).getRgb()[:3] == (0, 255, 0)
    assert qimage.pixelColor(10, 2).getRgb()[:3] == (0, 0, 255)
    assert qimage.pixelColor(5, 3).getRgb()[:3] == (0, 0, 0)  # untouched pixel


def test_preserves_alpha_channel(qapp):
    im = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    im.putpixel((5, 5), (255, 255, 255, 128))

    pixmap = pil_to_qpixmap(im)
    qimage = pixmap.toImage()

    assert qimage.pixelColor(5, 5).alpha() == 128
    assert qimage.pixelColor(0, 0).alpha() == 0
