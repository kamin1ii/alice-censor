from pathlib import Path

from alice_censor.manifest import parse_manifest
from alice_censor.verify import _expected_archive_name, _parse_list_output, verify_archive_contents


def _make_manifest(tmp_path, rows):
    """rows: list of (path, dst_format) tuples."""
    manifest_path = tmp_path / "manifest.txt"
    lines = ["#ALICEPACK", "archive.afa"]
    for path, dst_format in rows:
        lines.append(f"{path},{dst_format}" if dst_format else path)
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return parse_manifest(manifest_path)


class FakeTools:
    def __init__(self, listing_text: str):
        self._listing_text = listing_text

    def list_archive(self, archive_path):
        class Result:
            stdout = self._listing_text

        return Result()


def test_expected_archive_name_replaces_extension_when_dst_format_given():
    assert _expected_archive_name("foo/bar.png", "qnt") == "foo/bar.qnt"


def test_expected_archive_name_keeps_original_when_no_dst_format():
    assert _expected_archive_name("foo/bar.png", None) == "foo/bar.png"


def test_expected_archive_name_lowercases_the_format():
    assert _expected_archive_name("bar.png", "QNT") == "bar.qnt"


def test_parse_list_output_extracts_names():
    text = "0: a.qnt\n1: foo／bar.qnt\n2: baz.qnt\n"
    assert _parse_list_output(text) == ["a.qnt", "foo／bar.qnt", "baz.qnt"]


def test_parse_list_output_ignores_blank_lines():
    text = "0: a.qnt\n\n1: b.qnt\n"
    assert _parse_list_output(text) == ["a.qnt", "b.qnt"]


def test_verify_ok_when_everything_matches(tmp_path):
    manifest = _make_manifest(tmp_path, [("a.png", "qnt"), ("b.png", "qnt")])
    tools = FakeTools("0: a.qnt\n1: b.qnt\n")

    result = verify_archive_contents(tools, "archive.afa", manifest)

    assert result.ok
    assert result.expected_count == 2
    assert result.actual_count == 2
    assert result.missing == []
    assert result.suspicious == []


def test_verify_detects_missing_file(tmp_path):
    manifest = _make_manifest(tmp_path, [("a.png", "qnt"), ("b.png", "qnt")])
    tools = FakeTools("0: a.qnt\n")  # b.qnt never made it into the archive

    result = verify_archive_contents(tools, "archive.afa", manifest)

    assert not result.ok
    assert result.missing == ["b.qnt"]


def test_verify_detects_question_mark_corruption(tmp_path):
    # The actual signature of the real alice-tools bug (GH #92): a filename
    # containing a specific character got silently replaced with "?" during
    # packing, even though `ar pack` itself exited 0.
    manifest = _make_manifest(tmp_path, [("a〜b.png", "qnt")])
    tools = FakeTools("0: a?b.qnt\n")

    result = verify_archive_contents(tools, "archive.afa", manifest)

    assert not result.ok
    assert result.suspicious == ["a?b.qnt"]
    assert result.missing == ["a〜b.qnt"]  # the correctly-named file is also absent


def test_verify_reports_unexpected_extra_entries(tmp_path):
    manifest = _make_manifest(tmp_path, [("a.png", "qnt")])
    tools = FakeTools("0: a.qnt\n1: stray.qnt\n")

    result = verify_archive_contents(tools, "archive.afa", manifest)

    assert result.unexpected == ["stray.qnt"]
    assert result.ok  # extras alone (no missing/suspicious) don't fail verification


def test_verify_no_dst_format_entry_keeps_original_extension(tmp_path):
    manifest = _make_manifest(tmp_path, [("a.x", None)])
    tools = FakeTools("0: a.x\n")

    result = verify_archive_contents(tools, "archive.afa", manifest)

    assert result.ok
