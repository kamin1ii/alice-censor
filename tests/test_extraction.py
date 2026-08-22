"""Taking an archive apart into PNG files without alice-tools.

This is what a new project does first, and the only reason a project needed
alice.exe at all.
"""

import pytest
from PIL import Image

from alice_censor.extraction import ExtractionError, extract_archive
from alice_censor.formats import qnt
from alice_censor.manifest import parse_manifest

from .test_afa import make_afa
from .test_ajp import make_ajp
from .test_dcf import chunks_for, make_dcf

RED = (220, 40, 40, 255)
BLUE = (40, 40, 220, 255)


def flat(colour, size=(32, 32)):
    return Image.new("RGBA", size, colour)


def build(tmp_path, files, name="Game.afa"):
    path = tmp_path / name
    path.write_bytes(make_afa(files))
    return path


def rows(manifest_path):
    manifest = parse_manifest(manifest_path)
    return {e.path: (e.dst_format, e.extra) for e in manifest.entries}


def test_every_entry_becomes_a_png(tmp_path):
    archive = build(tmp_path, [
        ("one.qnt", qnt.encode(flat(RED))),
        ("two.qnt", qnt.encode(flat(BLUE))),
    ])
    out = tmp_path / "out"

    result = extract_archive(archive, out, out / "manifest.txt")

    assert sorted(result.written) == ["one.png", "two.png"]
    assert result.errors == {}
    assert Image.open(out / "one.png").convert("RGBA").getpixel((8, 8)) == RED
    assert Image.open(out / "two.png").convert("RGBA").getpixel((8, 8)) == BLUE


def test_the_manifest_can_be_read_back(tmp_path):
    archive = build(tmp_path, [("one.qnt", qnt.encode(flat(RED)))])
    out = tmp_path / "out"

    extract_archive(archive, out, out / "manifest.txt")

    manifest = parse_manifest(out / "manifest.txt")
    assert manifest.archive_format.value == "afa"
    assert [e.path for e in manifest.entries] == ["one.png"]


def test_an_ajp_is_recorded_as_qnt_because_nothing_can_write_one(tmp_path):
    archive = build(tmp_path, [
        ("colour.ajp", make_ajp(Image.new("RGB", (16, 16), (10, 200, 10)))),
    ])
    out = tmp_path / "out"

    extract_archive(archive, out, out / "manifest.txt")

    assert rows(out / "manifest.txt")["colour.png"] == ("qnt", None)
    r, g, b, a = Image.open(out / "colour.png").convert("RGBA").getpixel((8, 8))
    assert abs(g - 200) <= 3 and a == 255


def test_a_qnt_keeps_the_spelling_the_archive_used(tmp_path):
    """alice-tools does the same, so the two manifests can be compared."""
    archive = build(tmp_path, [("shout.QNT", qnt.encode(flat(RED)))])
    out = tmp_path / "out"

    extract_archive(archive, out, out / "manifest.txt")

    assert rows(out / "manifest.txt")["shout.png"] == ("QNT", None)


# ===== the difference format


def _dcf_archive(tmp_path, base_reference):
    """A base image and a difference against it, one chunk changed."""
    return build(tmp_path, [
        ("base.qnt", qnt.encode(flat(RED))),
        ("diff.dcf", make_dcf(32, 32, chunks_for(32, 32, changed={0}),
                              flat(BLUE), name=base_reference)),
    ])


def test_a_difference_is_composed_against_its_base(tmp_path):
    archive = _dcf_archive(tmp_path, "base.qnt")
    out = tmp_path / "out"

    extract_archive(archive, out, out / "manifest.txt")

    got = Image.open(out / "diff.png").convert("RGBA")
    assert got.getpixel((8, 8)) == BLUE, "the changed chunk"
    assert got.getpixel((24, 24)) == RED, "the rest, from the base"


def test_the_base_is_found_even_when_named_with_another_extension(tmp_path):
    """Rance 03 asks for a .bmp and the archive holds the same name as .qnt.

    Matching on anything more than the stem finds nothing, and the whole
    image then comes out as the bare difference.
    """
    archive = _dcf_archive(tmp_path, "some/folder/base.bmp")
    out = tmp_path / "out"

    extract_archive(archive, out, out / "manifest.txt")

    got = Image.open(out / "diff.png").convert("RGBA")
    assert got.getpixel((24, 24)) == RED, "the base was found and used"


