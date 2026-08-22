"""Reader and writer for AFA archives, the AlicArch container alice-tools calls .afa.

Derived from afa.c in libsys4, copyright 2019 Nunuhara Cabbage, GPL-2.0-or-later.
Translated to Python and restructured for Alice Censor in 2026 by kamin1ii, and
kept under the terms it arrived under.

An AFA is a header, a zlib compressed table of where everything lives, and
then the file data laid end to end.

    offset 0    AFAH, then 0x1c, then the text AlicArch
    offset 16   version, either 1 or 2
    offset 24   where the data section starts
    offset 28   INFO, then the size of the compressed table
    offset 40   how many files there are
    offset 44   the compressed table itself

Entries in the table carry a name, an offset measured from the start of the
data section, and a length. Nothing is compressed except the table, so a
file's bytes can be read straight out of the archive.

Two details are easy to get wrong. Each name is written twice over, first its
real length and then the length it was padded to, so a reader that takes the
first number and runs will land in the middle of the next field. And whether
an entry carries an id at all cannot be read from the header, so the table
has to be walked once assuming it does, on the understanding that only the
right guess consumes the table exactly.

Writing streams rather than building the archive in memory, because a CG
archive runs to hundreds of megabytes. The cost is that each entry has to
declare its size before its bytes are asked for, since the table is written
first.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable, Sequence
from typing import BinaryIO

MAGIC = b"AFAH"
LABEL = b"AlicArch"
HEADER_SIZE = 44
NAME_ENCODING = "cp932"

# Everything after an entry's name, when the entry carries an id. Five 32 bit
# fields, being the id, two unknowns, the offset and the size.
_TAIL_WITH_ID = 20
_TAIL_WITHOUT_ID = 16


class AfaError(ValueError):
    """The file is not an AFA, or is one this module cannot read."""


@dataclass(frozen=True)
class AfaEntry:
    name: str
    number: int
    offset: int
    size: int
    unknown0: int = 0
    unknown1: int = 0
    # The name exactly as stored, padding included, so a rebuilt archive can
    # reproduce the table byte for byte rather than approximately.
    raw_name: bytes = b""


class AfaReader:
    """Open an AFA and read entries out of it one at a time.

    A CG archive runs to hundreds of megabytes, so the file stays on disk and
    only the table is held in memory. Use it as a context manager.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._file: BinaryIO | None = None
        self.version = 0
        self.header_unknown = 1
        self.data_start = 0
        self.data_size = 0
        self.has_ids = False
        self.entries: list[AfaEntry] = []
        self._open()

    def __enter__(self) -> AfaReader:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def _open(self) -> None:
        try:
            self._file = self.path.open("rb")
        except OSError as exc:
            raise AfaError(f"could not open {self.path.name}, {exc}") from exc
        try:
            self._read_metadata()
        except Exception:
            self.close()
            raise

    def _read_metadata(self) -> None:
        assert self._file is not None
        file_size = self.path.stat().st_size
        header = self._file.read(HEADER_SIZE)
        if len(header) < HEADER_SIZE:
            raise AfaError(f"{self.path.name} is too short to be an AFA")
        if header[0:4] != MAGIC:
            raise AfaError(f"{self.path.name} is not an AFA archive")
        if header[8:16] != LABEL:
            if struct.unpack_from("<I", header, 8)[0] == 3:
                raise AfaError("AFA version 3 archives are not supported yet")
            raise AfaError(f"{self.path.name} has an AlicArch label this cannot read")
        if header[28:32] != b"INFO" or struct.unpack_from("<I", header, 4)[0] != 0x1C:
            raise AfaError(f"{self.path.name} has a damaged header")

        self.version = struct.unpack_from("<I", header, 16)[0]
        # A field with no known meaning, one in every archive seen so far.
        # Carried rather than assumed, so a rebuild cannot quietly change it.
        self.header_unknown = struct.unpack_from("<I", header, 20)[0]
        self.data_start = struct.unpack_from("<I", header, 24)[0]
        table_size = struct.unpack_from("<I", header, 32)[0] - 16
        table_unpacked = struct.unpack_from("<I", header, 36)[0]
        count = struct.unpack_from("<I", header, 40)[0]

        if self.data_start + 8 >= file_size:
            raise AfaError("the data section starts past the end of the file")
        if table_size <= 0 or table_unpacked <= 0:
            raise AfaError("the file table has no size")

        self._file.seek(self.data_start)
        marker = self._file.read(8)
        if marker[0:4] != b"DATA":
            raise AfaError("the data section does not start with DATA")
        self.data_size = struct.unpack_from("<I", marker, 4)[0]
        if self.data_start + self.data_size > file_size:
            raise AfaError("the data section runs past the end of the file")

        self._file.seek(HEADER_SIZE)
        packed = self._file.read(table_size)
        try:
            table = zlib.decompress(packed)
        except zlib.error as exc:
            raise AfaError(f"the file table did not decompress, {exc}") from exc
        if len(table) < table_unpacked:
            raise AfaError("the file table is shorter than the header claims")

        self.has_ids = self.version == 1 or _table_has_ids(table[:table_unpacked], count)
        self.entries = _read_table(table[:table_unpacked], count, self.has_ids)

    def read(self, entry: AfaEntry) -> bytes:
        """Pull one entry's bytes out of the archive."""
        if self._file is None:
            raise AfaError("the archive is closed")
        self._file.seek(self.data_start + entry.offset)
        data = self._file.read(entry.size)
        if len(data) != entry.size:
            raise AfaError(
                f"{entry.name} is truncated, wanted {entry.size} bytes "
                f"and got {len(data)}"
            )
        return data

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)


