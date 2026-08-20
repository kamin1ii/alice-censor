from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMessageBox

from alice_censor.alice_tools import AliceTools
from alice_censor.gui.main_window import MainWindow
from alice_censor.manifest import Manifest, ManifestFormat, ManifestOptions
from alice_censor.project import ProjectState
from alice_censor.session import OpenProject


def _project(tmp_path):
    return ProjectState(
        archive_path=str(tmp_path / "archive.afa"),
        manifest_path=str(tmp_path / "manifest.txt"),
        alice_exe_path="dummy.exe",
        extract_dir=str(tmp_path / "out"),
        output_dir=str(tmp_path / "censored_out"),
        archive_format="afa",
    )


def _open(tmp_path, project=None):
    """A window session around `project`. These tests only exercise saving
    and the close prompt, so the manifest is a valid empty one rather than
    a parsed fixture.
    """
    manifest = Manifest(
        manifest_path=tmp_path / "manifest.txt",
        magic="#ALICEPACK",
        options=ManifestOptions(),
        archive_line="archive.afa",
        archive_format=ManifestFormat.AFA,
    )
    return OpenProject(project or _project(tmp_path), manifest, AliceTools("dummy.exe"))


def test_close_without_dirty_changes_does_not_prompt(qapp, monkeypatch):
    def fail_if_called(*a, **k):
        raise AssertionError("should not prompt when there are no unsaved changes")

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fail_if_called))

    window = MainWindow()
    window._dirty = False
    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted()


def test_close_with_dirty_changes_prompts_user(qapp, monkeypatch):
    calls = []

    def record(*a, **k):
        calls.append(a)
        return QMessageBox.Cancel

    monkeypatch.setattr(QMessageBox, "question", staticmethod(record))

    window = MainWindow()
    window._dirty = True
    event = QCloseEvent()
    window.closeEvent(event)

    assert calls, "must prompt when there are unsaved changes"
    assert not event.isAccepted()  # Cancel keeps the window open
    assert window._dirty  # nothing was saved or discarded


def test_close_discard_closes_without_saving(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))

    window = MainWindow()
    window.session = _open(tmp_path)
    window.session.project.project_file = tmp_path / "project.acproj.json"
    window._dirty = True
    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted()
    assert not (tmp_path / "project.acproj.json").exists()  # discarded, not saved


def test_close_save_persists_then_closes(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Save))

    window = MainWindow()
    window.session = _open(tmp_path)
    project_file = tmp_path / "project.acproj.json"
    window.session.project.project_file = project_file
    window._dirty = True
    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted()
    assert project_file.exists()
    assert not window._dirty


def test_close_save_failure_keeps_window_open(qapp, tmp_path, monkeypatch):
    # Save Path points at a location that can't be written to (no
    # project_file set and no explicit path) -- ProjectState.save() raises
    # ValueError in that case, which isn't an OSError our _save_project
    # catches, so this specifically exercises "save() genuinely fails" via
    # an unwritable target directory instead.
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Save))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    window = MainWindow()
    window.session = _open(tmp_path)
    # A path under a file (not a directory) can't be created -> OSError.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    window.session.project.project_file = blocker / "project.acproj.json"
    window._dirty = True
    event = QCloseEvent()
    window.closeEvent(event)

    assert not event.isAccepted()
    assert window._dirty


def test_gallery_status_changed_marks_then_clears_dirty(qapp, tmp_path):
    window = MainWindow()
    window.session = _open(tmp_path)
    window.session.project.project_file = tmp_path / "project.acproj.json"
    assert not window._dirty

    window._on_gallery_status_changed()

    assert not window._dirty  # save succeeded, so the flag clears again
    assert (tmp_path / "project.acproj.json").exists()
