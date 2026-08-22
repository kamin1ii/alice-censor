"""Reading AFA archives without alice.exe.

Archives here are built by the helper below rather than committed as
fixtures, both because a real one is hundreds of megabytes and because
game data does not belong in the repository.
"""

import struct
import zlib

import pytest

from alice_censor.formats.afa import (
    AfaError,
    AfaReader,
    OutgoingEntry,
    copy_of,
    replacement_for,
    write_afa,
)


def make_afa(files, *, version=2, with_ids=None, pad_names=0, data_start=None,
             overstate_last_by=0):
    """Build an AFA holding these (name, data) pairs.

    Transcribed from the layout afa.c reads, which is GPL-2.0-or-later,
    copyright 2019 Nunuhara Cabbage.
    """
    if with_ids is None:
        with_ids = version == 1

    table = bytearray()
    body = bytearray()
    for index, (name, data) in enumerate(files):
        raw = name.encode("cp932")
        padded = raw + b"\0" * pad_names
        table += struct.pack("<2I", len(raw), len(padded)) + padded
        if with_ids:
            table += struct.pack("<i", index + 1)
        # Offsets are measured from the start of the data section, and the
        # DATA marker itself occupies the first eight bytes of it.
        claimed = len(data)
        if overstate_last_by and index == len(files) - 1:
            claimed += overstate_last_by
        table += struct.pack("<4I", 0, 0, 8 + len(body), claimed)
        body += data

    packed = zlib.compress(bytes(table), 9)
    if data_start is None:
        data_start = 44 + len(packed)

    header = bytearray(44)
    header[0:4] = b"AFAH"
    struct.pack_into("<I", header, 4, 0x1C)
    header[8:16] = b"AlicArch"
    struct.pack_into("<I", header, 16, version)
    struct.pack_into("<I", header, 20, 1)  # as real archives have
    struct.pack_into("<I", header, 24, data_start)
    header[28:32] = b"INFO"
    struct.pack_into("<I", header, 32, len(packed) + 16)
    struct.pack_into("<I", header, 36, len(table))
    struct.pack_into("<I", header, 40, len(files))

    out = bytearray(header + packed)
    out += b"\0" * (data_start - len(out))
    out += b"DATA" + struct.pack("<I", 8 + len(body)) + body
    return bytes(out)


def write_afa_bytes(tmp_path, files=None, **kwargs):
    """An archive on disk, built by the helper above rather than by us."""
    path = tmp_path / "Game.afa"
    path.write_bytes(make_afa(SAMPLE if files is None else files, **kwargs))
    return path


SAMPLE = [
    ("first.qnt", b"QNT\0" + b"a" * 100),
    ("second.qnt", b"QNT\0" + b"b" * 37),
    ("third.dcf", b"dcf " + b"c" * 9),
]


# ===== reading


def test_every_entry_comes_back_with_its_own_bytes(tmp_path):
    with AfaReader(write_afa_bytes(tmp_path, SAMPLE)) as ar:
        assert [e.name for e in ar] == ["first.qnt", "second.qnt", "third.dcf"]
        assert [ar.read(e) for e in ar] == [data for _, data in SAMPLE]


def test_sizes_and_offsets_describe_a_gapless_archive(tmp_path):
    with AfaReader(write_afa_bytes(tmp_path, SAMPLE)) as ar:
        ends = [e.offset + e.size for e in ar.entries]
        starts = [e.offset for e in ar.entries]
        assert starts[1:] == ends[:-1], "entries should sit end to end"
        assert ar.data_size == ends[-1]


@pytest.mark.parametrize("version,with_ids", [(1, True), (2, True), (2, False)])
def test_both_table_layouts_are_read(tmp_path, version, with_ids):
    """Whether an entry carries an id cannot be read from the header."""
    path = write_afa_bytes(tmp_path, SAMPLE, version=version, with_ids=with_ids)

    with AfaReader(path) as ar:
        assert ar.version == version
        assert ar.has_ids is with_ids
        assert [ar.read(e) for e in ar] == [data for _, data in SAMPLE]