def _table_has_ids(table: bytes, count: int) -> bool:
    """Walk the table assuming ids are present and see whether it fits.

    An entry is a name and then either four or five fields, and the header
    does not say which. Guessing wrong leaves the walk somewhere other than
    the end, so consuming the table exactly is the test.
    """
    at = 0
    end = len(table)
    for _ in range(count):
        if end - at < 8:
            return False
        padded = struct.unpack_from("<I", table, at + 4)[0]
        at += 8 + padded + _TAIL_WITH_ID
        if at > end:
            return False
    return at == end


def _read_table(table: bytes, count: int, has_ids: bool) -> list[AfaEntry]:
    entries: list[AfaEntry] = []
    at = 0
    end = len(table)
    tail = _TAIL_WITH_ID if has_ids else _TAIL_WITHOUT_ID
    for index in range(count):
        if end - at < 8:
            raise AfaError(f"the file table ran out after {index} of {count} entries")
        real, padded = struct.unpack_from("<2I", table, at)
        at += 8
        if end - at < padded + tail:
            raise AfaError(f"entry {index} of {count} runs past the end of the table")
        raw_name = table[at:at + padded]
        at += padded

        if has_ids:
            number = struct.unpack_from("<i", table, at)[0] - 1
            at += 4
            if number < 0:
                number = index
        else:
            number = index
        unknown0, unknown1, offset, size = struct.unpack_from("<4I", table, at)
        at += 16

        entries.append(AfaEntry(
            name=_decode_name(raw_name[:real]),
            number=number,
            offset=offset,
            size=size,
            unknown0=unknown0,
            unknown1=unknown1,
            raw_name=raw_name,
        ))
    return entries


def _decode_name(raw: bytes) -> str:
    """Names are Shift JIS, and a bad byte should not lose the whole archive."""
    return raw.decode(NAME_ENCODING, errors="replace").replace("\\", "/")


# ===== writing
#
# Derived from write_afa.c in alice-tools, copyright 2019 Nunuhara Cabbage,
# GPL-2.0-or-later, with the layout choices checked against a real archive
# rather than taken from it. The two disagree in four places, and the real
# archive wins each time, because rebuilding one has to come out byte for
# byte identical when nothing was edited.
#
#   alice-tools rounds every entry up to eight bytes. AliceSoft packs them
#   end to end with no padding at all.
#   alice-tools writes zero for the two unknown fields in each entry, which
#   throws away whatever the original held. Those are carried across here.
#   alice-tools writes no padding after a name. AliceSoft pads every one out
#   to a multiple of four, adding four when it is already there.
#   alice-tools fills the gap before the data with zeroes. AliceSoft fills
#   it with 0xFF.

DUMMY_CHUNK = b"DUMM"
DEFAULT_FILLER = 0xFF
DEFAULT_ALIGNMENT = 0x1000
NAME_PADDING = 4
TABLE_LEVEL = 9


@dataclass(frozen=True)
class OutgoingEntry:
    """One file on its way into an archive.

    The bytes arrive through a callable rather than as a value so that
    rebuilding a large archive does not need it all in memory at once. The
    size has to be known in advance because the table is written before the
    data, and read is called exactly once, after the table is already down.
    """
    name: str
    size: int
    read: Callable[[], bytes]
    raw_name: bytes = b""
    unknown0: int = 0
    unknown1: int = 0
    number: int = 0


def copy_of(reader: AfaReader, entry: AfaEntry) -> OutgoingEntry:
    """An entry passed through from one archive to another, untouched."""
    return OutgoingEntry(
        name=entry.name,
        size=entry.size,
        read=lambda: reader.read(entry),
        raw_name=entry.raw_name,
        unknown0=entry.unknown0,
        unknown1=entry.unknown1,
        number=entry.number,
    )


