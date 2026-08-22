"""Decoding QNT without alice.exe.

Every case here builds a QNT with the reference encoder below, which is a
direct and deliberately slow transcription of qnt.c, and then checks that the
decoder gets the original pixels back. Doing it that way means the tests
describe the format rather than the implementation, so the fast decoder is
free to reach the answer however it likes.
"""

import random
import struct
import zlib

import pytest
from PIL import Image

from alice_censor.formats.qnt import QntError, decode, is_qnt, read_header


# ===== reference encoder
#
# Transcribed from _qnt_write and friends in libsys4's qnt.c, which is
# GPL-2.0-or-later, copyright Masaki Chikama, Nunuhara Cabbage and
# KichikuouChrome. Kept naive on purpose so it can be read against the C.


def _filtered_rows(pixels, width, height):
    padded_width = (width + 1) & ~1
    padded_height = (height + 1) & ~1
    rows = [bytearray(padded_width * 4) for _ in range(padded_height)]
    for y in range(height):
        rows[y][0:width * 4] = pixels[y * width * 4:(y + 1) * width * 4]

    # Bottom right to top left, so every neighbour read is still original.
    for y in range(height - 1, 0, -1):
        for x in range(width - 1, 0, -1):
            for c in range(4):
                up = rows[y - 1][x * 4 + c]
                left = rows[y][(x - 1) * 4 + c]
                rows[y][x * 4 + c] = (((up + left) >> 1) - rows[y][x * 4 + c]) & 0xFF
        for c in range(4):
            rows[y][c] = (rows[y - 1][c] - rows[y][c]) & 0xFF
    for x in range(width - 1, 0, -1):
        for c in range(4):
            rows[0][x * 4 + c] = (rows[0][(x - 1) * 4 + c] - rows[0][x * 4 + c]) & 0xFF
    return rows, padded_width, padded_height


def make_qnt(pixels, width, height, *, alpha=True, version=1, header_size=52):
    """Build a QNT holding these RGBA bytes."""
    rows, padded_width, padded_height = _filtered_rows(pixels, width, height)

    colour = bytearray()
    for c in (2, 1, 0):
        for y in range(0, padded_height, 2):
            for x in range(0, padded_width, 2):
                colour.append(rows[y][x * 4 + c])
                colour.append(rows[y + 1][x * 4 + c])
                colour.append(rows[y][(x + 1) * 4 + c])
                colour.append(rows[y + 1][(x + 1) * 4 + c])
    colour_stream = zlib.compress(bytes(colour), 9)

    if alpha:
        plane = bytearray()
        for y in range(padded_height):
            for x in range(padded_width):
                plane.append(rows[y][x * 4 + 3])
        alpha_stream = zlib.compress(bytes(plane), 9)
    else:
        alpha_stream = b""

    fields = (0, 0, width, height, 24, 1, len(colour_stream), len(alpha_stream))
    if version == 0:
        header = b"QNT\0" + struct.pack("<I", 0) + struct.pack("<8I", *fields)
        header = header.ljust(48, b"\0")
    else:
        header = (b"QNT\0" + struct.pack("<II", version, header_size)
                  + struct.pack("<8I", *fields)).ljust(header_size, b"\0")
    return header + colour_stream + alpha_stream


def gradient(width, height, *, alpha=True):
    """Something with structure in both directions and a flat patch."""
    out = bytearray()
    for y in range(height):
        for x in range(width):
            flat = 3 <= x < 6 and 1 <= y < 4
            out += bytes((
                0x40 if flat else (x * 37 + y * 11) & 0xFF,
                0x40 if flat else (x * 5 + y * 61) & 0xFF,
                0x40 if flat else (x * 97 + y * 3) & 0xFF,
                0xFF if not alpha else (x * 23 + y * 47) & 0xFF,
            ))
    return bytes(out)


def _rgba(image):
    assert image.mode == "RGBA"
    return image.tobytes()


# ===== the tests


@pytest.mark.parametrize("width,height", [
    (8, 6),      # both even, the common case
    (7, 6),      # odd width, so the stored plane carries a padding column
    (8, 5),      # odd height, so it carries a padding row
    (7, 5),      # both odd
    (1, 1),      # no neighbours to predict from at all
    (1, 6),      # a single column, so every pixel predicts from above
    (9, 1),      # a single row, so every pixel predicts from the left
    (2, 2),
])
def test_a_qnt_decodes_back_to_the_pixels_it_was_made_from(width, height):
    pixels = gradient(width, height)

    got = decode(make_qnt(pixels, width, height))

    assert got.size == (width, height)
    assert _rgba(got) == pixels


def test_no_alpha_stream_means_fully_opaque():
    """AliceSoft's own files leave it out rather than store a plane of 255."""
    pixels = gradient(8, 6, alpha=False)

    got = decode(make_qnt(pixels, 8, 6, alpha=False))

    assert _rgba(got) == pixels
    assert read_header(make_qnt(pixels, 8, 6, alpha=False)).has_alpha is False


def test_transparency_survives_a_round_trip():
    pixels = gradient(8, 6)
    assert len(set(pixels[3::4])) > 1, "the fixture must actually vary"

    got = decode(make_qnt(pixels, 8, 6))

    assert _rgba(got)[3::4] == pixels[3::4]


