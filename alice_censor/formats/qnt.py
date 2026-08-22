"""Decoder for QNT images, the lossless format AliceSoft uses for CG.

Derived from qnt.c in libsys4, which carries this notice.

    Copyright (C) 1997-1998 Masaki Chikama (Wren)
                  1998-     <masaki-c@is.aist-nara.ac.jp>
                  2019-2020 Nunuhara Cabbage <nunuhara@haniwa.technology>
                  2020      <KichikuouChrome@gmail.com>

    This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 2 of the License, or
    (at your option) any later version.

Translated to Python and restructured for Alice Censor in 2026 by kamin1ii.
Kept under GPL-2.0-or-later, the terms it arrived under.

A QNT holds two zlib streams, one for colour and one for transparency, and
both are stored the same way. Every value is the difference between a pixel
and a prediction made from its neighbours, so long stretches of flat colour
turn into runs of zero and compress well.

Colour is stored as three separate planes in blue, green, red order rather
than as interleaved pixels, and each plane is written as a sequence of two
by two blocks. Both planes round their dimensions up to even numbers, and
the padding is real data in the stream even though no pixel uses it.

Decoding is done with byte string slicing and Pillow rather than a loop over
pixels. The reason is speed. The prediction step is a recurrence, where each
pixel needs the one to its left and the one above it, and a full size CG runs
to two and a half million of those. In Python that takes the best part of a
second per image, and an archive holds thousands.

The way out is that QNT predicts pixels exactly the way PNG does. PNG calls
it the Average filter and adds the stored value where QNT subtracts it, so
negating every byte turns one into the other. That lets Pillow undo the
prediction in C, and leaves this module doing only work that byte slicing
can do in bulk.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from io import BytesIO

from PIL import Image

MAGIC = b"QNT"

# Turning a stored difference into the value PNG wants. See the module
# docstring. Doing it with translate applies it to a whole plane at C speed.
_NEGATE = bytes((-value) & 0xFF for value in range(256))

# PNG filter type bytes, one per scanline.
_SUB = 1
_AVERAGE = 3


class QntError(ValueError):
    """The data is not a QNT, or is one this module cannot read."""


@dataclass(frozen=True)
class QntHeader:
    version: int
    header_size: int
    x0: int
    y0: int
    width: int
    height: int
    bpp: int
    reserved: int
    pixel_size: int
    alpha_size: int

    @property
    def padded_width(self) -> int:
        return (self.width + 1) & ~1

    @property
    def padded_height(self) -> int:
        return (self.height + 1) & ~1

    @property
    def has_alpha(self) -> bool:
        """False means fully opaque.

        AliceSoft's own files leave the alpha stream out rather than store a
        plane of 255, and libsys4 fills in opaque when it finds none. Writing
        a transparency plane where the original had none is what turned the
        Rance 03 main menu black, so this distinction matters.
        """
        return self.alpha_size > 0


def is_qnt(data: bytes) -> bool:
    return data[:3] == MAGIC


def read_header(data: bytes) -> QntHeader:
    """Parse the fixed part of the header.

    There are two layouts. The older one has no size field and is always 48
    bytes, and it is recognised by a zero where the newer one keeps a version
    number.
    """
    if not is_qnt(data):
        raise QntError("not a QNT image")
    if len(data) < 48:
        raise QntError(f"truncated QNT header, {len(data)} bytes")

    version = struct.unpack_from("<I", data, 4)[0]
    if version == 0:
        header_size = 48
        fields = struct.unpack_from("<8I", data, 8)
    else:
        header_size = struct.unpack_from("<I", data, 8)[0]
        fields = struct.unpack_from("<8I", data, 12)

    header = QntHeader(version, header_size, *fields)
    if header.width == 0 or header.height == 0:
        raise QntError(f"QNT has no area, {header.width}x{header.height}")
    if header.header_size < 44:
        raise QntError(f"QNT header size {header.header_size} is too small")
    return header


def decode(data: bytes) -> Image.Image:
    """Decode a QNT into an RGBA image."""
    header = read_header(data)
    if header.bpp != 24:
        raise QntError(f"unsupported bits per pixel, {header.bpp}")

    width, height = header.width, header.height
    colour_at = header.header_size
    alpha_at = colour_at + header.pixel_size

    if header.pixel_size:
        plane_size = header.padded_width * header.padded_height
        raw = _inflate(data[colour_at:alpha_at], plane_size * 3, "colour")
        # Stored blue first, then green, then red.
        blue, green, red = (
            _undo_prediction(
                _deinterleave(raw[i * plane_size:(i + 1) * plane_size], width, height),
                width,
                height,
            )
            for i in range(3)
        )
    else:
        blue = green = red = Image.new("L", (width, height), 0)

    if header.alpha_size:
        raw = _inflate(
            data[alpha_at:alpha_at + header.alpha_size],
            header.padded_width * height,
            "alpha",
        )
        alpha = _undo_prediction(_unpad_rows(raw, width, height), width, height)
    else:
        alpha = Image.new("L", (width, height), 255)

    return Image.merge("RGBA", (red, green, blue, alpha))


def _inflate(stream: bytes, at_least: int, what: str) -> bytes:
    try:
        raw = zlib.decompress(stream)
    except zlib.error as exc:
        raise QntError(f"the {what} stream did not decompress, {exc}") from exc
    if len(raw) < at_least:
        raise QntError(
            f"the {what} stream is short, got {len(raw)} bytes and needed {at_least}"
        )
    return raw


def _deinterleave(plane: bytes, width: int, height: int) -> bytes:
    """Turn one stored colour plane into plain rows.

    The plane arrives as two by two blocks, four bytes at a time, scanning
    left to right and then top to bottom over a grid of blocks. Taking every
    fourth byte pulls out one corner of every block at once, and assigning
    into a slice with a step of two interleaves two of those corners back
    into a row, both of which Python does in C.
    """
    padded_width = (width + 1) & ~1
    padded_height = (height + 1) & ~1
    blocks_across = padded_width // 2

    top_left = plane[0::4]
    bottom_left = plane[1::4]
    top_right = plane[2::4]
    bottom_right = plane[3::4]

    rows = bytearray(width * height)
    row = bytearray(padded_width)
    for block_row in range(padded_height // 2):
        start = block_row * blocks_across
        stop = start + blocks_across
        for offset, (evens, odds) in enumerate(
            ((top_left, top_right), (bottom_left, bottom_right))
        ):
            y = block_row * 2 + offset
            if y >= height:
                break
            row[0::2] = evens[start:stop]
            row[1::2] = odds[start:stop]
            rows[y * width:(y + 1) * width] = row[:width]
    return bytes(rows)


def _unpad_rows(raw: bytes, width: int, height: int) -> bytes:
    """Drop the padding column from a transparency plane.

    Rows are padded out to an even width but the row count is not padded,
    so the plane is plain rows with a stride that can be one wider than the
    image.
    """
    stride = (width + 1) & ~1
    if stride == width:
        return raw[:width * height]
    rows = bytearray(width * height)
    for y in range(height):
        at = y * stride
        rows[y * width:(y + 1) * width] = raw[at:at + width]
    return bytes(rows)


def _undo_prediction(plane: bytes, width: int, height: int) -> Image.Image:
    """Recover one plane by handing the recurrence to Pillow as a PNG.

    Every pixel is stored as a prediction minus the truth, where the
    prediction is the pixel above averaged with the pixel to the left. PNG's
    Average filter is the same prediction with the sign flipped, so negating
    the plane and labelling each row Average makes a PNG decoder do the work.

    Two positions do not fit that pattern and are patched by hand. The first
    row has nothing above it and predicts from the left alone, which is PNG's
    Sub filter. The first column has nothing to its left and predicts from
    directly above, where PNG would halve it, so the column is walked here
    and each row's opening byte is adjusted to land on the right value.
    """
    negated = plane.translate(_NEGATE)

    scanlines = bytearray()
    scanlines.append(_SUB)
    first = bytearray(negated[0:width])
    first[0] = plane[0]
    scanlines += first

    above = plane[0]
    for y in range(1, height):
        at = y * width
        here = (above - plane[at]) & 0xFF
        scanlines.append(_AVERAGE)
        row = bytearray(negated[at:at + width])
        row[0] = (here - (above >> 1)) & 0xFF
        scanlines += row
        above = here

    return _grayscale_png(bytes(scanlines), width, height)


def _grayscale_png(scanlines: bytes, width: int, height: int) -> Image.Image:
    """Wrap already filtered scanlines as an 8 bit grayscale PNG.

    Compression is turned off because the only reader is Pillow, a few lines
    below, and deflating bytes so they can be inflated again would be the
    slowest part of decoding.
    """
    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    png = b"".join((
        b"\x89PNG\r\n\x1a\n",
        chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)),
        chunk(b"IDAT", zlib.compress(scanlines, 0)),
        chunk(b"IEND", b""),
    ))
    image = Image.open(BytesIO(png))
    image.load()
    return image
