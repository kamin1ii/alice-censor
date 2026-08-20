"""Removing censor layers from a gallery selection.

Destructive and not undoable, so the wiring around it matters as much as
the deletion itself. The widget only reports the selection, the window
confirms and logs, and the model does the work and marks the project dirty
so it gets saved.
"""

from PIL import Image
from PySide6.QtWidgets import QMessageBox

from alice_censor.alice_tools import AliceTools
from alice_censor.gui.main_window import MainWindow
from alice_censor.manifest import parse_manifest
from alice_censor.project import CensorLayer, ImageRecord, LayerType, ProjectState
from alice_censor.scanning import scan_and_sync
from alice_censor.session import OpenProject


def _layer():
    return CensorLayer(id="l1", type=LayerType.SOLID, rect=(0, 0, 0.5, 0.5),
                       params={"color": "#000000"})


def _window(qapp, tmp_path, edited=("a.png",), layers_each=2):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    names = ["a.png", "b.png", "c.png"]
    for name in names:
        Image.new("RGB", (40, 40), (255, 255, 255)).save(out_dir / name, "PNG")
    manifest_path = out_dir / "manifest.txt"
    manifest_path.write_text(
        "#ALICEPACK --src-dir=" + str(out_dir) + "\narchive.afa\n"
        + "\n".join(f"{n},qnt" for n in names) + "\n",
        encoding="utf-8",
    )
    manifest = parse_manifest(manifest_path)
    project = ProjectState(
        archive_path=str(tmp_path / "archive.afa"),
        manifest_path=str(manifest_path),
        alice_exe_path="dummy.exe",
        extract_dir=str(out_dir),
        output_dir=str(tmp_path / "censored_out"),
        archive_format="afa",
    )
    result = scan_and_sync(project, manifest)
    project.save(tmp_path / "p.acproj.json")
    for name in edited:
        project.images[name].layers = [_layer() for _ in range(layers_each)]

    window = MainWindow()
    window.session = OpenProject(project, manifest, AliceTools("dummy.exe"))
    window._refresh_summary()
    window._refresh_gallery(result)
    return window, project


def test_removing_edits_asks_first_and_says_what_it_would_delete(qapp, tmp_path, monkeypatch):
    window, project = _window(qapp, tmp_path, edited=("a.png", "c.png"), layers_each=3)
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a) or QMessageBox.Yes)
    )

    window._on_clear_edits_requested(["a.png", "b.png", "c.png"])

    assert asked, "a destructive bulk action must be confirmed"
    body = asked[0][2]
    assert "6 censor layer(s)" in body, "counts the layers, not the images"
    assert "2 image(s)" in body, "counts only the images that actually had edits"
    assert "cannot be undone" in body


def test_declining_the_confirmation_changes_nothing(qapp, tmp_path, monkeypatch):
    window, project = _window(qapp, tmp_path)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))

    window._on_clear_edits_requested(["a.png"])

    assert len(project.images["a.png"].layers) == 2


def test_accepting_removes_the_layers_and_saves_the_project(qapp, tmp_path, monkeypatch):
    window, project = _window(qapp, tmp_path)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))

    window._on_clear_edits_requested(["a.png"])

    assert project.images["a.png"].layers == []
    assert not window._dirty, "clearing edits must autosave, like every other gallery change"
    reloaded = ProjectState.load(tmp_path / "p.acproj.json")
    assert reloaded.images["a.png"].layers == []


def test_a_selection_with_no_edits_says_so_instead_of_prompting(qapp, tmp_path, monkeypatch):
    window, project = _window(qapp, tmp_path, edited=())
    asked = []
    told = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a)))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: told.append(a)))

    window._on_clear_edits_requested(["a.png", "b.png"])

    assert told, "the user should be told there was nothing to do"
    assert not asked, "no point confirming a deletion of nothing"


def test_the_gallery_button_reports_the_selection_rather_than_acting_on_it(
    qapp, tmp_path, monkeypatch
):
    """The widget owns no dialogs and no persistence, matching auto-flag.
    Selection itself comes from Qt and is not what is under test here."""
    window, project = _window(qapp, tmp_path)
    gallery = window.gallery_widget
    # The window is connected to this signal too and would open a real
    # confirmation. Decline it, which also proves reporting alone deletes
    # nothing.
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    monkeypatch.setattr(gallery, "_selected_paths", lambda: ["a.png", "c.png"])
    emitted = []
    gallery.clear_edits_requested.connect(lambda paths: emitted.append(paths))

    gallery._request_clear_edits()

    assert emitted == [["a.png", "c.png"]]
    assert project.images["a.png"].layers, "reporting must not itself delete anything"


def test_an_empty_selection_emits_nothing(qapp, tmp_path, monkeypatch):
    window, project = _window(qapp, tmp_path)
    gallery = window.gallery_widget
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    monkeypatch.setattr(gallery, "_selected_paths", lambda: [])
    emitted = []
    gallery.clear_edits_requested.connect(lambda paths: emitted.append(paths))

    gallery._request_clear_edits()

    assert emitted == []