def replacement_for(entry: AfaEntry, data: bytes) -> OutgoingEntry:
    """An entry whose contents changed but which keeps its place and name."""
    return OutgoingEntry(
        name=entry.name,
        size=len(data),
        read=lambda: data,
        raw_name=entry.raw_name,
        unknown0=entry.unknown0,
        unknown1=entry.unknown1,
        number=entry.number,
    )


def write_afa(
    path: str | Path,
    entries: Sequence[OutgoingEntry],
    *,
    version: int = 2,
    has_ids: bool = False,
    data_start: int | None = None,
    alignment: int = DEFAULT_ALIGNMENT,
    entry_alignment: int = 1,
    filler: int = DEFAULT_FILLER,
    header_unknown: int = 1,
) -> None:
    """Write an AFA archive.

    Pass data_start to put the data section exactly where another archive
    had it. Left alone it is rounded up to the next boundary, which is what
    alice-tools does and what ALDExplorer needs to open the result.
    """
    if version not in (1, 2):
        raise AfaError(f"cannot write AFA version {version}")
    if not entries:
        raise AfaError("an archive needs at least one file")

    table, offsets = _build_table(entries, has_ids or version == 1, entry_alignment)
    packed = zlib.compress(table, TABLE_LEVEL)

    table_end = HEADER_SIZE + len(packed)
    if data_start is None:
        data_start = (table_end + alignment - 1) & ~(alignment - 1)
    if data_start < table_end:
        raise AfaError(
            f"the data section cannot start at {data_start}, the table ends at {table_end}"
        )

    last = offsets[-1] + _round_up(entries[-1].size, entry_alignment)
    header = bytearray(HEADER_SIZE)
    header[0:4] = MAGIC
    struct.pack_into("<I", header, 4, 0x1C)
    header[8:16] = LABEL
    struct.pack_into("<3I", header, 16, version, header_unknown, data_start)
    header[28:32] = b"INFO"
    struct.pack_into("<3I", header, 32, len(packed) + 16, len(table), len(entries))

    path = Path(path)
    with path.open("wb") as out:
        out.write(header)
        out.write(packed)
        _write_gap(out, data_start - table_end, filler)
        out.write(b"DATA" + struct.pack("<I", last))
        for entry, offset in zip(entries, offsets):
            data = entry.read()
            if len(data) != entry.size:
                raise AfaError(
                    f"{entry.name} said it was {entry.size} bytes and gave {len(data)}"
                )
            out.write(data)
            # Padding between entries is zero rather than the gap filler,
            # and with the default alignment there is none of it at all.
            out.write(bytes(_round_up(entry.size, entry_alignment) - entry.size))


def _build_table(
    entries: Sequence[OutgoingEntry], has_ids: bool, entry_alignment: int
) -> tuple[bytes, list[int]]:
    table = bytearray()
    offsets: list[int] = []
    # The DATA marker sits at the front of the data section, so the first
    # file starts eight bytes into it.
    offset = 8
    for index, entry in enumerate(entries):
        raw = entry.raw_name or _pad_name(entry.name)
        real = len(_encode_name(entry.name))
        if real > len(raw):
            raise AfaError(f"the stored name for {entry.name} is shorter than the name")
        table += struct.pack("<2I", real, len(raw)) + raw
        if has_ids:
            table += struct.pack("<i", entry.number + 1)
        table += struct.pack("<4I", entry.unknown0, entry.unknown1, offset, entry.size)
        offsets.append(offset)
        offset += _round_up(entry.size, entry_alignment)
    return bytes(table), offsets


def _write_gap(out: BinaryIO, pad: int, filler: int) -> None:
    """Fill the space between the table and the data section.

    Anything from eight bytes up gets a marked chunk, so a reader walking
    the file sees a named region rather than a stretch of nothing.
    """
    if pad <= 0:
        return
    if pad >= 8:
        out.write(DUMMY_CHUNK + struct.pack("<I", pad))
        pad -= 8
    out.write(bytes([filler]) * pad)


def _encode_name(name: str) -> bytes:
    return name.replace("/", "\\").encode(NAME_ENCODING)


def _pad_name(name: str) -> bytes:
    """Names are padded out to a multiple of four, always by at least one."""
    raw = _encode_name(name)
    return raw + b"\0" * (NAME_PADDING - len(raw) % NAME_PADDING)


def _round_up(value: int, to: int) -> int:
    return value if to <= 1 else (value + to - 1) & ~(to - 1)
