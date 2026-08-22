"""Reader for AFA archives, the AlicArch container alice-tools calls .afa.

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

This module reads. Writing is a separate job, because an archive is large
enough that rebuilding one has to stream rather than sit in memory.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
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
