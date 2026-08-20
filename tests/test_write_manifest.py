from pathlib import Path

from alice_censor.manifest import parse_manifest, write_manifest

FIXTURES = Path(__file__).parent / "fixtures"


def test_write_manifest_round_trips_through_parse(tmp_path):
    original = parse_manifest(FIXTURES / "afa_manifest.txt")

    export_manifest_path = tmp_path / "export" / "manifest.txt"
    export_manifest_path.parent.mkdir(parents=True)
    export_src_dir = tmp_path / "rendered"
    export_cache_dir = export_src_dir / "alice-tools-cache"

    write_manifest(original, export_manifest_path, src_dir=export_src_dir, cache_dir=export_cache_dir)
    reparsed = parse_manifest(export_manifest_path)

    assert reparsed.paths() == original.paths()
    assert [e.dst_format for e in reparsed.entries] == [e.dst_format for e in original.entries]
    assert reparsed.resolved_src_dir() == export_src_dir
    assert reparsed.resolved_cache_dir() == export_cache_dir


def test_write_manifest_points_archive_line_at_original_archive(tmp_path):
    original = parse_manifest(FIXTURES / "afa_manifest.txt")
    export_manifest_path = tmp_path / "export" / "manifest.txt"
    export_manifest_path.parent.mkdir(parents=True)

    write_manifest(original, export_manifest_path, src_dir=tmp_path / "rendered")
    reparsed = parse_manifest(export_manifest_path)

    # archive line must resolve to the SAME absolute path regardless of
    # this manifest living in a different directory than the original
    assert reparsed.resolved_archive_path() == original.resolved_archive_path()


def test_write_manifest_quotes_paths_with_special_characters(tmp_path):
    original = parse_manifest(FIXTURES / "afa_manifest.txt")
    export_manifest_path = tmp_path / "manifest.txt"

    # a src_dir containing a space (extremely common on Windows) must
    # still round-trip through alice-tools' own real lexer requirements
    write_manifest(original, export_manifest_path, src_dir=tmp_path / "path with space" / "out")
    reparsed = parse_manifest(export_manifest_path)

    assert reparsed.resolved_src_dir() == tmp_path / "path with space" / "out"
    # the quoted-comma fixture entry should still be present and correct
    quoted = [e for e in reparsed.entries if "weird" in e.path]
    assert quoted and quoted[0].path == "CG／weird, name.png"


def test_write_manifest_without_cache_dir(tmp_path):
    original = parse_manifest(FIXTURES / "afa_manifest.txt")
    export_manifest_path = tmp_path / "manifest.txt"

    write_manifest(original, export_manifest_path, src_dir=tmp_path / "rendered")
    reparsed = parse_manifest(export_manifest_path)

    assert reparsed.resolved_cache_dir() is None
