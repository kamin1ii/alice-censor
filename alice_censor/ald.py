"""Reader and writer for ALD archives.

ALD is the archive format AliceSoft used from System 3.5 onward, and it is
the one alice-tools can read but cannot write. `ar_pack` refuses any output
that is not .afa (src/core/ar/pack.c) and `write_afa.c` is the only archive
writer in that codebase, so repacking an .ald needs its own implementation.
This is it.

Layout, per https://haniwa.technology/tech/ald.html and confirmed byte for
byte against a real 134 MB Rance 02 archive.

Everything is measured in 256-byte sectors.

    pointer table    LE24 sector numbers, starting at offset 0
    link table       3 bytes per file index
    data             one entry per pointer, sector aligned
    trailer          a few bytes carrying a version number

The pointer table's first element is the size of the pointer table itself,
and its second element is the first sector after the link table, which is
also where the data starts. Every element after that is the start of one
entry, and an entry's length runs to the next element, so a table for N
entries holds N+2 elements. Trailing elements are zero.

The link table is what the game indexes into. Element i describes file
number i as a 1-based volume number and a 1-based index into the pointer
table, with an all-zero element meaning no file has that number. It is
sparse and mostly empty in practice, and those gaps are load bearing, since
the game asks for files by number.

Each entry begins with a header.

    0    LE32   header size, which is also the offset from the header to
                the data, so the data begins at entry_start + header_size
    4    LE32   data size in bytes
    8    LE32   timestamp, low half of a Windows FILETIME
    12   LE32   timestamp, high half
    16+  bytes  NUL-terminated name, padded so the header is at least 32

Names are Shift-JIS on disk. Volume splitting (fooA.ald, fooB.ald) exists
in the format but is not written here, since a censor repack rewrites one
archive in place and splitting it would change what the game looks for.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

SECTOR = 256
HEADER_MIN = 32
NAME_ENCODING = "cp932"

# Written into the trailer when an archive is built from scratch rather
# than rebuilt from an existing one. Observed in the wild alongside
# 0x012020, whose difference is undocumented, so a rebuild always reuses
# whatever the original carried instead of assuming this one.
DEFAULT_VERSION = 0x014C4E


class AldError(ValueError):
    """Raised when an ALD file cannot be parsed or cannot be built."""


@dataclass
class AldEntry:
    """One file in an ALD archive.

    `index` is the file number the game uses to ask for it, and it must
    survive a rebuild unchanged. `timestamp` is a Windows FILETIME, kept
    only so a rebuilt archive stays as close to the original as possible.
    """

    index: int
    name: str
    data: bytes
    timestamp: int = 0


@dataclass
class AldArchive:
    entries: list[AldEntry] = field(default_factory=list)
    # Kept verbatim from the source archive so a rebuild reproduces it
    # exactly. See the module docstring on the version trailer.
    trailer: bytes = b""

    def by_index(self) -> dict[int, AldEntry]:
        return {entry.index: entry for entry in self.entries}


def _get_le24(buf: bytes, offset: int) -> int:
    return buf[offset] | (buf[offset + 1] << 8) | (buf[offset + 2] << 16)


def _put_le24(value: int) -> bytes:
    if not 0 <= value < 1 << 24:
        raise AldError(f"value {value} does not fit in a 24-bit field")
    return bytes((value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF))


def _sectors(n_bytes: int) -> int:
    return (n_bytes + SECTOR - 1) // SECTOR


def read_ald(path: str | Path) -> AldArchive:
    """Parse an ALD archive.

    Reads the whole file into memory. These run to a few hundred MB, which
    is worth it to keep every entry's bytes available for a rebuild that
    copies untouched files through without re-encoding them.
    """
    path = Path(path)
    data = path.read_bytes()
    if len(data) < 6:
        raise AldError(f"{path}: too short to be an ALD archive")

    ptr_sectors = _get_le24(data, 0)
    link_end_sectors = _get_le24(data, 3)
    map_sectors = link_end_sectors - ptr_sectors
    if ptr_sectors <= 0 or map_sectors <= 0:
        # libsys4 has a heuristic for archives whose first three bytes have
        # been offset by a per-game constant. Nothing that ships as .ald
        # for the games this tool targets uses it, and guessing wrong would
        # corrupt an archive silently, so this refuses instead.
        raise AldError(
            f"{path}: implausible table sizes (pointer={ptr_sectors}, link={map_sectors}). "
            "This archive may use the obfuscated header variant, which is not supported."
        )

    ptr_table_end = ptr_sectors * SECTOR
    link_table = data[ptr_table_end : link_end_sectors * SECTOR]

    n_slots = ptr_table_end // 3
    pointers = [_get_le24(data, i * 3 + 3) * SECTOR for i in range(n_slots - 1)]

    archive = AldArchive()
    last_data_end = 0
    for i in range(len(link_table) // 3):
        volume = link_table[i * 3]
        if volume == 0:
            continue  # no file has this number
        ptr_index = (link_table[i * 3 + 1] | (link_table[i * 3 + 2] << 8)) - 1
        if ptr_index < 0 or ptr_index + 1 >= len(pointers):
            raise AldError(f"{path}: file {i} points outside the pointer table")

        start = pointers[ptr_index]
        if start == 0:
            continue
        header_size, size, time_low, time_high = struct.unpack_from("<IIII", data, start)
        if header_size < HEADER_MIN:
            raise AldError(f"{path}: file {i} has a {header_size}-byte header, expected 32 or more")
        raw_name = data[start + 16 : start + header_size].split(b"\0", 1)[0]
        payload_start = start + header_size
        archive.entries.append(
            AldEntry(
                index=i,
                name=raw_name.decode(NAME_ENCODING, errors="replace"),
                data=data[payload_start : payload_start + size],
                timestamp=(time_high << 32) | time_low,
            )
        )
        last_data_end = max(last_data_end, payload_start + size)

    # Whatever follows the final entry, rounded up to its sector, is the
    # version trailer. Preserved rather than regenerated.
    archive.trailer = data[_sectors(last_data_end) * SECTOR :]
    return archive


def _build_entry_block(entry: AldEntry) -> bytes:
    """One entry's header and data, padded out to a sector boundary."""
    raw_name = entry.name.encode(NAME_ENCODING) + b"\0"
    header_size = max(HEADER_MIN, 16 + len(raw_name))
    # alice-tools reads the name as the whole span from 16 to header_size,
    # so any slack has to be NUL rather than left uninitialised.
    header = struct.pack(
        "<IIII",
        header_size,
        len(entry.data),
        entry.timestamp & 0xFFFFFFFF,
        (entry.timestamp >> 32) & 0xFFFFFFFF,
    ) + raw_name.ljust(header_size - 16, b"\0")
    block = header + entry.data
    padding = (-len(block)) % SECTOR
    return block + b"\0" * padding