def test_ids_are_numbered_from_the_stored_value(tmp_path):
    with AfaReader(write_afa_bytes(tmp_path, SAMPLE, version=1)) as ar:
        assert [e.number for e in ar] == [0, 1, 2]


def test_a_padded_name_does_not_shift_the_fields_after_it(tmp_path):
    """Each name is written twice over, its real length and its padded one."""
    path = write_afa_bytes(tmp_path, SAMPLE, pad_names=6)

    with AfaReader(path) as ar:
        assert [e.name for e in ar] == ["first.qnt", "second.qnt", "third.dcf"]
        assert [ar.read(e) for e in ar] == [data for _, data in SAMPLE]


def test_japanese_names_survive(tmp_path):
    files = [("立ち絵背／かなみ／基本.qnt", b"QNT\0data")]

    with AfaReader(write_afa_bytes(tmp_path, files)) as ar:
        assert ar.entries[0].name == "立ち絵背／かなみ／基本.qnt"


def test_backslashes_in_names_become_forward_slashes(tmp_path):
    with AfaReader(write_afa_bytes(tmp_path, [("dir\\file.qnt", b"QNT\0")])) as ar:
        assert ar.entries[0].name == "dir/file.qnt"


def test_the_name_is_kept_exactly_as_stored_as_well(tmp_path):
    """So a rebuilt archive can reproduce the table rather than approximate it."""
    with AfaReader(write_afa_bytes(tmp_path, SAMPLE, pad_names=4)) as ar:
        assert ar.entries[0].raw_name == b"first.qnt\0\0\0\0"


def test_padding_before_the_data_section_is_skipped(tmp_path):
    """Real archives align the data section rather than butting it up."""
    path = write_afa_bytes(tmp_path, SAMPLE, data_start=4096)

    with AfaReader(path) as ar:
        assert ar.data_start == 4096
        assert [ar.read(e) for e in ar] == [data for _, data in SAMPLE]


def test_closing_it_releases_the_file(tmp_path):
    path = write_afa_bytes(tmp_path, SAMPLE)
    ar = AfaReader(path)
    entry = ar.entries[0]
    ar.close()

    with pytest.raises(AfaError, match="closed"):
        ar.read(entry)
    path.unlink(), "the handle must be gone on Windows too"


# ===== refusing what it cannot read


