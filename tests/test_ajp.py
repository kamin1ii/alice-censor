"""Decoding AJP, which is a JPEG and a transparency mask in one file.

There is no encoder for the format anywhere, so these build the container by
hand around a JPEG Pillow makes and a PMS mask from the PMS tests.
"""

import struct
import zlib
from io import BytesIO

import pytest
from PIL import Image

from alice_censor.formats.ajp import KEY, AjpError, decode, is_ajp, read_header

from .test_pms import make_pms


def scramble(stream):
    """The same exclusive or the format applies, which undoes itself."""
    return bytes(b ^ k for b, k in zip(stream[:len(KEY)], KEY)) + stream[len(KEY):]


def jpeg_bytes(image, quality=95):
    buffer = BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


def make_ajp(image, mask=b"", *, width=None, height=None):
    colour = scramble(jpeg_bytes(image))
    mask = scramble(mask) if mask else b""
    head = bytearray(36)
    head[0:4] = b"AJP\0"
    struct.pack_into("<6I", head, 12,
                     width or image.width, height or image.height,
                     36, len(colour), 36 + len(colour), len(mask))
    return bytes(head) + colour + mask


def picture(size=(16, 16)):
    """Flat colour, so lossy compression cannot move it."""
    return Image.new("RGB", size, (200, 100, 50))


def test_the_colour_comes_out_of_the_jpeg_inside():
    got = decode(make_ajp(picture()))

    assert got.mode == "RGBA"
    assert got.size == (16, 16)
    r, g, b, _ = got.getpixel((8, 8))
    assert abs(r - 200) <= 2 and abs(g - 100) <= 2 and abs(b - 50) <= 2


def test_no_mask_means_fully_opaque():
    """470 of Rance 02's 920 AJPs are stored this way."""
    got = decode(make_ajp(picture()))

    assert set(got.getchannel("A").tobytes()) == {255}


def test_a_pms_mask_becomes_the_transparency():
    mask = make_pms([0xFD, 0x0C, 0x40] * 16, 16, 16)

    got = decode(make_ajp(picture(), mask))

    assert set(got.getchannel("A").tobytes()) == {0x40}


def test_a_zlib_mask_becomes_the_transparency():
    mask = zlib.compress(bytes([0x20]) * (16 * 16), 9)
    assert mask[:1] == b"\x78", "the format is recognised by this first byte"

    got = decode(make_ajp(picture(), mask))

    assert set(got.getchannel("A").tobytes()) == {0x20}


def test_a_webp_mask_becomes_the_transparency():
    buffer = BytesIO()
    source = Image.new("RGBA", (16, 16), (0, 0, 0, 0x7F))
    source.save(buffer, "WEBP", lossless=True)

    got = decode(make_ajp(picture(), buffer.getvalue()))

    assert set(got.getchannel("A").tobytes()) == {0x7F}


def test_a_mask_that_cannot_be_read_falls_back_to_opaque():
    """libsys4 does the same. A picture beats no picture."""
    got = decode(make_ajp(picture(), b"PM" + bytes(70)))

    assert set(got.getchannel("A").tobytes()) == {255}


def test_a_mask_of_the_wrong_size_falls_back_to_opaque():
    got = decode(make_ajp(picture(), make_pms([0xFD, 0x00, 0x40] * 4, 4, 4)))

    assert set(got.getchannel("A").tobytes()) == {255}


def test_the_scrambling_is_undone():
    """Without it the JPEG will not open, which is the whole point of it."""
    data = bytearray(make_ajp(picture()))
    assert data[36:38] != b"\xff\xd8", "the stored bytes are not a plain JPEG"

    assert decode(bytes(data)).size == (16, 16)


def test_it_recognises_what_it_can_read():
    assert is_ajp(b"AJP\0rest")
    assert not is_ajp(b"QNT\0rest")


def test_the_header_is_read():
    header = read_header(make_ajp(picture((24, 12))))

    assert (header.width, header.height) == (24, 12)
    assert header.has_alpha is False


def test_something_that_is_not_an_ajp_is_refused():
    with pytest.raises(AjpError, match="not an AJP"):
        read_header(b"QNT\0" + bytes(60))


def test_a_truncated_header_is_refused():
    with pytest.raises(AjpError, match="truncated"):
        read_header(b"AJP\0" + bytes(8))


def test_a_stream_pointing_past_the_end_is_refused():
    data = bytearray(make_ajp(picture()))
    struct.pack_into("<I", data, 24, 999999)
    with pytest.raises(AjpError, match="past the end"):
        decode(bytes(data))


def test_a_header_that_disagrees_with_the_picture_is_refused():
    """Rather than handing back something the caller will misplace."""
    with pytest.raises(AjpError, match="says 99x99"):
        decode(make_ajp(picture(), width=99, height=99))


def test_colour_that_is_not_a_jpeg_at_all_is_reported():
    head = bytearray(36)
    head[0:4] = b"AJP\0"
    struct.pack_into("<6I", head, 12, 8, 8, 36, 20, 56, 0)
    with pytest.raises(AjpError, match="not a readable JPEG"):
        decode(bytes(head) + bytes(20))
