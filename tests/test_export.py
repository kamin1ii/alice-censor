from PIL import Image

from alice_censor.export import render_export
from alice_censor.manifest import parse_manifest
from alice_censor.project import CensorLayer, ImageRecord, LayerType, ProjectState


def _make_manifest(tmp_path, names, size=(50, 50)):
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        Image.new("RGB", size, (255, 255, 255)).save(out_dir / name, "PNG")
    manifest_path = tmp_path / "manifest.txt"
    lines = ["#ALICEPACK --src-dir=out", "archive.afa"]
    lines += [f"{name},qnt" for name in names]
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return parse_manifest(manifest_path)


def _solid_layer(color="#FF0000"):
    return CensorLayer(id="l1", type=LayerType.SOLID, rect=(0.0, 0.0, 1.0, 1.0), params={"color": color})


def test_image_with_layers_is_rendered(tmp_path):
    manifest = _make_manifest(tmp_path, ["a.png", "b.png"])
    project = ProjectState(output_dir=str(tmp_path / "censored_out"))
    project.images["a.png"] = ImageRecord(layers=[_solid_layer()])
    project.images["b.png"] = ImageRecord()

    result = render_export(project, manifest)

    assert result.rendered_paths == ["a.png"]
    assert result.copied_paths == ["b.png"]
    assert result.errors == {}

    with Image.open(tmp_path / "censored_out" / "a.png") as im:
        assert im.getpixel((5, 5))[:3] == (255, 0, 0)
    with Image.open(tmp_path / "censored_out" / "b.png") as im:
        assert im.getpixel((5, 5))[:3] == (255, 255, 255)  # untouched, just copied


def test_source_original_is_never_modified(tmp_path):
    manifest = _make_manifest(tmp_path, ["a.png"])
    project = ProjectState(output_dir=str(tmp_path / "censored_out"))
    project.images["a.png"] = ImageRecord(layers=[_solid_layer()])

    render_export(project, manifest)

    with Image.open(manifest.resolved_src_dir() / "a.png") as im:
        assert im.getpixel((5, 5))[:3] == (255, 255, 255)  # original untouched


def test_disabled_layer_is_treated_as_no_layers_and_copied(tmp_path):
    manifest = _make_manifest(tmp_path, ["a.png"])
    project = ProjectState(output_dir=str(tmp_path / "censored_out"))
    layer = _solid_layer()
    layer.enabled = False
    project.images["a.png"] = ImageRecord(layers=[layer])

    result = render_export(project, manifest)

    assert result.copied_paths == ["a.png"]
    assert result.rendered_paths == []


def test_image_with_no_project_record_is_copied(tmp_path):
    manifest = _make_manifest(tmp_path, ["a.png"])
    project = ProjectState(output_dir=str(tmp_path / "censored_out"))
    # no project.images entry at all for a.png

    result = render_export(project, manifest)

    assert result.copied_paths == ["a.png"]


def test_missing_source_file_reports_error_but_continues(tmp_path):
    manifest = _make_manifest(tmp_path, ["a.png", "b.png"])
    (manifest.resolved_src_dir() / "a.png").unlink()
    project = ProjectState(output_dir=str(tmp_path / "censored_out"))

    result = render_export(project, manifest)

    assert "a.png" in result.errors
    assert result.copied_paths == ["b.png"]


def test_writes_manifest_pointing_at_export_dir(tmp_path):
    manifest = _make_manifest(tmp_path, ["a.png"])
    project = ProjectState(output_dir=str(tmp_path / "censored_out"))
    project.images["a.png"] = ImageRecord(layers=[_solid_layer()])

    result = render_export(project, manifest)

    assert result.manifest_path == tmp_path / "censored_out" / "manifest.txt"
    exported = parse_manifest(result.manifest_path)
    assert exported.paths() == manifest.paths()
    assert exported.resolved_src_dir() == tmp_path / "censored_out"
    assert exported.resolved_archive_path() == manifest.resolved_archive_path()


def test_creates_cache_dir_upfront(tmp_path):
    # alice-tools' cache-write path doesn't create this directory itself
    # (confirmed against pack.c) -- a missing one just produces a noisy
    # WARNING per file during repack without affecting correctness, but we
    # can avoid the noise (and let its caching optimization actually work)
    # by creating it ourselves before repack ever runs.
    manifest = _make_manifest(tmp_path, ["a.png"])
    project = ProjectState(output_dir=str(tmp_path / "censored_out"))

    render_export(project, manifest)

    assert (tmp_path / "censored_out" / "alice-tools-cache").is_dir()


def test_relative_output_dir_is_resolved_to_absolute_in_export_manifest(tmp_path, monkeypatch):
    # project.output_dir being relative (e.g. hand-edited project.json, or
    # a caller that forgot to resolve it -- main_window.py always does,
    # but nothing enforces that at the ProjectState level) must not
    # silently double the --src-dir path on reparse the way a naive
    # relative write did before this was fixed.
    monkeypatch.chdir(tmp_path)
    manifest = _make_manifest(tmp_path, ["a.png"])
    project = ProjectState(output_dir="censored_out")  # deliberately relative

    result = render_export(project, manifest)

    exported = parse_manifest(result.manifest_path)
    assert exported.resolved_src_dir() == (tmp_path / "censored_out").resolve()