def test_the_manifest_records_which_image_a_difference_is_against(tmp_path):
    archive = _dcf_archive(tmp_path, "base.qnt")
    out = tmp_path / "out"

    extract_archive(archive, out, out / "manifest.txt")

    assert rows(out / "manifest.txt")["diff.png"] == ("dcf", "base.png")


def test_a_difference_with_no_base_in_the_archive_still_comes_out(tmp_path):
    archive = build(tmp_path, [
        ("diff.dcf", make_dcf(32, 32, chunks_for(32, 32, changed={0}),
                              flat(BLUE), name="not_here.qnt")),
    ])
    out = tmp_path / "out"

    result = extract_archive(archive, out, out / "manifest.txt")

    assert result.errors == {}
    assert Image.open(out / "diff.png").convert("RGBA").getpixel((8, 8)) == BLUE


# ===== awkward archives


def test_a_name_with_a_folder_in_it_gets_the_folder_made(tmp_path):
    archive = build(tmp_path, [(r"art\people\one.qnt", qnt.encode(flat(RED)))])
    out = tmp_path / "out"

    result = extract_archive(archive, out, out / "manifest.txt")

    assert result.written == ["art/people/one.png"]
    assert (out / "art" / "people" / "one.png").is_file()


def test_the_kind_of_archive_is_read_from_the_file_not_its_name(tmp_path):
    """The pristine copy a repack reads from is called .afa.orig-backup."""
    archive = build(tmp_path, [("one.qnt", qnt.encode(flat(RED)))],
                    name="Game.afa.orig-backup")
    out = tmp_path / "out"

    result = extract_archive(archive, out, out / "manifest.txt",
                             archive_line=tmp_path / "Game.afa")

    assert result.archive_format == "afa"
    assert result.written == ["one.png"]
    assert parse_manifest(out / "manifest.txt").archive_format.value == "afa"


def test_one_unreadable_image_does_not_cost_the_others(tmp_path):
    archive = build(tmp_path, [
        ("good.qnt", qnt.encode(flat(RED))),
        ("bad.qnt", b"QNT\0" + bytes(60)),
        ("also_good.qnt", qnt.encode(flat(BLUE))),
    ])
    out = tmp_path / "out"

    result = extract_archive(archive, out, out / "manifest.txt")

    assert sorted(result.written) == ["also_good.png", "good.png"]
    assert list(result.errors) == ["bad.png"]
    assert "bad.png" not in rows(out / "manifest.txt"), "not offered for packing back"


def test_something_that_is_not_an_archive_is_refused(tmp_path):
    junk = tmp_path / "notes.afa"
    junk.write_bytes(b"neither one thing nor the other" * 8)

    with pytest.raises(ExtractionError, match="could not read"):
        extract_archive(junk, tmp_path / "out", tmp_path / "out" / "m.txt")


def test_progress_is_reported_for_every_image(tmp_path):
    archive = build(tmp_path, [(f"cg{i}.qnt", qnt.encode(flat(RED))) for i in range(5)])
    seen = []

    extract_archive(archive, tmp_path / "out", tmp_path / "out" / "m.txt",
                    on_progress=seen.append)

    assert sorted(seen) == [f"cg{i}.png" for i in range(5)]


@pytest.mark.parametrize("workers", [1, 4])
def test_the_result_does_not_depend_on_the_thread_count(tmp_path, workers):
    archive = build(tmp_path, [(f"cg{i}.qnt", qnt.encode(flat(RED))) for i in range(7)])
    out = tmp_path / f"out{workers}"

    result = extract_archive(archive, out, out / "m.txt", workers=workers)

    assert sorted(result.written) == [f"cg{i}.png" for i in range(7)]
    assert all((out / p).is_file() for p in result.written)


def test_the_whole_archive_is_not_read_into_memory_at_once(tmp_path):
    """An archive is far bigger than the images being written out of it.

    Planning looks only at the front of each entry, and the rest is read in
    the wave that decodes it, so a 772 MB archive does not have to fit in
    memory to extract.
    """
    from alice_censor.extraction import PEEK

    archive = build(tmp_path, [(f"cg{i}.qnt", qnt.encode(flat(RED))) for i in range(6)])
    reads = []

    import alice_censor.extraction as module

    original = module._Source.read

    def counted(self, index):
        reads.append(index)
        return original(self, index)

    module._Source.read = counted
    try:
        extract_archive(archive, tmp_path / "out", tmp_path / "out" / "m.txt", workers=1)
    finally:
        module._Source.read = original

    assert len(reads) == 6, "each entry read once, and only when it is decoded"
    assert PEEK < 65536, "a peek has to stay small to be worth doing"
