"""Decoding DCF, an image stored as the difference from another image.

Nothing here is a fixture. Each case builds the container around real QNTs
from the QNT encoder, so a decode goes through every step it would for real.
"""

import struct
import zlib

import pytest
from PIL import Image

from alice_censor.formats import qnt
from alice_censor.formats.dcf import DcfError, base_name, decode, is_dcf, read_header

BASE = (20, 40, 60, 255)
DIFF = (200, 30, 90, 255)


def rotate_name(name):
    """Store a name the way the format does, rotated right so reading it
    back with a rotate left gives the name again."""
    raw = name.encode("cp932")
    rot = (len(raw) % 7) + 1
    return bytes(((b >> rot) | (b << (8 - rot))) & 0xFF for b in raw)


def make_dcf(width, height, chunk_map, diff_image, *, name="base.qnt",
             version=1, bpp=32):
    stored = rotate_name(name)
    header = struct.pack("<5i", version, width, height, bpp, len(stored)) + stored
    out = bytearray(b"dcf " + struct.pack("<i", len(header)) + header)

    blob = struct.pack("<I", len(chunk_map)) + bytes(chunk_map)
    packed = zlib.compress(blob, 9)
    out += b"dfdl" + struct.pack("<iI", 4 + len(packed), len(blob)) + packed

    image = qnt.encode(diff_image)
    out += b"dcgd" + struct.pack("<i", len(image)) + image
    return bytes(out)


def flat(size, colour):
    return Image.new("RGBA", size, colour)


def chunks_for(width, height, changed=()):
    """One byte a chunk. Zero means the chunk changed, anything else means
    the base already has it."""
    across, down = width // 16, height // 16
    return bytes(0 if i in changed else 1 for i in range(across * down))


def test_a_chunk_marked_changed_comes_from_the_difference():
    """Zero means take it from the diff, which is the easy one to invert."""
    data = make_dcf(32, 32, chunks_for(32, 32, changed={0}), flat((32, 32), DIFF))

    got = decode(data, lambda name: flat((32, 32), BASE))

    assert got.getpixel((8, 8)) == DIFF, "the changed chunk"
    assert got.getpixel((24, 8)) == BASE, "its neighbour, unchanged"
    assert got.getpixel((8, 24)) == BASE
    assert got.getpixel((24, 24)) == BASE


def test_every_chunk_unchanged_gives_back_the_base_untouched():
    data = make_dcf(32, 32, chunks_for(32, 32), flat((32, 32), DIFF))

    got = decode(data, lambda name: flat((32, 32), BASE))

    assert set(got.tobytes()[i::4] for i in range(4)) == {
        bytes([c]) * (32 * 32) for c in BASE
    }


def test_pixels_past_the_last_whole_chunk_always_come_from_the_difference():
    """A size that is not a multiple of sixteen has an edge the map does
    not describe at all."""
    data = make_dcf(40, 40, chunks_for(40, 40), flat((40, 40), DIFF))

    got = decode(data, lambda name: flat((40, 40), BASE))

    assert got.getpixel((8, 8)) == BASE, "inside the mapped chunks"
    assert got.getpixel((36, 8)) == DIFF, "the strip past the right edge"
    assert got.getpixel((8, 36)) == DIFF, "the strip past the bottom edge"


def test_with_no_base_the_difference_is_returned_on_its_own():
    """More use than nothing, and what libsys4 does."""
    data = make_dcf(32, 32, chunks_for(32, 32, changed={0}), flat((32, 32), DIFF))

    assert decode(data).getpixel((8, 8)) == DIFF
    assert decode(data, lambda name: None).getpixel((8, 8)) == DIFF


def test_the_name_of_the_base_image_is_unscrambled():
    data = make_dcf(32, 32, chunks_for(32, 32), flat((32, 32), DIFF),
                    name="立ち絵／かなみ.qnt")

    assert base_name(data) == "立ち絵／かなみ.qnt"


@pytest.mark.parametrize("name", ["a.qnt", "ab.qnt", "abc.qnt", "abcdefg.qnt",
                                  "abcdefgh.qnt", "a_rather_longer_name.qnt"])
def test_the_name_survives_whatever_its_length(name):
    """The rotation depends on the length, so every remainder matters."""
    data = make_dcf(32, 32, chunks_for(32, 32), flat((32, 32), DIFF), name=name)

    assert base_name(data) == name


def test_the_base_it_asks_for_is_the_one_it_is_given():
    seen = []
    data = make_dcf(32, 32, chunks_for(32, 32), flat((32, 32), DIFF), name="wanted.qnt")

    decode(data, lambda name: seen.append(name) or flat((32, 32), BASE))

    assert seen == ["wanted.qnt"]


def test_the_header_is_read():
    header = read_header(make_dcf(48, 32, chunks_for(48, 32), flat((48, 32), DIFF)))

    assert (header.width, header.height, header.bpp) == (48, 32, 32)
    assert header.base_name == "base.qnt"


def test_it_recognises_what_it_can_read():
    assert is_dcf(b"dcf rest")
    assert not is_dcf(b"QNT\0")


def test_something_that_is_not_a_dcf_is_refused():
    with pytest.raises(DcfError, match="not a DCF"):
        read_header(b"QNT\0" + bytes(60))


def test_an_unsupported_version_is_named():
    with pytest.raises(DcfError, match="version 2"):
        read_header(make_dcf(32, 32, chunks_for(32, 32), flat((32, 32), DIFF), version=2))


def test_an_unsupported_depth_is_named():
    with pytest.raises(DcfError, match="bits per pixel"):
        read_header(make_dcf(32, 32, chunks_for(32, 32), flat((32, 32), DIFF), bpp=24))


def test_a_base_of_the_wrong_size_is_refused_rather_than_pasted_wrong():
    data = make_dcf(32, 32, chunks_for(32, 32, changed={0}), flat((32, 32), DIFF))

    with pytest.raises(DcfError, match="is 64x64"):
        decode(data, lambda name: flat((64, 64), BASE))


def test_a_difference_that_disagrees_with_the_header_is_refused():
    data = bytearray(make_dcf(32, 32, chunks_for(32, 32), flat((32, 32), DIFF)))
    struct.pack_into("<i", data, 16, 64)  # height in the header only
    with pytest.raises(DcfError, match="says 32x64"):
        decode(bytes(data))


def test_a_missing_chunk_map_section_is_named():
    data = bytearray(make_dcf(32, 32, chunks_for(32, 32), flat((32, 32), DIFF)))
    at = 8 + struct.unpack_from("<i", data, 4)[0]
    data[at:at + 4] = b"junk"
    with pytest.raises(DcfError, match="expected a dfdl"):
        decode(bytes(data))


def test_a_corrupt_chunk_map_is_named():
    data = bytearray(make_dcf(32, 32, chunks_for(32, 32), flat((32, 32), DIFF)))
    at = 8 + struct.unpack_from("<i", data, 4)[0]
    data[at + 12:at + 18] = b"junk!!"
    with pytest.raises(DcfError, match="did not decompress|disagrees"):
        decode(bytes(data))
