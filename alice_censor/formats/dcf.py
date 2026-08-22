"""Decoder for DCF, an image stored as the difference from another image.

Derived from dcf.c in libsys4, copyright Nunuhara Cabbage, GPL-2.0-or-later.
Translated to Python for Alice Censor in 2026 by kamin1ii and kept under the
terms it arrived under.

A DCF is not a picture on its own. It names another image in the same
archive, carries a map saying which sixteen by sixteen chunks of that image
changed, and carries a full size picture holding the new chunks. Decoding
means loading the base, then pasting in every chunk the map marks.

    dcf     the size and the name of the base image
    dfdl    the chunk map, zlib compressed
    dcgd    a QNT the size of the whole image, holding the changed chunks

Two things are easy to get backwards. A zero in the map means take that
chunk from the diff, not from the base. And the name of the base image is
stored with every byte rotated left, by an amount worked out from the length
of the name itself.

There is no encoder here on purpose. Writing a DCF back is what turned the
Rance 03 main menu into a black screen, because the encoder blanks the
chunks that match the base and takes their transparency to zero along with
everything else. An edited DCF is written out as a whole QNT instead, which
costs some space and nothing else.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Callable

from PIL import Image

from . import qnt

MAGIC = b"dcf "
CHUNK_MAP = b"dfdl"
DIFF_IMAGE = b"dcgd"

CHUNK = 16
SUPPORTED_VERSION = 1
SUPPORTED_BPP = 32

# Ceilings the format itself does not state, taken from libsys4, which uses
# them to reject nonsense before allocating on the strength of it.
MAX_HEADER = 4096
MAX_CHUNK_MAP_PACKED = 10000
MAX_CHUNK_MAP = 40000
MAX_NAME = 2000

NAME_ENCODING = "cp932"


class DcfError(ValueError):
    """The data is not a DCF, or is one this module cannot read."""


@dataclass(frozen=True)
class DcfHeader:
    width: int
    height: int
    bpp: int
    base_name: str


def is_dcf(data: bytes) -> bool:
    return data[:4] == MAGIC


def read_header(data: bytes) -> DcfHeader:
    header, _ = _read_header(data)
    return header


def base_name(data: bytes) -> str:
    """Which image this one is a difference from."""
    return read_header(data).base_name


def decode(data: bytes, resolve_base: Callable[[str], Image.Image | None] | None = None) -> Image.Image:
    """Decode a DCF into an RGBA image.

    `resolve_base` is handed the name of the base image and should return it
    decoded, or None if it cannot be found. Without a base, or with one that
    cannot be found, the diff is returned on its own. That is what libsys4
    does, and it is more useful than nothing, since the changed chunks are
    the part somebody wanted to look at.
    """
    header, at = _read_header(data)
    chunks, at = _read_chunk_map(data, at)
    diff = qnt.decode(_read_diff(data, at))

    if diff.size != (header.width, header.height):
        raise DcfError(
            f"the DCF header says {header.width}x{header.height} and the picture "
            f"inside is {diff.size[0]}x{diff.size[1]}"
        )

    base = resolve_base(header.base_name) if resolve_base else None
    if base is None:
        return diff
    if base.size != diff.size:
        raise DcfError(
            f"the base image {header.base_name} is {base.size[0]}x{base.size[1]} "
            f"and this difference is {diff.size[0]}x{diff.size[1]}"
        )

    return _apply(base.convert("RGBA"), diff, chunks, header.width, header.height)


def _read_header(data: bytes) -> tuple[DcfHeader, int]:
    if not is_dcf(data):
        raise DcfError("not a DCF image")
    if len(data) < 12:
        raise DcfError(f"truncated DCF header, {len(data)} bytes")

    size = struct.unpack_from("<i", data, 4)[0]
    if not 0 <= size <= MAX_HEADER:
        raise DcfError(f"the DCF header claims to be {size} bytes")
    end = 8 + size

    version, width, height, bpp, name_length = struct.unpack_from("<5i", data, 8)
    if version != SUPPORTED_VERSION:
        raise DcfError(f"unsupported DCF version {version}")
    if bpp != SUPPORTED_BPP:
        raise DcfError(f"unsupported bits per pixel in a DCF, {bpp}")
    if width <= 0 or height <= 0:
        raise DcfError(f"DCF has no area, {width}x{height}")
    if not 0 <= name_length <= MAX_NAME:
        raise DcfError(f"the DCF base image name claims to be {name_length} bytes")
    if 28 + name_length > len(data):
        raise DcfError("the DCF base image name runs past the end of the file")

    name = _unrotate(data[28:28 + name_length])
    # The header can carry more than it needs to. Trust its own length.
    return DcfHeader(width, height, bpp, name), end


def _unrotate(raw: bytes) -> str:
    """Undo the rotation the base image name is stored with.

    Every byte is rotated left by an amount taken from the length of the
    name, which makes it unreadable in a hex editor and nothing more.
    """
    if not raw:
        return ""
    rot = (len(raw) % 7) + 1
    turned = bytes(((b << rot) | (b >> (8 - rot))) & 0xFF for b in raw)
    return turned.decode(NAME_ENCODING, errors="replace").replace("\\", "/")


def _read_chunk_map(data: bytes, at: int) -> tuple[bytes, int]:
    if data[at:at + 4] != CHUNK_MAP:
        raise DcfError("expected a dfdl section after the DCF header")
    size = struct.unpack_from("<i", data, at + 4)[0]
    if not 0 <= size <= MAX_CHUNK_MAP_PACKED:
        raise DcfError(f"the DCF chunk map claims to be {size} bytes")
    end = at + 8 + size

    unpacked_size = struct.unpack_from("<I", data, at + 8)[0]
    if unpacked_size > MAX_CHUNK_MAP:
        raise DcfError(f"the DCF chunk map unpacks to {unpacked_size} bytes")
    try:
        chunks = zlib.decompress(data[at + 12:at + 8 + size])
    except zlib.error as exc:
        raise DcfError(f"the DCF chunk map did not decompress, {exc}") from exc
    if len(chunks) < unpacked_size:
        raise DcfError("the DCF chunk map is shorter than it claims")

    chunks = chunks[:unpacked_size]
    # The map opens with its own length, counted from after that field.
    if len(chunks) < 4 or struct.unpack_from("<I", chunks, 0)[0] != len(chunks) - 4:
        raise DcfError("the DCF chunk map disagrees with its own length")
    return chunks[4:], end


def _read_diff(data: bytes, at: int) -> bytes:
    if data[at:at + 4] != DIFF_IMAGE:
        raise DcfError("expected a dcgd section after the DCF chunk map")
    size = struct.unpack_from("<i", data, at + 4)[0]
    if size < 0 or at + 8 + size > len(data):
        raise DcfError(f"the DCF difference image claims to be {size} bytes")
    return data[at + 8:at + 8 + size]


def _apply(base: Image.Image, diff: Image.Image, chunks: bytes,
           width: int, height: int) -> Image.Image:
    """Paste every changed chunk of the difference onto the base.

    A zero in the map means this chunk changed. Anything else means the base
    already has it. The diff is the full size of the image, so a chunk is
    copied from the same place it is going to.
    """
    out = base.copy()
    across = width // CHUNK
    down = height // CHUNK

    for index, keep_base in enumerate(chunks[:across * down]):
        if keep_base:
            continue
        x = (index % across) * CHUNK
        y = (index // across) * CHUNK
        box = (x, y, x + CHUNK, y + CHUNK)
        out.paste(diff.crop(box), box)

    # Pixels past the last whole chunk are not in the map at all and always
    # come from the difference.
    spare_width = width % CHUNK
    spare_height = height % CHUNK
    if spare_width:
        box = (across * CHUNK, 0, width, height)
        out.paste(diff.crop(box), box)
    if spare_height:
        box = (0, down * CHUNK, width, height)
        out.paste(diff.crop(box), box)

    return out