def test_rerender_skips_copy_when_export_already_up_to_date(tmp_path):
    manifest = _make_manifest(tmp_path, ["b.png"])
    project = ProjectState(output_dir=str(tmp_path / "censored_out"))

    render_export(project, manifest)
    exported_copy = tmp_path / "censored_out" / "b.png"
    first_mtime = exported_copy.stat().st_mtime

    render_export(project, manifest)  # nothing changed
    assert exported_copy.stat().st_mtime == first_mtime


# ===== pack cache curation
#
# `ar pack` packs a cache entry verbatim when it is newer than its source
# (pack.c alicepack_to_file_list). Seeding the original bytes for untouched
# files is what keeps them out of a needless decode and re-encode, and
# removing the entry for edited ones is what stops an edit being dropped.


def _make_cached_manifest(tmp_path, names, size=(50, 50)):
    """Like _make_manifest but with a raw cache alongside, standing in for
    what `ar extract --cache` leaves behind."""
    manifest = _make_manifest(tmp_path, names, size=size)
    raw_cache = tmp_path / "out" / "alice-tools-cache"
    raw_cache.mkdir(parents=True, exist_ok=True)
    for name in names:
        stem = name.rsplit(".", 1)[0]
        (raw_cache / f"{stem}.qnt").write_bytes(b"QNT\0original bytes for " + stem.encode())
    manifest_path = tmp_path / "manifest.txt"
    text = manifest_path.read_text(encoding="utf-8").splitlines()
    text[0] = "#ALICEPACK --src-dir=out --cache-dir=out/alice-tools-cache"
    manifest_path.write_text("\n".join(text) + "\n", encoding="utf-8")
    return parse_manifest(manifest_path)


def test_untouched_files_get_their_original_bytes_seeded_into_the_pack_cache(tmp_path):
    manifest = _make_cached_manifest(tmp_path, ["a.png", "b.png"])
    project = ProjectState(output_dir=str(tmp_path / "export"))

    result = render_export(project, manifest)

    assert sorted(result.preserved_paths) == ["a.png", "b.png"]
    cache = tmp_path / "export" / "alice-tools-cache"
    assert (cache / "a.qnt").read_bytes() == b"QNT\0original bytes for a"
    assert (cache / "b.qnt").read_bytes() == b"QNT\0original bytes for b"


def test_a_cached_entry_is_newer_than_its_source_so_pack_actually_uses_it(tmp_path):
    """pack.c compares whole seconds with a strict less than, so an equal
    timestamp is a cache miss."""
    manifest = _make_cached_manifest(tmp_path, ["a.png"])
    project = ProjectState(output_dir=str(tmp_path / "export"))

    render_export(project, manifest)

    src = (tmp_path / "export" / "a.png").stat().st_mtime
    cached = (tmp_path / "export" / "alice-tools-cache" / "a.qnt").stat().st_mtime
    assert int(src) < int(cached)


def test_an_edited_file_has_no_cache_entry_left_behind(tmp_path):
    """The dangerous case. A stale cache entry newer than the rendered PNG
    would be packed instead of the edit, silently discarding it."""
    manifest = _make_cached_manifest(tmp_path, ["a.png", "b.png"])
    project = ProjectState(output_dir=str(tmp_path / "export"))
    project.images["a.png"] = ImageRecord(layers=[_solid_layer()])
    # A previous export left one behind.
    stale = tmp_path / "export" / "alice-tools-cache"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "a.qnt").write_bytes(b"QNT\0stale")

    result = render_export(project, manifest)

    assert result.rendered_paths == ["a.png"]
    assert "a.png" not in result.preserved_paths
    assert not (stale / "a.qnt").exists(), "an edited file must not keep a cache entry"
    assert (stale / "b.qnt").exists(), "untouched files still keep theirs"


def test_seeding_is_skipped_when_the_extraction_had_no_cache(tmp_path):
    """Projects extracted without --cache still export, just without the
    byte-for-byte preservation."""
    manifest = _make_manifest(tmp_path, ["a.png"])  # no --cache-dir
    project = ProjectState(output_dir=str(tmp_path / "export"))

    result = render_export(project, manifest)

    assert result.copied_paths == ["a.png"]
    assert result.preserved_paths == []


def test_seeding_is_skipped_when_the_original_format_differs_from_the_packed_one(tmp_path):
    """An AJP row packs as qnt, and the raw bytes are AJP, so they must not
    be seeded under the qnt name. alice-tools has no AJP encoder and
    deliberately rewrites those entries."""
    manifest = _make_cached_manifest(tmp_path, ["a.png"])
    (tmp_path / "out" / "alice-tools-cache" / "a.qnt").unlink()
    (tmp_path / "out" / "alice-tools-cache" / "a.ajp").write_bytes(b"AJP\0raw")
    project = ProjectState(output_dir=str(tmp_path / "export"))

    result = render_export(project, manifest)

    assert result.preserved_paths == []
    assert not (tmp_path / "export" / "alice-tools-cache" / "a.qnt").exists()


