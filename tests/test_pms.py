"""Decoding the PMS masks that live inside AJP files.

PMS is run length encoded with five commands, so each one gets a case built
by hand here rather than a fixture, since a real mask is game data.
"""

import struct

import pytest

from alice_censor.formats.pms import (
    PmsError,
    _copy_back,
    extract_mask,
    is_pms,
    is_pms8,
    read_header,
)


def make_pms(commands, width, height, *, bpp=8, data_offset=64):
    """Wrap a command stream in a PMS8 header."""
    header = bytearray(data_offset)
    header[0:2] = b"PM"
    struct.pack_into("<2H", header, 2, 2, data_offset)
    header[6] = bpp
    struct.pack_into("<7i", header, 16, 0, 0, width, height, data_offset, 0, 0)
    return bytes(header) + bytes(commands)


def literals(values):
    """Every value here has to be below the first command byte."""
    assert all(v <= 0xF7 for v in values)
    return list(values)


def test_literal_bytes_come_through_in_order():
    data = make_pms(literals([1, 2, 3, 4, 5, 6]), 3, 2)

    assert extract_mask(data) == (3, 2, bytes([1, 2, 3, 4, 5, 6]))


def test_a_run_repeats_one_byte():
    """0xFD, then a count less four, then the byte."""
    data = make_pms([0xFD, 0x02, 0x7F], 6, 1)

    assert extract_mask(data) == (6, 1, bytes([0x7F] * 6))


def test_a_run_repeats_a_pair_of_bytes():
    """0xFC, then a count of pairs less three, then the two bytes."""
    data = make_pms([0xFC, 0x00, 0x10, 0x20], 6, 1)

    assert extract_mask(data) == (6, 1, bytes([0x10, 0x20] * 3))


def test_a_run_copies_from_the_row_above():
    data = make_pms(literals([1, 2, 3, 4]) + [0xFF, 0x01], 4, 2)

    assert extract_mask(data) == (4, 2, bytes([1, 2, 3, 4, 1, 2, 3, 4]))


def test_a_run_copies_from_two_rows_above():
    data = make_pms(
        literals([1, 2, 3, 4]) + [0xFD, 0x00, 0x09] + [0xFE, 0x01], 4, 3
    )

    width, height, plane = extract_mask(data)
    assert plane[0:4] == bytes([1, 2, 3, 4])
    assert plane[4:8] == bytes([9, 9, 9, 9])
    assert plane[8:12] == bytes([1, 2, 3, 4]), "copied from two rows up"


def test_a_copy_longer_than_it_reaches_back_reads_what_it_just_wrote():
    """Tested directly, because a well formed image never shows it.

    A run that overshoots its row spills into the slack past the image
    rather than into the next row, since every row reads its own commands.
    The behaviour still has to match the C, which copies forward one byte
    at a time rather than taking a snapshot the way a slice would.
    """
    buffer = bytearray([7, 8] + [0] * 10)

    _copy_back(buffer, 2, 2, 8)

    assert bytes(buffer[:10]) == bytes([7, 8, 7, 8, 7, 8, 7, 8, 7, 8])


def test_an_escaped_byte_is_taken_literally():
    """0xF8 through 0xFB mean the next byte is a value, not a command."""
    data = make_pms([0xF9, 0xFF] + literals([2]), 2, 1)

    assert extract_mask(data) == (2, 1, bytes([0xFF, 2]))


def test_a_mask_of_solid_opaque_decodes():
    """The common case, since 0xFF cannot be written as a literal.

    One run per row. Every row starts a fresh walk of the command stream,
    so a single run covering the whole image would leave the later rows
    with nothing to read.
    """
    data = make_pms([0xFD, 0x00, 0xFF] * 4, 4, 4)

    assert extract_mask(data) == (4, 4, bytes([0xFF] * 16))


def test_it_recognises_what_it_can_read():
    eight = make_pms([0xFD, 0x00, 0x01], 4, 1)
    sixteen = make_pms([0], 4, 1, bpp=16)

    assert is_pms(eight) and is_pms8(eight)
    assert is_pms(sixteen) and not is_pms8(sixteen)
    assert not is_pms(b"AJP\0")


def test_the_header_is_read():
    header = read_header(make_pms([0xFD, 0x00, 0x01], 40, 30))

    assert (header.width, header.height, header.bpp) == (40, 30, 8)
    assert header.data_offset == 64


def test_something_that_is_not_a_pms_is_refused():
    with pytest.raises(PmsError, match="not a PMS"):
        read_header(b"QNT\0" + bytes(60))


def test_a_sixteen_bit_mask_is_named_rather_than_misread():
    with pytest.raises(PmsError, match="16 bits per pixel"):
        extract_mask(make_pms([0], 4, 1, bpp=16))


def test_a_nonsense_size_is_refused():
    data = bytearray(make_pms([0], 4, 1))
    struct.pack_into("<i", data, 24, -5)
    with pytest.raises(PmsError, match="nonsense size"):
        read_header(bytes(data))


def test_a_command_stream_that_stops_early_is_refused():
    with pytest.raises(PmsError, match="ended part way"):
        extract_mask(make_pms(literals([1, 2]), 8, 4))