def test_the_older_headerless_layout_is_read():
    """A zero where the version sits means a fixed 48 byte header."""
    pixels = gradient(8, 6)

    data = make_qnt(pixels, 8, 6, version=0)

    assert read_header(data).header_size == 48
    assert _rgba(decode(data)) == pixels


def test_a_larger_header_is_skipped_rather_than_assumed():
    pixels = gradient(8, 6)

    data = make_qnt(pixels, 8, 6, header_size=64)

    assert read_header(data).header_size == 64
    assert _rgba(decode(data)) == pixels


def test_an_empty_colour_stream_decodes_to_black():
    """pixel_size of zero is legal and means there is nothing to draw."""
    data = bytearray(make_qnt(gradient(4, 4, alpha=False), 4, 4, alpha=False))
    struct.pack_into("<I", data, 36, 0)

    got = decode(bytes(data[:52]))

    assert got.getpixel((2, 2)) == (0, 0, 0, 255)


def test_flat_colour_compresses_to_almost_nothing():
    """The reason a censored archive comes out smaller than the original.

    A gradient is not a fair comparison here, since predicting from the
    neighbours flattens one almost as well as it flattens a solid block, so
    the busy image has to be genuinely noisy.
    """
    flat = bytes([0x20, 0x40, 0x60, 0xFF]) * (64 * 64)
    noise = random.Random(20260822)
    busy = bytes(noise.randrange(256) for _ in range(64 * 64 * 4))

    assert len(make_qnt(flat, 64, 64)) * 20 < len(make_qnt(busy, 64, 64))
    assert _rgba(decode(make_qnt(flat, 64, 64))) == flat
    assert _rgba(decode(make_qnt(busy, 64, 64))) == busy, "noise round trips too"


def test_it_recognises_what_it_can_read():
    assert is_qnt(b"QNT\0rest")
    assert not is_qnt(b"AJP\0rest")
    assert not is_qnt(b"\x89PNG")


def test_something_that_is_not_a_qnt_is_refused():
    with pytest.raises(QntError, match="not a QNT"):
        read_header(b"AJP\0" + bytes(60))


def test_a_truncated_header_is_refused():
    with pytest.raises(QntError, match="truncated"):
        read_header(b"QNT\0" + bytes(8))


def test_a_zero_sized_image_is_refused():
    """Rather than dividing by it later."""
    header = (b"QNT\0" + struct.pack("<II", 1, 52)
              + struct.pack("<8I", 0, 0, 0, 8, 24, 1, 0, 0)).ljust(52, b"\0")
    with pytest.raises(QntError, match="no area"):
        read_header(header)


def test_a_corrupt_stream_says_so_rather_than_raising_zlib():
    data = bytearray(make_qnt(gradient(8, 6), 8, 6))
    data[52:60] = b"garbage!"
    with pytest.raises(QntError, match="did not decompress"):
        decode(bytes(data))


def test_a_short_stream_is_refused():
    """A stream that inflates to less than the image needs."""
    short = zlib.compress(b"\0" * 12, 9)
    header = (b"QNT\0" + struct.pack("<II", 1, 52)
              + struct.pack("<8I", 0, 0, 8, 6, 24, 1, len(short), 0)).ljust(52, b"\0")
    with pytest.raises(QntError, match="is short"):
        decode(header + short)


def test_an_unsupported_depth_is_refused():
    data = bytearray(make_qnt(gradient(8, 6), 8, 6))
    struct.pack_into("<I", data, 28, 32)
    with pytest.raises(QntError, match="bits per pixel"):
        decode(bytes(data))


def test_the_decoder_agrees_with_pillow_on_a_known_picture():
    """A last check that nothing is transposed or channel swapped."""
    source = Image.new("RGBA", (6, 4), (0, 0, 0, 255))
    source.putpixel((0, 0), (255, 0, 0, 255))
    source.putpixel((5, 0), (0, 255, 0, 255))
    source.putpixel((0, 3), (0, 0, 255, 255))
    source.putpixel((5, 3), (10, 20, 30, 40))

    got = decode(make_qnt(source.tobytes(), 6, 4))

    assert got.getpixel((0, 0)) == (255, 0, 0, 255)
    assert got.getpixel((5, 0)) == (0, 255, 0, 255)
    assert got.getpixel((0, 3)) == (0, 0, 255, 255)
    assert got.getpixel((5, 3)) == (10, 20, 30, 40)


def test_a_single_pixel_wide_image_reads_its_stored_transparency():
    """libsys4 leaves this one uninitialised and Rance 03 contains one.

    Its transparency reader only writes the first pixel when the image is
    wider than one, so a one wide picture comes back holding whatever was
    in the buffer. alice.exe read the same file as 70 on one run and 96 on
    another. What the file actually stores here is nothing at all.
    """
    pixels = bytes([10, 20, 30, 0, 40, 50, 60, 0, 70, 80, 90, 0])

    got = decode(make_qnt(pixels, 1, 3))

    assert _rgba(got) == pixels
    assert got.getpixel((0, 0))[3] == 0
