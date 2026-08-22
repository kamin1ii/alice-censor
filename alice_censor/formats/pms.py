"""Decoder for PMS, used inside AJP files to hold the transparency mask.

Derived from pms.c in libsys4, copyright Masaki Chikama and Nunuhara Cabbage,
GPL-2.0-or-later. Translated to Python for Alice Censor in 2026 by kamin1ii
and kept under the terms it arrived under.

PMS is run length encoded, one byte per pixel, with five commands. Values up
to 0xF7 are a literal pixel. Above that they copy a run from the row above or
from two rows above, or repeat one byte, or repeat a pair of bytes.

Only the 8 bit form is here, because that is the only one AJP uses for a
mask. The 16 bit form holds colour and nothing in these archives stores an
image that way.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = b"PM"

# How far past the end of the image the decoder is allowed to write. A run
# can be asked to carry on past the end of its row, and the C this came from
# leaves room rather than clipping, so the same slack is left here.
_SLACK = 10

_LITERAL_MAX = 0xF7
_COPY_ROW_ABOVE = 0xFF
_COPY_TWO_ROWS_ABOVE = 0xFE
_REPEAT_ONE = 0xFD
_REPEAT_PAIR = 0xFC


class PmsError(ValueError):
    """The data is not a PMS, or is one this module cannot read."""


@dataclass(frozen=True)
class PmsHeader:
    version: int
    header_size: int
    bpp: int
    shadow_bpp: int
    x: int
    y: int
    width: int
    height: int
    data_offset: int
    palette_offset: int
    comment_offset: int


def is_pms(data: bytes) -> bool:
    return data[:2] == MAGIC


def is_pms8(data: bytes) -> bool:
    return len(data) > 6 and is_pms(data) and data[6] == 8


def read_header(data: bytes) -> PmsHeader:
    if not is_pms(data):
        raise PmsError("not a PMS image")
    if len(data) < 44:
        raise PmsError(f"truncated PMS header, {len(data)} bytes")
    version, header_size = struct.unpack_from("<2H", data, 2)
    bpp, shadow_bpp = data[6], data[7]
    x, y, width, height, data_offset, palette_offset, comment_offset = struct.unpack_from(
        "<7i", data, 16
    )
    if width <= 0 or height <= 0 or x < 0 or y < 0:
        raise PmsError(f"PMS has a nonsense size, {width}x{height} at {x},{y}")
    return PmsHeader(version, header_size, bpp, shadow_bpp, x, y, width, height,
                     data_offset, palette_offset, comment_offset)


def extract_mask(data: bytes) -> tuple[int, int, bytes]:
    """Decode an 8 bit PMS and return its width, height and one byte a pixel."""
    header = read_header(data)
    if header.bpp != 8:
        raise PmsError(f"PMS mask is {header.bpp} bits per pixel, expected 8")
    if header.data_offset > len(data):
        raise PmsError("PMS pixel offset points past the end of the data")
    return header.width, header.height, _decode8(
        data, header.data_offset, header.width, header.height
    )


def _decode8(data: bytes, at: int, width: int, height: int) -> bytes:
    """Walk the command stream.

    Runs are allowed to overshoot the end of a row and spill into the next
    one, which is why the loop tracks a single position rather than a row
    and a column, and why the buffer has slack on the end.
    """
    out = bytearray(width * height + (width + _SLACK) * _SLACK)
    end = len(data)

    for y in range(height):
        x = 0
        while x < width:
            if at >= end:
                raise PmsError("the PMS command stream ended part way through")
            loc = y * width + x
            command = data[at]
            at += 1

            if command <= _LITERAL_MAX:
                out[loc] = command
                x += 1
                continue

            if command == _COPY_ROW_ABOVE:
                run = data[at] + 3
                at += 1
                _copy_back(out, loc, width, run)
            elif command == _COPY_TWO_ROWS_ABOVE:
                run = data[at] + 3
                at += 1
                _copy_back(out, loc, width * 2, run)
            elif command == _REPEAT_ONE:
                run = data[at] + 4
                value = data[at + 1]
                at += 2
                out[loc:loc + run] = bytes([value]) * run
            elif command == _REPEAT_PAIR:
                run = (data[at] + 3) * 2
                pair = data[at + 1:at + 3]
                at += 3
                out[loc:loc + run] = (pair * ((run + 1) // 2))[:run]
            else:
                # 0xF8 through 0xFB. An escape, so the byte after it is a
                # literal even though it would otherwise be a command.
                out[loc] = data[at]
                at += 1
                run = 1
            x += run

    return bytes(out[:width * height])


def _copy_back(out: bytearray, loc: int, distance: int, run: int) -> None:
    """Copy a run from earlier in the image, which may overlap itself.

    A run longer than the distance it reaches back reads bytes this same
    copy is writing. The C does that with memcpy, which in practice works
    forward one byte at a time, so that is what happens here rather than
    taking a snapshot the way a slice would.
    """
    source = loc - distance
    if source < 0:
        raise PmsError("a PMS run reaches back before the start of the image")
    if run <= distance:
        out[loc:loc + run] = out[source:source + run]
        return
    for offset in range(run):
        out[loc + offset] = out[source + offset]
