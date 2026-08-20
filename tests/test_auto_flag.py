from PIL import Image
from PySide6.QtWidgets import QMessageBox

from alice_censor.alice_tools import AliceTools
from alice_censor.gui.main_window import MainWindow
from alice_censor.manifest import parse_manifest
from alice_censor.project import ImageStatus, ProjectState
from alice_censor.scanning import scan_and_sync
from alice_censor.session import OpenProject


def _make_afa_window(qapp, tmp_path, monkeypatch, names):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    for name in names:
        Image.new("RGB", (50, 50), (255, 255, 255)).save(out_dir / name, "PNG")
    manifest_path = out_dir / "manifest.txt"
    lines = ["#ALICEPACK", "archive.afa"] + [f"{name},qnt" for name in names]
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
    return window, project, manifest


def test_auto_flag_button_enabled_only_for_afa(qapp, tmp_path):
    window, project, manifest = _make_afa_window(
        qapp, tmp_path, None, ["イベント／シィルＨ０１.png"]
    )
    assert window.gallery_widget.auto_flag_button.isEnabled()


def test_auto_flag_flags_matching_unreviewed_images(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    names = [
        "イベント／シィルＨ０１.png",
        "イベント／シィル挿入.png",
        "CG／タイトル.png",
    ]
    window, project, manifest = _make_afa_window(qapp, tmp_path, monkeypatch, names)

    window._on_auto_flag_requested()

    assert project.images["イベント／シィルＨ０１.png"].status == ImageStatus.FLAGGED
    assert project.images["イベント／シィル挿入.png"].status == ImageStatus.FLAGGED
    assert project.images["CG／タイトル.png"].status == ImageStatus.UNREVIEWED

    reloaded = ProjectState.load(project.project_file)
    assert reloaded.images["イベント／シィルＨ０１.png"].status == ImageStatus.FLAGGED


def test_auto_flag_does_not_override_already_reviewed_images(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    names = ["イベント／シィルＨ０１.png", "イベント／シィルＨ０２.png"]
    window, project, manifest = _make_afa_window(qapp, tmp_path, monkeypatch, names)
    project.images["イベント／シィルＨ０１.png"].status = ImageStatus.CLEAN

    window._on_auto_flag_requested()

    assert project.images["イベント／シィルＨ０１.png"].status == ImageStatus.CLEAN
    assert project.images["イベント／シィルＨ０２.png"].status == ImageStatus.FLAGGED


def test_auto_flag_declining_confirmation_changes_nothing(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    names = ["イベント／シィルＨ０１.png"]
    window, project, manifest = _make_afa_window(qapp, tmp_path, monkeypatch, names)

    window._on_auto_flag_requested()

    assert project.images["イベント／シィルＨ０１.png"].status == ImageStatus.UNREVIEWED


def test_auto_flag_no_matches_shows_info_and_does_not_prompt(qapp, tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(1) or QMessageBox.Yes)
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    names = ["CG／タイトル.png"]
    window, project, manifest = _make_afa_window(qapp, tmp_path, monkeypatch, names)

    window._on_auto_flag_requested()

    assert asked == []  # never reached confirmation -- nothing matched


def test_auto_flag_blocked_for_ald_with_clear_message(qapp, tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    Image.new("RGB", (50, 50), (255, 255, 255)).save(out_dir / "cg00001.png", "PNG")
    manifest_path = out_dir / "manifest.txt"
    manifest_path.write_text("#ALICEPACK\narchive.ald\ncg00001.png,qnt\n", encoding="utf-8")
    manifest = parse_manifest(manifest_path)
    project = ProjectState(
        archive_path=str((tmp_path / "archive.ald").resolve()),
        manifest_path=str(manifest_path.resolve()),
        alice_exe_path="dummy.exe",
        extract_dir=str(manifest.resolved_src_dir()),
        output_dir=str((tmp_path / "censored_out").resolve()),
        archive_format=manifest.archive_format.value,
    )
    result = scan_and_sync(project, manifest)
    project.save(manifest_path.with_suffix(".acproj.json"))

    # Opening an .ald project warns that rebuilding it is experimental.
    # Not what this test is about, so swallow it.
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    window = MainWindow()
    window.session = OpenProject(project, manifest, AliceTools("dummy.exe"))
    window._refresh_summary()
    window._refresh_gallery(result)

    assert not window.gallery_widget.auto_flag_button.isEnabled()

    shown = []
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a))
    )
    window._on_auto_flag_requested()

    assert shown
    assert project.images["cg00001.png"].status == ImageStatus.UNREVIEWED
