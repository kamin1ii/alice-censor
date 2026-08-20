import os
import time

from alice_censor.manifest import parse_manifest
from alice_censor.project import ProjectState
from alice_censor.scanning import scan_and_sync


def _make_manifest(tmp_path, names):
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True)
    for name in names:
        (out_dir / name).write_bytes(b"fake png bytes")
    manifest_path = tmp_path / "manifest.txt"
    lines = ["#ALICEPACK --src-dir=out", "archive.afa"]
    lines += [f"{name},qnt" for name in names]
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest_path


def test_first_scan_reports_everything_new(tmp_path):
    manifest_path = _make_manifest(tmp_path, ["a.png", "b.png"])
    manifest = parse_manifest(manifest_path)
    project = ProjectState()

    result = scan_and_sync(project, manifest)

    assert sorted(result.new_paths) == ["a.png", "b.png"]
    assert result.changed_paths == []
    assert result.missing_paths == []
    assert project.images["a.png"].size == len(b"fake png bytes")
    assert project.images["a.png"].mtime is not None


def test_rescan_with_no_changes_reports_nothing(tmp_path):
    manifest_path = _make_manifest(tmp_path, ["a.png", "b.png"])
    manifest = parse_manifest(manifest_path)
    project = ProjectState()
    scan_and_sync(project, manifest)

    result = scan_and_sync(project, manifest)

    assert result.new_paths == []
    assert result.changed_paths == []
    assert result.missing_paths == []


def test_rescan_detects_changed_file(tmp_path):
    manifest_path = _make_manifest(tmp_path, ["a.png"])
    manifest = parse_manifest(manifest_path)
    project = ProjectState()
    scan_and_sync(project, manifest)

    # simulate the game update re-extracting a.png with different content
    fs_path = manifest.resolved_src_dir() / "a.png"
    time.sleep(0.05)
    fs_path.write_bytes(b"different, longer content")
    new_mtime = time.time() + 5
    os.utime(fs_path, (new_mtime, new_mtime))

    result = scan_and_sync(project, manifest)

    assert result.changed_paths == ["a.png"]
    assert result.new_paths == []


def test_rescan_detects_new_and_missing_files(tmp_path):
    manifest_path = _make_manifest(tmp_path, ["a.png", "b.png"])
    manifest = parse_manifest(manifest_path)
    project = ProjectState()
    scan_and_sync(project, manifest)

    manifest_path2 = _make_manifest(tmp_path.parent / (tmp_path.name + "_v2"), ["a.png", "c.png"])
    manifest2 = parse_manifest(manifest_path2)
    # reuse the same project (simulating a game-update rescan)
    result = scan_and_sync(project, manifest2)

    assert result.new_paths == ["c.png"]
    assert result.missing_paths == ["b.png"]
    assert "b.png" in project.images  # review history for missing files is kept


def test_scan_populates_groups(tmp_path):
    manifest_path = _make_manifest(tmp_path, ["a.png", "b.png"])
    manifest = parse_manifest(manifest_path)
    project = ProjectState()

    result = scan_and_sync(project, manifest)

    all_members = sorted(m for g in result.groups.values() for m in g.members)
    assert all_members == ["a.png", "b.png"]
