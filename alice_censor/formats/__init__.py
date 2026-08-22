"""Readers and writers for AliceSoft file formats.

This package talks to the formats directly rather than shelling out to
alice.exe. It imports nothing from the rest of Alice Censor so that it can
be lifted into a library of its own once it covers enough ground.

    QNT   read and write, lossless, what nearly every CG is stored as
    AJP   read only, a scrambled JPEG with a separate transparency mask
    DCF   read only, the chunks that differ from another image
    PMS   read only, the run length format an AJP mask uses
    AFA   read and write, the container alice-tools calls .afa
    ALD   read and write, the older container, sectors and a pointer table

Nothing writes AJP or DCF, because no encoder for either exists anywhere.
An edited image of either kind is written back as QNT.

What is not here is manifest.py, which parses the text file alice-tools uses
to describe a pack job. That is alice-tools' own format rather than one of
AliceSoft's, and this package is the AliceSoft side.

decode_image below is the one place that knows which format is which. Every
caller that has bytes and wants a picture should come through it rather than
testing magic numbers of its own.
"""

from __future__ import annotations

from typing import Callable

from PIL import Image

from . import ajp, dcf, qnt

__all__ = ["ajp", "dcf", "qnt", "can_decode", "decode_image", "describe"]


def can_decode(data: bytes) -> bool:
    """Whether these bytes are an image this package can read."""
    return qnt.is_qnt(data) or ajp.is_ajp(data) or dcf.is_dcf(data)


def describe(data: bytes) -> str:
    """What these bytes are, for putting in a message."""
    if qnt.is_qnt(data):
        return "qnt"
    if ajp.is_ajp(data):
        return "ajp"
    if dcf.is_dcf(data):
        return "dcf"
    return "unknown"


def decode_image(
    data: bytes,
    resolve_base: Callable[[str], Image.Image | None] | None = None,
) -> Image.Image:
    """Decode any image format this package reads, into RGBA.

    `resolve_base` is only used by DCF, which is stored as the difference
    from another image and needs that one to be complete. Without it a DCF
    decodes to the difference alone.
    """
    if qnt.is_qnt(data):
        return qnt.decode(data)
    if ajp.is_ajp(data):
        return ajp.decode(data)
    if dcf.is_dcf(data):
        return dcf.decode(data, resolve_base)
    raise ValueError("not an image format this build can read")
