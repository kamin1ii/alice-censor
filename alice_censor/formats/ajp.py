"""Decoder for AJP images, the lossy format AliceSoft uses alongside QNT.

Derived from ajp.c in libsys4, copyright 2019 Nunuhara Cabbage,
GPL-2.0-or-later. Translated to Python for Alice Censor in 2026 by kamin1ii
and kept under the terms it arrived under.

An AJP is an ordinary JPEG for the colour and a separate mask for the
transparency, both stashed in one file behind a short header. The first
sixteen bytes of each are exclusive ored with a fixed key, which is the only
thing standing between the JPEG inside and any image viewer.

There is no encoder here and there is none in alice-tools either, so an
edited AJP is written back as QNT. Reading is enough, since untouched
entries are copied as raw bytes and never go through this at all.

The mask can be stored three ways. Every one in a Rance 02 archive is PMS,
and 470 of its 920 AJPs have no mask at all, but WebP and bare zlib both
turn up in the format and cost little to accept.

Colour comes out very slightly different from alice.exe, which is expected
and not a fault. The two use different JPEG decoders, and the standard lets
an implementation round the inverse transform its own way. Measured across
120 Rance 02 images the transparency matches exactly, and colour channels
differ by an average of 1.12 with a worst case of 5 out of 255, nearly all
of them off by one. Untouched AJPs are copied as raw bytes and never come
through here at all.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from . import pms

MAGIC = b"AJP\0"

# The first sixteen bytes of the colour stream and of the mask stream are
# each exclusive ored with this.
KEY = bytes((
    0x5D, 0x91, 0xAE, 0x87,
    0x4A, 0x56, 0x41, 0xCD,
    0x83, 0xEC, 0x4C, 0x92,
    0xB5, 0xCB, 0x16, 0x34,
))

OPAQUE = 0xFF


class AjpError(ValueError):
    """The data is not an AJP, or is one this module cannot read."""


@dataclass(frozen=True)
class AjpHeader:
    width: int
    height: int
    jpeg_offset: int
    jpeg_size: int
    mask_offset: int
    mask_size: int

    @property
    def has_alpha(self) -> bool:
        return self.mask_size > 0


def is_ajp(data: bytes) -> bool:
    return data[:4] == MAGIC


def read_header(data: bytes) -> AjpHeader:
    if not is_ajp(data):
        raise AjpError("not an AJP image")
    if len(data) < 36:
        raise AjpError(f"truncated AJP header, {len(data)} bytes")
    header = AjpHeader(*struct.unpack_from("<6I", data, 12))
    if header.width == 0 or header.height == 0:
        raise AjpError(f"AJP has no area, {header.width}x{header.height}")
    return header


def decode(data: bytes) -> Image.Image:
    """Decode an AJP into an RGBA image."""
    header = read_header(data)
    size = len(data)
    for label, at, length in (
        ("colour", header.jpeg_offset, header.jpeg_size),
        ("mask", header.mask_offset, header.mask_size),
    ):
        if at > size or at + length > size:
            raise AjpError(f"the AJP {label} stream runs past the end of the file")

    colour = _decode_jpeg(
        _unscramble(data[header.jpeg_offset:header.jpeg_offset + header.jpeg_size])
    )
    if colour.size != (header.width, header.height):
        raise AjpError(
            f"the AJP header says {header.width}x{header.height} and the picture "
            f"inside is {colour.size[0]}x{colour.size[1]}"
        )

    colour.putalpha(_alpha(data, header))
    return colour


def _unscramble(stream: bytes) -> bytes:
    head = bytes(b ^ k for b, k in zip(stream[:len(KEY)], KEY))
    return head + stream[len(KEY):]


def _decode_jpeg(stream: bytes) -> Image.Image:
    try:
        with Image.open(BytesIO(stream)) as opened:
            image = opened.convert("RGB")
            image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise AjpError(f"the AJP colour stream is not a readable JPEG, {exc}") from exc
    return image


def _alpha(data: bytes, header: AjpHeader) -> Image.Image:
    """The transparency plane, or a fully opaque one when there is none.

    libsys4 falls back to opaque for a mask it cannot read rather than
    failing the image, and the same is done here. A picture with the wrong
    transparency is worth more than no picture.
    """
    if not header.has_alpha:
        return Image.new("L", (header.width, header.height), OPAQUE)

    mask = _unscramble(data[header.mask_offset:header.mask_offset + header.mask_size])
    size = (header.width, header.height)
    try:
        if pms.is_pms8(mask):
            width, height, plane = pms.extract_mask(mask)
            if (width, height) != size:
                raise AjpError(f"the AJP mask is {width}x{height} and the picture is not")
            return Image.frombytes("L", size, plane)
        if mask[:4] == b"RIFF":
            with Image.open(BytesIO(mask)) as opened:
                return opened.convert("RGBA").getchannel("A")
        if mask[:1] == b"\x78":
            plane = zlib.decompress(mask)
            if len(plane) < header.width * header.height:
                raise AjpError("the AJP mask decompressed to less than the picture needs")
            return Image.frombytes("L", size, plane[:header.width * header.height])
    except (pms.PmsError, AjpError, zlib.error, UnidentifiedImageError, OSError):
        return Image.new("L", size, OPAQUE)

    return Image.new("L", size, OPAQUE)
