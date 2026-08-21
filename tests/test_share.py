"""Sharing censor work between machines.

A project file is small and holds the part that took the time, so it is
worth passing around. Two things stop it working on its own: every path in
it is absolute and belongs to one machine, and its overlay layers name
stickers that live in that machine's library.
"""

import json
import zipfile

import pytest

from alice_censor.project import CensorLayer, ImageRecord, ImageStatus, LayerType, ProjectState
from alice_censor.share import (
    BundleError,
    apply_bundle,
    export_bundle,
    read_bundle,
    referenced_stickers,
)


def _overlay(name):
    return CensorLayer(id=f"o-{name}", type=LayerType.OVERLAY, rect=(0, 0, 0.5, 0.5),
                       params={"sticker": name})


def _solid():
    return CensorLayer(id="s1", type=LayerType.SOLID, rect=(0, 0, 1, 1),
                       params={"color": "#000000"})


def _project(tmp_path, sticker_names=("a.png",)):
    stickers = tmp_path / "stickers"
    stickers.mkdir(parents=True, exist_ok=True)
    for n in sticker_names:
        (stickers / n).write_bytes(b"PNG" + n.encode())
    project = ProjectState(
        archive_path=str(tmp_path / "Game.afa"),
        alice_exe_path=str(tmp_path / "alice.exe"),
        extract_dir=str(tmp_path / "out"),
        output_dir=str(tmp_path / "out" / "censored_out"),
        manifest_path=str(tmp_path / "out" / "manifest.txt"),
        sticker_dir=str(stickers),
        archive_format="afa",
    )
    project.images["a.png"] = ImageRecord(
        status=ImageStatus.FLAGGED, layers=[_overlay("a.png"), _solid()]
    )
    project.images["b.png"] = ImageRecord(status=ImageStatus.CLEAN)
    project.images["c.png"] = ImageRecord()
    return project


def _recipient(tmp_path, paths=("a.png", "b.png", "c.png")):
    return ProjectState(
        archive_path=str(tmp_path / "their" / "Game.afa"),
        sticker_dir=str(tmp_path / "their" / "stickers"),
        images={p: ImageRecord() for p in paths},
    )


def test_a_bundle_carries_no_local_paths(tmp_path):
    """Every path in a project file belongs to the machine that made it."""
    export_bundle(_project(tmp_path), tmp_path / "b.zip")

    with zipfile.ZipFile(tmp_path / "b.zip") as zf:
        data = json.loads(zf.read("project.acproj.json"))
    for key in ("archive_path", "alice_exe_path", "extract_dir", "output_dir",
                "sticker_dir", "manifest_path"):
        assert key not in data, f"{key} must not travel"


def test_a_bundle_carries_the_stickers_its_layers_use(tmp_path):
    included = export_bundle(_project(tmp_path), tmp_path / "b.zip")

    assert included == ["a.png"]
    with zipfile.ZipFile(tmp_path / "b.zip") as zf:
        assert "stickers/a.png" in zf.namelist()


def test_unused_stickers_are_left_out(tmp_path):
    """A library collects whatever was tried and discarded."""
    project = _project(tmp_path, sticker_names=("a.png", "never_used.png"))

    included = export_bundle(project, tmp_path / "b.zip")

    assert included == ["a.png"]


def test_an_absolute_sticker_reference_is_not_exported(tmp_path):
    """Those predate the library and name a file on one machine."""
    project = _project(tmp_path)
    project.images["a.png"].layers = [_overlay(str(tmp_path / "elsewhere.png"))]

    assert referenced_stickers(project) == []
    assert export_bundle(project, tmp_path / "b.zip") == []


def test_a_missing_sticker_does_not_fail_the_export(tmp_path):
    project = _project(tmp_path)
    project.images["a.png"].layers.append(_overlay("gone.png"))

    included = export_bundle(project, tmp_path / "b.zip")

    assert included == ["a.png"], "the ones that exist still ship"