def write_ald(path: str | Path, archive: AldArchive) -> None:
    """Write `archive` out as a single-volume ALD file.

    Entries keep the file numbers they came in with, so a rebuilt archive
    answers the same lookups the game already makes. Writing is atomic via
    a temp file and a replace, because the target is usually the user's
    only copy of a game archive.
    """
    entries = sorted(archive.entries, key=lambda e: e.index)
    if not entries:
        raise AldError("refusing to write an ALD archive with no entries")
    seen = {e.index for e in entries}
    if len(seen) != len(entries):
        raise AldError("two entries share a file number")

    blocks = [_build_entry_block(entry) for entry in entries]

    # The pointer table holds one element for its own size, one for the
    # start of the data, one per entry after the first, and one terminator.
    # See the module docstring.
    n_elements = len(entries) + 2
    ptr_sectors = _sectors(n_elements * 3)
    map_sectors = _sectors((entries[-1].index + 1) * 3)
    data_start = (ptr_sectors + map_sectors) * SECTOR

    offsets = []
    cursor = data_start
    for block in blocks:
        offsets.append(cursor)
        cursor += len(block)

    ptr_table = bytearray(ptr_sectors * SECTOR)
    ptr_table[0:3] = _put_le24(ptr_sectors)
    for i, offset in enumerate([*offsets, cursor]):
        ptr_table[(i + 1) * 3 : (i + 2) * 3] = _put_le24(offset // SECTOR)

    link_table = bytearray(map_sectors * SECTOR)
    for ptr_index, entry in enumerate(entries):
        slot = entry.index * 3
        link_table[slot] = 1  # single volume, 1-indexed
        link_table[slot + 1] = (ptr_index + 1) & 0xFF
        link_table[slot + 2] = ((ptr_index + 1) >> 8) & 0xFF

    trailer = archive.trailer or _put_le24(DEFAULT_VERSION) + b"\0"

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(ptr_table)
            f.write(link_table)
            for block in blocks:
                f.write(block)
            f.write(trailer)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
