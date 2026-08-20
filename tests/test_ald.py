"""Tests for the ALD reader and writer.

The format is documented at https://haniwa.technology/tech/ald.html and
these were checked against a real 134 MB Rance 02 archive, which reads and
rewrites byte for byte. What is tested here is the shape of the format, so
the fixtures are built by write_ald and read back rather than checked in as
binary blobs.
"""

import struct

import pytest

from alice_censor.ald import (
    SECTOR,
    AldArchive,
    AldEntry,
    AldError,
    read_ald,
    write_ald,
)


def _archive(*entries: AldEntry, trailer: bytes = b"") -> AldArchive:
    return AldArchive(entries=list(entries), trailer=trailer)


def test_round_trip_preserves_everything_that_matters(tmp_path):
    original = _archive(
        AldEntry(index=0, name="cg00001.QNT", data=b"QNT\0first", timestamp=132321283087235886),
        AldEntry(index=10, name="cg00011.AJP", data=b"AJP\0second", timestamp=1),
        AldEntry(index=37218, name="cg37219.QNT", data=b"QNT\0last", timestamp=0),
    )
    path = tmp_path / "gameA.ald"
    write_ald(path, original)

    back = read_ald(path)
    assert [e.index for e in back.entries] == [0, 10, 37218]
    assert [e.name for e in back.entries] == ["cg00001.QNT", "cg00011.AJP", "cg37219.QNT"]
    assert [e.data for e in back.entries] == [b"QNT\0first", b"AJP\0second", b"QNT\0last"]
    assert [e.timestamp for e in back.entries] == [132321283087235886, 1, 0]


def test_writing_twice_is_deterministic(tmp_path):
    archive = _archive(AldEntry(index=3, name="a.QNT", data=b"x" * 500))
    first, second = tmp_path / "aA.ald", tmp_path / "bA.ald"
    write_ald(first, archive)
    write_ald(second, archive)
    assert first.read_bytes() == second.read_bytes()


def test_file_numbers_are_preserved_exactly(tmp_path):
    """The game asks for files by number, and the link table is sparse, so
    a gap is meaningful and must survive a rebuild."""
    archive = _archive(
        AldEntry(index=0, name="a.QNT", data=b"a"),
        AldEntry(index=5000, name="b.QNT", data=b"b"),
    )
    path = tmp_path / "gapA.ald"
    write_ald(path, archive)

    back = read_ald(path)
    assert {e.index: e.name for e in back.entries} == {0: "a.QNT", 5000: "b.QNT"}


def test_entries_are_sector_aligned(tmp_path):
    archive = _archive(
        AldEntry(index=0, name="a.QNT", data=b"a" * 300),
        AldEntry(index=1, name="b.QNT", data=b"b" * 7),
    )
    path = tmp_path / "alignA.ald"
    write_ald(path, archive)
    raw = path.read_bytes()

    get3 = lambda o: raw[o] | (raw[o + 1] << 8) | (raw[o + 2] << 16)  # noqa: E731
    assert get3(0) >= 1  # pointer table occupies at least one sector

    # Data start, second entry and the terminator are all sector numbers,
    # so every entry begins on a sector boundary by construction. The
    # terminator marks where the data ends and the version trailer begins.
    data_start, second, terminator = (get3(i * 3) for i in (1, 2, 3))
    assert data_start < second < terminator
    assert terminator * SECTOR == len(raw) - len(read_ald(path).trailer)


def test_header_is_at_least_32_bytes_even_for_short_names(tmp_path):
    """libsys4 reads the name as the whole span from offset 16 to the
    header size, so a short name still needs the minimum header."""
    path = tmp_path / "shortA.ald"
    write_ald(path, _archive(AldEntry(index=0, name="a.Q", data=b"x")))
    raw = path.read_bytes()
    get3 = lambda o: raw[o] | (raw[o + 1] << 8) | (raw[o + 2] << 16)  # noqa: E731
    entry_start = get3(3) * SECTOR
    header_size, size = struct.unpack_from("<II", raw, entry_start)
    assert header_size >= 32
    assert size == 1


def test_trailer_is_preserved(tmp_path):
    """The last sector carries a version number whose meaning is
    undocumented, so a rebuild reuses whatever the original had."""
    trailer = bytes.fromhex("4e4c010010000000 01d2060000000000".replace(" ", ""))
    path = tmp_path / "verA.ald"
    write_ald(path, _archive(AldEntry(index=0, name="a.QNT", data=b"x"), trailer=trailer))
    assert read_ald(path).trailer == trailer


def test_non_ascii_names_round_trip_as_shift_jis(tmp_path):
    path = tmp_path / "sjisA.ald"
    write_ald(path, _archive(AldEntry(index=0, name="立ち絵.QNT", data=b"x")))
    assert read_ald(path).entries[0].name == "立ち絵.QNT"


def test_empty_archive_is_refused(tmp_path):
    with pytest.raises(AldError):
        write_ald(tmp_path / "emptyA.ald", AldArchive())


def test_duplicate_file_numbers_are_refused(tmp_path):
    archive = _archive(
        AldEntry(index=7, name="a.QNT", data=b"a"),
        AldEntry(index=7, name="b.QNT", data=b"b"),
    )
    with pytest.raises(AldError):
        write_ald(tmp_path / "dupA.ald", archive)


def test_truncated_file_is_refused(tmp_path):
    path = tmp_path / "shortA.ald"
    path.write_bytes(b"\x01\x02")
    with pytest.raises(AldError):
        read_ald(path)


def test_implausible_tables_are_refused_rather_than_guessed(tmp_path):
    """libsys4 has a heuristic for archives with an offset header. Guessing
    wrong would corrupt an archive silently, so this refuses instead."""
    path = tmp_path / "oddA.ald"
    path.write_bytes(b"\xff\xff\xff\x01\x00\x00" + b"\0" * 512)
    with pytest.raises(AldError, match="obfuscated|implausible"):
        read_ald(path)


def test_failed_write_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    path = tmp_path / "failA.ald"
    archive = _archive(AldEntry(index=0, name="a.QNT", data=b"x"))

    import alice_censor.ald as ald_module

    def boom(entry):
        raise OSError("disk full")

    monkeypatch.setattr(ald_module, "_build_entry_block", boom)
    with pytest.raises(OSError):
        write_ald(path, archive)
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []
