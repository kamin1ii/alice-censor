from pathlib import Path

import pytest

from alice_censor.manifest import ManifestError, ManifestFormat, parse_manifest

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_afa_manifest_header_and_options():
    m = parse_manifest(FIXTURES / "afa_manifest.txt")
    assert m.magic == "#ALICEPACK"
    assert m.options.src_dir == "out"
    assert m.options.cache_dir == r"out\alice-tools-cache"
    assert m.archive_format == ManifestFormat.AFA
    assert m.archive_name == "Rance01CG.afa"


def test_parse_afa_manifest_entries():
    m = parse_manifest(FIXTURES / "afa_manifest.txt")
    assert len(m.entries) == 13
    first = m.entries[0]
    assert first.path == "イベント／シィルＨ０１.png"
    assert first.dst_format == "qnt"
    assert first.extra is None


def test_parse_quoted_entry_with_comma():
    m = parse_manifest(FIXTURES / "afa_manifest.txt")
    quoted = [e for e in m.entries if "weird" in e.path]
    assert len(quoted) == 1
    assert quoted[0].path == "CG／weird, name.png"
    assert quoted[0].dst_format == "qnt"


def test_parse_ald_manifest_format_detection():
    m = parse_manifest(FIXTURES / "ald_manifest.txt")
    assert m.archive_format == ManifestFormat.ALD
    assert m.archive_name == "rance02GA.ald"
    assert len(m.entries) == 8


def test_resolved_dirs_relative_to_manifest():
    m = parse_manifest(FIXTURES / "afa_manifest.txt")
    assert m.resolved_src_dir() == FIXTURES / "out"
    assert m.resolved_cache_dir() == FIXTURES / "out" / "alice-tools-cache"


def test_rejects_non_alicepack_magic(tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text("#BATCHPACK\narchive.afa\nfoo.png\n", encoding="utf-8")
    with pytest.raises(ManifestError):
        parse_manifest(bad)


def test_rejects_unrecognized_archive_extension(tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text("#ALICEPACK\narchive.zip\nfoo.png\n", encoding="utf-8")
    with pytest.raises(ManifestError):
        parse_manifest(bad)


def test_too_short_manifest(tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text("#ALICEPACK\n", encoding="utf-8")
    with pytest.raises(ManifestError):
        parse_manifest(bad)