def test_applying_a_bundle_transfers_layers_and_status(tmp_path):
    export_bundle(_project(tmp_path), tmp_path / "b.zip")
    theirs = _recipient(tmp_path)

    result = apply_bundle(tmp_path / "b.zip", theirs)

    assert len(theirs.images["a.png"].layers) == 2
    assert theirs.images["a.png"].status == ImageStatus.FLAGGED
    assert theirs.images["b.png"].status == ImageStatus.CLEAN
    assert sorted(result.applied) == ["a.png", "b.png"]


def test_applying_unpacks_stickers_into_the_recipients_library(tmp_path):
    export_bundle(_project(tmp_path), tmp_path / "b.zip")
    theirs = _recipient(tmp_path)

    result = apply_bundle(tmp_path / "b.zip", theirs)

    assert result.stickers_copied == ["a.png"]
    assert (tmp_path / "their" / "stickers" / "a.png").is_file()


def test_a_sticker_the_recipient_already_has_is_not_overwritten(tmp_path):
    """It may be a different picture they chose deliberately."""
    export_bundle(_project(tmp_path), tmp_path / "b.zip")
    theirs = _recipient(tmp_path)
    mine = tmp_path / "their" / "stickers" / "a.png"
    mine.parent.mkdir(parents=True, exist_ok=True)
    mine.write_bytes(b"THEIRS")

    result = apply_bundle(tmp_path / "b.zip", theirs)

    assert mine.read_bytes() == b"THEIRS"
    assert result.stickers_copied == []


def test_images_the_recipient_does_not_have_are_reported_not_invented(tmp_path):
    """A bundle from a different archive should not silently add entries."""
    export_bundle(_project(tmp_path), tmp_path / "b.zip")
    theirs = _recipient(tmp_path, paths=("b.png",))

    result = apply_bundle(tmp_path / "b.zip", theirs)

    assert result.unmatched == ["a.png"]
    assert "a.png" not in theirs.images


def test_a_layer_whose_sticker_is_missing_is_reported(tmp_path):
    project = _project(tmp_path)
    project.images["a.png"].layers.append(_overlay("gone.png"))
    export_bundle(project, tmp_path / "b.zip")
    theirs = _recipient(tmp_path)

    result = apply_bundle(tmp_path / "b.zip", theirs)

    assert result.missing_stickers == ["gone.png"]


def test_untouched_images_are_left_alone(tmp_path):
    """An unreviewed image with no layers carries no information."""
    export_bundle(_project(tmp_path), tmp_path / "b.zip")
    theirs = _recipient(tmp_path)
    theirs.images["c.png"].status = ImageStatus.NEEDS_EDIT

    apply_bundle(tmp_path / "b.zip", theirs)

    assert theirs.images["c.png"].status == ImageStatus.NEEDS_EDIT


def test_overwrite_false_keeps_existing_work(tmp_path):
    export_bundle(_project(tmp_path), tmp_path / "b.zip")
    theirs = _recipient(tmp_path)
    theirs.images["a.png"].layers = [_solid()]

    apply_bundle(tmp_path / "b.zip", theirs, overwrite=False)

    assert len(theirs.images["a.png"].layers) == 1, "their own layer survives"


def test_reading_reports_what_is_inside_without_applying_it(tmp_path):
    export_bundle(_project(tmp_path), tmp_path / "b.zip")

    bundle = read_bundle(tmp_path / "b.zip")

    assert bundle.edited_count == 1
    assert bundle.layer_count == 2
    assert bundle.stickers == ["a.png"]
    assert bundle.archive_name == "Game.afa", "kept so a mismatch can be pointed out"


def test_something_that_is_not_a_bundle_is_refused(tmp_path):
    junk = tmp_path / "notes.zip"
    junk.write_bytes(b"this is not a zip at all")
    with pytest.raises(BundleError):
        read_bundle(junk)

    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("readme.txt", "hello")
    with pytest.raises(BundleError, match="not an Alice Censor bundle"):
        read_bundle(empty)