def test_the_packed_order_is_archive_order_not_the_gallery_order(tmp_path):
    """The gallery sorts by name so scenes read in sequence. What gets
    packed must stay in the order the archive had, because that is what
    the game indexes into. These two orders must never be confused.
    """
    from alice_censor.paths import natural_sort_key

    archive_order = ["h10.png", "h02.png", "h01.png", "h04.png"]
    manifest = _make_manifest(tmp_path, archive_order)
    project = ProjectState(output_dir=str(tmp_path / "export"))

    result = render_export(project, manifest)

    packed = parse_manifest(result.manifest_path).paths()
    assert packed == archive_order, "packing must not reorder anything"
    assert packed != sorted(archive_order, key=natural_sort_key), (
        "and the sorted order must be genuinely different, or this proves nothing"
    )


# ===== difference images (DCF)
#
# A DCF stores a map of which 16x16 chunks differ from a base image, plus
# an image holding those chunks. Re-encoding one produced a black screen in
# Rance 03: dcf_encode blanks the matching chunks including their alpha,
# libsys4's QNT writer always emits an alpha plane, and AliceSoft's own
# difference images have none, so the blanked chunks come back transparent
# instead of opaque.


def _diff_manifest(tmp_path, names, diff_rows):
    """Manifest where `diff_rows` maps a path to the base it differs from."""
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        Image.new("RGB", (50, 50), (255, 255, 255)).save(out_dir / name, "PNG")
    lines = ["#ALICEPACK --src-dir=out", "archive.afa"]
    for name in names:
        if name in diff_rows:
            lines.append(f"{name},dcf,{diff_rows[name]}")
        else:
            lines.append(f"{name},qnt")
    (tmp_path / "manifest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return parse_manifest(tmp_path / "manifest.txt")


def test_an_edited_difference_image_is_written_whole(tmp_path):
    manifest = _diff_manifest(tmp_path, ["a.png", "b.png"], {"b.png": "a.png"})
    project = ProjectState(output_dir=str(tmp_path / "export"))
    project.images["b.png"] = ImageRecord(layers=[_solid_layer()])

    result = render_export(project, manifest)

    assert result.flattened_paths == ["b.png"]
    row = next(e for e in parse_manifest(result.manifest_path).entries if e.path == "b.png")
    assert row.dst_format == "qnt", "packed whole, not as a difference"
    assert row.extra is None, "a whole image has no base to point at"


def test_an_untouched_difference_image_is_left_as_it_was(tmp_path):
    """It is copied through as its original bytes and never re-encoded, so
    it keeps working exactly as before."""
    manifest = _diff_manifest(tmp_path, ["a.png", "b.png"], {"b.png": "a.png"})
    project = ProjectState(output_dir=str(tmp_path / "export"))

    result = render_export(project, manifest)

    assert result.flattened_paths == []
    row = next(e for e in parse_manifest(result.manifest_path).entries if e.path == "b.png")
    assert row.dst_format == "dcf"
    assert row.extra == "a.png", "its base reference must survive"


def test_flattening_does_not_disturb_ordinary_edited_images(tmp_path):
    manifest = _diff_manifest(tmp_path, ["a.png", "b.png"], {})
    project = ProjectState(output_dir=str(tmp_path / "export"))
    project.images["a.png"] = ImageRecord(layers=[_solid_layer()])

    result = render_export(project, manifest)

    assert result.flattened_paths == []
    row = next(e for e in parse_manifest(result.manifest_path).entries if e.path == "a.png")
    assert row.dst_format == "qnt"


def test_the_row_count_never_changes(tmp_path):
    """Every row still has to be packed, whatever format it ends up in."""
    manifest = _diff_manifest(tmp_path, ["a.png", "b.png", "c.png"],
                              {"b.png": "a.png", "c.png": "a.png"})
    project = ProjectState(output_dir=str(tmp_path / "export"))
    project.images["b.png"] = ImageRecord(layers=[_solid_layer()])

    result = render_export(project, manifest)

    assert len(parse_manifest(result.manifest_path).entries) == 3


def test_the_stale_cache_entry_under_the_old_format_is_removed(tmp_path):
    """The row's format changed, so pack looks for a different name in the
    cache. A leftover under the old name would be packed verbatim."""
    manifest = _diff_manifest(tmp_path, ["a.png", "b.png"], {"b.png": "a.png"})
    project = ProjectState(output_dir=str(tmp_path / "export"))
    project.images["b.png"] = ImageRecord(layers=[_solid_layer()])
    cache = tmp_path / "export" / "alice-tools-cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "b.dcf").write_bytes(b"stale difference")
    (cache / "b.qnt").write_bytes(b"stale whole image")

    render_export(project, manifest)

    assert not (cache / "b.dcf").exists(), "the old-format entry must go"
    assert not (cache / "b.qnt").exists(), "and so must the new-format one"