def test_something_that_is_not_an_afa_is_refused(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_bytes(b"hello there, this is not an archive at all" * 4)
    with pytest.raises(AfaError, match="not an AFA"):
        AfaReader(path)


def test_a_file_too_short_to_hold_a_header_is_refused(tmp_path):
    path = tmp_path / "stub.afa"
    path.write_bytes(b"AFAH" + bytes(8))
    with pytest.raises(AfaError, match="too short"):
        AfaReader(path)


def test_version_three_is_named_rather_than_misread(tmp_path):
    """A different format that happens to share the outer magic."""
    data = bytearray(make_afa(SAMPLE))
    struct.pack_into("<I", data, 8, 3)
    path = tmp_path / "v3.afa"
    path.write_bytes(bytes(data))

    with pytest.raises(AfaError, match="version 3"):
        AfaReader(path)


def test_a_missing_data_marker_is_refused(tmp_path):
    data = bytearray(make_afa(SAMPLE))
    start = struct.unpack_from("<I", data, 24)[0]
    data[start:start + 4] = b"JUNK"
    path = tmp_path / "bad.afa"
    path.write_bytes(bytes(data))

    with pytest.raises(AfaError, match="does not start with DATA"):
        AfaReader(path)


def test_a_corrupt_file_table_says_so(tmp_path):
    data = bytearray(make_afa(SAMPLE))
    data[44:52] = b"garbage!"
    path = tmp_path / "bad.afa"
    path.write_bytes(bytes(data))

    with pytest.raises(AfaError, match="did not decompress"):
        AfaReader(path)


def test_a_data_section_past_the_end_of_the_file_is_refused(tmp_path):
    data = bytearray(make_afa(SAMPLE))
    struct.pack_into("<I", data, 24, len(data) + 5000)
    path = tmp_path / "bad.afa"
    path.write_bytes(bytes(data))

    with pytest.raises(AfaError, match="past the end"):
        AfaReader(path)


def test_a_truncated_entry_is_reported_rather_than_read_as_junk(tmp_path):
    """The header's count and the table's contents can disagree."""
    data = bytearray(make_afa(SAMPLE))
    struct.pack_into("<I", data, 40, 99)
    path = tmp_path / "bad.afa"
    path.write_bytes(bytes(data))

    with pytest.raises(AfaError, match="ran out|past the end"):
        AfaReader(path)


def test_reading_an_entry_that_runs_off_the_end_is_reported(tmp_path):
    """The table can promise more bytes than the archive actually holds."""
    path = write_afa_bytes(tmp_path, SAMPLE, overstate_last_by=500)

    with AfaReader(path) as ar:
        assert ar.read(ar.entries[0]) == SAMPLE[0][1], "the sound ones still read"
        with pytest.raises(AfaError, match="truncated"):
            ar.read(ar.entries[-1])


# ===== writing


def _outgoing(name, data, **kwargs):
    return OutgoingEntry(name=name, size=len(data), read=lambda: data, **kwargs)


def test_what_is_written_can_be_read_back(tmp_path):
    path = tmp_path / "out.afa"

    write_afa(path, [_outgoing(n, d) for n, d in SAMPLE])

    with AfaReader(path) as ar:
        assert [e.name for e in ar] == [n for n, _ in SAMPLE]
        assert [ar.read(e) for e in ar] == [d for _, d in SAMPLE]


def test_rebuilding_an_archive_reproduces_it_byte_for_byte(tmp_path):
    """The whole point. An untouched rebuild must not differ anywhere."""
    original = write_afa_bytes(tmp_path)
    rebuilt = tmp_path / "rebuilt.afa"

    with AfaReader(original) as ar:
        write_afa(rebuilt, [copy_of(ar, e) for e in ar.entries],
                  version=ar.version, has_ids=ar.has_ids, data_start=ar.data_start,
                  header_unknown=ar.header_unknown)

    assert rebuilt.read_bytes() == original.read_bytes()


def test_the_unknown_fields_are_carried_across_rather_than_zeroed(tmp_path):
    """Real archives hold something in them and alice-tools throws it away."""
    path = tmp_path / "out.afa"
    write_afa(path, [_outgoing("a.qnt", b"QNT\0xx", unknown0=12345, unknown1=999)])

    with AfaReader(path) as ar:
        assert (ar.entries[0].unknown0, ar.entries[0].unknown1) == (12345, 999)


def test_a_name_is_padded_out_to_a_multiple_of_four(tmp_path):
    """Which is what AliceSoft does, and it pads even when already aligned."""
    path = tmp_path / "out.afa"
    write_afa(path, [_outgoing("ab.qnt", b"QNT\0"), _outgoing("abcd.qnt", b"QNT\0")])

    with AfaReader(path) as ar:
        assert [len(e.raw_name) for e in ar] == [8, 12]
        assert [e.name for e in ar] == ["ab.qnt", "abcd.qnt"]


def test_a_stored_name_is_reproduced_exactly_when_one_is_given(tmp_path):
    path = tmp_path / "out.afa"
    write_afa(path, [_outgoing("a.qnt", b"QNT\0", raw_name=b"a.qnt\0\0\0\0\0\0\0")])

    with AfaReader(path) as ar:
        assert ar.entries[0].raw_name == b"a.qnt\0\0\0\0\0\0\0"
        assert ar.entries[0].name == "a.qnt"


def test_entries_are_packed_end_to_end(tmp_path):
    """AliceSoft does, alice-tools rounds each one up to eight bytes."""
    path = tmp_path / "out.afa"
    write_afa(path, [_outgoing("a.qnt", b"x" * 5), _outgoing("b.qnt", b"y" * 3)])

    with AfaReader(path) as ar:
        assert [(e.offset, e.size) for e in ar] == [(8, 5), (13, 3)]


def test_entries_can_be_aligned_when_asked(tmp_path):
    path = tmp_path / "out.afa"
    write_afa(path, [_outgoing("a.qnt", b"x" * 5), _outgoing("b.qnt", b"y" * 3)],
              entry_alignment=8)

    with AfaReader(path) as ar:
        assert [(e.offset, e.size) for e in ar] == [(8, 5), (16, 3)]
        assert [ar.read(e) for e in ar] == [b"x" * 5, b"y" * 3]


def test_the_data_section_is_aligned_and_the_gap_is_marked(tmp_path):
    path = tmp_path / "out.afa"
    write_afa(path, [_outgoing(n, d) for n, d in SAMPLE])

    raw = path.read_bytes()
    with AfaReader(path) as ar:
        assert ar.data_start % 0x1000 == 0
        table_end = 44 + struct.unpack_from("<I", raw, 32)[0] - 16
        assert raw[table_end:table_end + 4] == b"DUMM"
        assert struct.unpack_from("<I", raw, table_end + 4)[0] == ar.data_start - table_end
        assert set(raw[table_end + 8:ar.data_start]) == {0xFF}


def test_the_data_section_can_be_put_where_another_archive_had_it(tmp_path):
    path = tmp_path / "out.afa"
    write_afa(path, [_outgoing(n, d) for n, d in SAMPLE], data_start=0x4000)

    with AfaReader(path) as ar:
        assert ar.data_start == 0x4000
        assert [ar.read(e) for e in ar] == [d for _, d in SAMPLE]


def test_replacing_one_entry_shifts_the_ones_after_it(tmp_path):
    original = write_afa_bytes(tmp_path)
    changed = tmp_path / "changed.afa"

    with AfaReader(original) as ar:
        out = [copy_of(ar, e) for e in ar.entries]
        out[0] = replacement_for(ar.entries[0], b"QNT\0" + b"z" * 500)
        write_afa(changed, out, version=ar.version, has_ids=ar.has_ids)

    with AfaReader(changed) as ar:
        assert ar.read(ar.entries[0]) == b"QNT\0" + b"z" * 500
        assert [ar.read(e) for e in ar.entries[1:]] == [d for _, d in SAMPLE[1:]]
        assert ar.entries[1].offset == 8 + 504


@pytest.mark.parametrize("version,has_ids", [(1, True), (2, True), (2, False)])
def test_both_table_layouts_can_be_written(tmp_path, version, has_ids):
    path = tmp_path / "out.afa"
    write_afa(path, [_outgoing(n, d) for n, d in SAMPLE],
              version=version, has_ids=has_ids)

    with AfaReader(path) as ar:
        assert ar.has_ids is has_ids
        assert [ar.read(e) for e in ar] == [d for _, d in SAMPLE]


def test_japanese_names_survive_a_write(tmp_path):
    path = tmp_path / "out.afa"
    write_afa(path, [_outgoing("立ち絵背／かなみ／基本.qnt", b"QNT\0data")])

    with AfaReader(path) as ar:
        assert ar.entries[0].name == "立ち絵背／かなみ／基本.qnt"


def test_an_entry_that_lies_about_its_size_is_caught(tmp_path):
    """The table is written before the data, so a wrong size corrupts it."""
    liar = OutgoingEntry(name="a.qnt", size=10, read=lambda: b"short")

    with pytest.raises(AfaError, match="said it was 10 bytes"):
        write_afa(tmp_path / "out.afa", [liar])


def test_an_empty_archive_is_refused(tmp_path):
    with pytest.raises(AfaError, match="at least one file"):
        write_afa(tmp_path / "out.afa", [])


def test_an_unwritable_version_is_refused(tmp_path):
    with pytest.raises(AfaError, match="version 3"):
        write_afa(tmp_path / "out.afa", [_outgoing("a.qnt", b"x")], version=3)


def test_a_data_section_that_would_overlap_the_table_is_refused(tmp_path):
    with pytest.raises(AfaError, match="cannot start at"):
        write_afa(tmp_path / "out.afa", [_outgoing("a.qnt", b"x")], data_start=8)
