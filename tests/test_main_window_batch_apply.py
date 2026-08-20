from PIL import Image
from PySide6.QtWidgets import QMessageBox

from alice_censor.editor.batch_apply_dialog import BatchApplyDialog
from alice_censor.alice_tools import AliceTools
from alice_censor.gui.main_window import MainWindow
from alice_censor.manifest import parse_manifest
from alice_censor.project import ProjectState
from alice_censor.scanning import scan_and_sync
from alice_censor.session import OpenProject


def _make_window_with_group(qapp, tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    for name in ("evt／sceneＨ０１.png", "evt／sceneＨ０２.png", "evt／sceneＨ０３.png"):
        Image.new("RGB", (100, 80), (255, 255, 255)).save(out_dir / name, "PNG")
    manifest_path = out_dir / "manifest.txt"
    lines = ["#ALICEPACK", "archive.afa"]
    lines += [
        f"{name},qnt"
        for name in ("evt／sceneＨ０１.png", "evt／sceneＨ０２.png", "evt／sceneＨ０３.png")
    ]
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = parse_manifest(manifest_path)

    project = ProjectState(
        archive_path=str((tmp_path / "archive.afa").resolve()),
        manifest_path=str(manifest_path.resolve()),
        alice_exe_path="dummy.exe",
        extract_dir=str(manifest.resolved_src_dir()),
        output_dir=str((tmp_path / "censored_out").resolve()),
        archive_format=manifest.archive_format.value,
    )
    result = scan_and_sync(project, manifest)
    project.save(manifest_path.with_suffix(".acproj.json"))

    window = MainWindow()
    window.session = OpenProject(project, manifest, AliceTools("dummy.exe"))
    window._refresh_summary()
    window._refresh_gallery(result)

    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    return window, project, manifest


def test_group_members_excludes_self_and_other_groups(qapp, tmp_path, monkeypatch):
    window, project, manifest = _make_window_with_group(qapp, tmp_path, monkeypatch)

    members = window._group_members("evt／sceneＨ０１.png")

    assert sorted(members) == ["evt／sceneＨ０２.png", "evt／sceneＨ０３.png"]


def test_open_and_batch_apply_updates_project_and_gallery(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QRectF

    window, project, manifest = _make_window_with_group(qapp, tmp_path, monkeypatch)

    captured = {}

    def fake_exec(self):
        # simulate: draw a region, batch-apply it to the other two, then
        # cancel this image's own edit (Save/Cancel shouldn't matter for
        # what batch apply already committed)
        self._on_region_created(QRectF(10, 10, 40, 30))
        captured["dialog"] = self
        import alice_censor.editor.batch_apply_dialog as bad

        real_exec = bad.BatchApplyDialog.exec
        real_selected = bad.BatchApplyDialog.selected_paths
        bad.BatchApplyDialog.exec = lambda s: 1
        bad.BatchApplyDialog.selected_paths = lambda s: s.list_widget and [
            s.list_widget.item(i).text() for i in range(s.list_widget.count())
        ]
        try:
            self._on_batch_apply()
        finally:
            bad.BatchApplyDialog.exec = real_exec
            bad.BatchApplyDialog.selected_paths = real_selected
        return 0  # user then cancels their own edit

    monkeypatch.setattr(
        "alice_censor.gui.main_window.RegionEditorDialog.exec", fake_exec, raising=True
    )

    window._on_gallery_open_requested("evt／sceneＨ０１.png")

    assert len(project.images["evt／sceneＨ０２.png"].layers) == 1
    assert len(project.images["evt／sceneＨ０３.png"].layers) == 1
    assert project.images["evt／sceneＨ０１.png"].layers == []  # own edit was cancelled

    reloaded = ProjectState.load(project.project_file)
    assert len(reloaded.images["evt／sceneＨ０２.png"].layers) == 1
