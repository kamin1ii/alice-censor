"""Opening a project must be all or nothing.

A project file outliving the manifest it points at is an ordinary thing to
hit, since a game update or a tidied working folder can remove it. What
must not happen is the window ending up holding one project's state next
to another project's file list.
"""

from PySide6.QtWidgets import QFileDialog, QMessageBox

from alice_censor.alice_tools import AliceTools
from alice_censor.gui.main_window import MainWindow
from alice_censor.manifest import Manifest, ManifestFormat, ManifestOptions
from alice_censor.project import ProjectState
from alice_censor.session import OpenProject


def _already_open(tmp_path):
    """A window with a healthy project open, standing in for whatever the
    user was working on before they picked a broken one.
    """
    project = ProjectState(
        archive_path=str(tmp_path / "first.afa"),
        manifest_path=str(tmp_path / "first-manifest.txt"),
        alice_exe_path="dummy.exe",
        extract_dir=str(tmp_path / "out"),
        archive_format="afa",
    )
    manifest = Manifest(
        manifest_path=tmp_path / "first-manifest.txt",
        magic="#ALICEPACK",
        options=ManifestOptions(),
        archive_line="first.afa",
        archive_format=ManifestFormat.AFA,
    )
    window = MainWindow()
    window.session = OpenProject(project, manifest, AliceTools("dummy.exe"))
    return window, window.session


def _project_file_with_missing_manifest(tmp_path):
    second = ProjectState(
        archive_path=str(tmp_path / "second.afa"),
        manifest_path=str(tmp_path / "gone" / "manifest.txt"),
        alice_exe_path="dummy.exe",
        extract_dir=str(tmp_path / "second-out"),
        archive_format="afa",
    )
    path = tmp_path / "second.acproj.json"
    second.save(path)
    return path


def test_failed_open_leaves_the_previous_project_untouched(qapp, tmp_path, monkeypatch):
    window, original = _already_open(tmp_path)
    broken = _project_file_with_missing_manifest(tmp_path)

    errors = []
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(broken), ""))
    )
    monkeypatch.setattr(
        QMessageBox, "critical", staticmethod(lambda *a, **k: errors.append(a))
    )

    window.open_project()

    assert errors, "a project that cannot be opened must say so"
    # The whole point: not the new project carrying the old manifest.
    assert window.session is original


def test_successful_open_replaces_the_whole_session(qapp, tmp_path, monkeypatch):
    window, original = _already_open(tmp_path)

    manifest_path = tmp_path / "second-manifest.txt"
    manifest_path.write_text("#ALICEPACK\nsecond.afa\nb.png,QNT\n", encoding="utf-8")
    second = ProjectState(
        archive_path=str(tmp_path / "second.afa"),
        manifest_path=str(manifest_path),
        alice_exe_path="dummy.exe",
        extract_dir=str(tmp_path),
        archive_format="afa",
    )
    project_file = tmp_path / "second.acproj.json"
    second.save(project_file)

    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(project_file), ""))
    )

    window.open_project()

    assert window.session is not original
    assert window.session.project.archive_path == str(tmp_path / "second.afa")
    assert window.session.manifest.paths() == ["b.png"]


def _session_for(tmp_path, archive_line, fmt):
    project = ProjectState(
        archive_path=str(tmp_path / archive_line),
        manifest_path=str(tmp_path / "manifest.txt"),
        alice_exe_path="dummy.exe",
        extract_dir=str(tmp_path / "out"),
        output_dir=str(tmp_path / "censored_out"),
        archive_format=fmt.value,
    )
    manifest = Manifest(
        manifest_path=tmp_path / "manifest.txt",
        magic="#ALICEPACK",
        options=ManifestOptions(),
        archive_line=archive_line,
        archive_format=fmt,
    )
    project.project_file = tmp_path / "p.acproj.json"
    return OpenProject(project, manifest, AliceTools("dummy.exe"))


def _capture_load_warning(monkeypatch):
    """Opening an .ald project now interrupts with a modal. Tests have no
    one to click it, so capture it and hand back what it said."""
    shown = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a))
    )
    return shown


def test_repack_is_available_for_afa(qapp, tmp_path):
    from alice_censor.manifest import ManifestFormat

    window = MainWindow()
    window.session = _session_for(tmp_path, "game.afa", ManifestFormat.AFA)
    window._refresh_summary()

    assert window._repack_blocked_reason() is None
    assert window.repack_button.isEnabled()


def test_single_volume_ald_can_be_repacked(qapp, tmp_path, monkeypatch):
    """alice-tools cannot write .ald, so these go through ald_repack
    instead of `ar pack`. That is a supported path, not a blocked one.
    """
    from alice_censor.manifest import ManifestFormat

    _capture_load_warning(monkeypatch)
    (tmp_path / "gameA.ald").write_bytes(b"")
    window = MainWindow()
    window.session = _session_for(tmp_path, "gameA.ald", ManifestFormat.ALD)
    window._refresh_summary()

    assert window._repack_blocked_reason() is None
    assert window.repack_button.isEnabled()


def test_multi_volume_ald_is_refused_before_any_work_happens(qapp, tmp_path, monkeypatch):
    """A split set extracts as one archive but would have to be rebuilt as
    several, so it is refused up front rather than part way through.
    """
    from alice_censor.manifest import ManifestFormat

    warned_on_load = _capture_load_warning(monkeypatch)
    for name in ("gameA.ald", "gameB.ald"):
        (tmp_path / name).write_bytes(b"")
    window = MainWindow()
    window.session = _session_for(tmp_path, "gameA.ald", ManifestFormat.ALD)
    window._refresh_summary()

    reason = window._repack_blocked_reason()
    assert reason is not None and "volumes" in reason
    assert not window.repack_button.isEnabled()
    assert "Repack unavailable" in window.summary_label.text()

    assert warned_on_load, "a project that cannot be repacked must say so on open"
    started = []
    monkeypatch.setattr(MainWindow, "_run_worker", lambda self, *a, **k: started.append(a))
    window.run_repack()

    assert len(warned_on_load) > 1, "the user must be told why"
    assert not started, "nothing may be rebuilt for an archive that cannot be rebuilt correctly"


def test_ald_projects_are_told_the_path_is_experimental(qapp, tmp_path, monkeypatch):
    """The ALD writer is new and has been exercised against one archive, so
    a project that will use it says so when it opens, not after an evening
    of review."""
    from alice_censor.manifest import ManifestFormat

    shown = _capture_load_warning(monkeypatch)
    (tmp_path / "gameA.ald").write_bytes(b"")
    window = MainWindow()
    window.session = _session_for(tmp_path, "gameA.ald", ManifestFormat.ALD)
    window._refresh_summary()

    assert shown, "opening an .ald project must interrupt, not only log"
    assert "EXPERIMENTAL" in shown[0][2]
    assert shown[0][1] == "Experimental .ald support"

    notice = window._experimental_notice()
    assert notice is not None and "EXPERIMENTAL" in notice
    assert "orig-backup" in notice, "the way back must be part of the warning"
    assert "experimental" in window.summary_label.text().lower()
    assert "EXPERIMENTAL" in window.log_view.toPlainText()
    assert "EXPERIMENTAL" in window.repack_button.toolTip()
    # Still usable. This is a warning, not a block.
    assert window.repack_button.isEnabled()


def test_afa_projects_get_no_experimental_notice(qapp, tmp_path):
    from alice_censor.manifest import ManifestFormat

    window = MainWindow()
    window.session = _session_for(tmp_path, "game.afa", ManifestFormat.AFA)
    window._refresh_summary()

    assert window._experimental_notice() is None
    assert "experimental" not in window.summary_label.text().lower()
    assert "EXPERIMENTAL" not in window.log_view.toPlainText()


def test_the_notice_is_shown_once_not_on_every_refresh(qapp, tmp_path, monkeypatch):
    from alice_censor.manifest import ManifestFormat

    shown = _capture_load_warning(monkeypatch)
    (tmp_path / "gameA.ald").write_bytes(b"")
    window = MainWindow()
    window.session = _session_for(tmp_path, "gameA.ald", ManifestFormat.ALD)
    for _ in range(4):
        window._refresh_summary()

    assert window.log_view.toPlainText().count("EXPERIMENTAL") == 1
    assert len(shown) == 1, "the popup must not reappear on every refresh"


def test_rebuilding_an_ald_asks_before_overwriting_the_archive(qapp, tmp_path, monkeypatch):
    """Declining must leave the archive alone and hand the button back."""
    from alice_censor.manifest import ManifestFormat

    _capture_load_warning(monkeypatch)
    # The session points at a dummy alice.exe. Repack now verifies the
    # binary can do the job before it asks anything, and this test is
    # about the confirmation rather than about that check.
    monkeypatch.setattr(AliceTools, "check_available", lambda self: None)
    monkeypatch.setattr(AliceTools, "check_supported", lambda self: None)
    (tmp_path / "gameA.ald").write_bytes(b"")
    window = MainWindow()
    window.session = _session_for(tmp_path, "gameA.ald", ManifestFormat.ALD)
    window._refresh_summary()

    asked = []
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a) or QMessageBox.No)
    )
    started = []
    monkeypatch.setattr(MainWindow, "_run_worker", lambda self, *a, **k: started.append(a))
    window.run_repack()

    assert asked, "overwriting the game's own archive must be confirmed"
    assert "EXPERIMENTAL" in asked[0][2], "the confirmation must say what it is agreeing to"
    assert not started, "declining must not rebuild anything"
    assert window.repack_button.isEnabled(), "declining must leave Repack usable"


def test_repack_refuses_an_alice_exe_that_is_too_old(qapp, tmp_path, monkeypatch):
    """The failure is silent otherwise. A pre-manifest build accepts the
    command, does nothing and exits 0."""
    from alice_censor.alice_tools import AliceToolsOutdated
    from alice_censor.manifest import ManifestFormat

    _capture_load_warning(monkeypatch)
    (tmp_path / "gameA.ald").write_bytes(b"")
    window = MainWindow()
    window.session = _session_for(tmp_path, "gameA.ald", ManifestFormat.ALD)
    window._refresh_summary()

    monkeypatch.setattr(AliceTools, "check_available", lambda self: None)
    monkeypatch.setattr(
        AliceTools, "check_supported",
        lambda self: (_ for _ in ()).throw(AliceToolsOutdated("no --manifest, get a nightly")),
    )
    told = []
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: told.append(a)))
    started = []
    monkeypatch.setattr(MainWindow, "_run_worker", lambda self, *a, **k: started.append(a))

    window.run_repack()

    assert told, "the user must be told the binary is the problem"
    assert "too old" in told[0][1].lower()
    assert not started, "nothing may run with a build that cannot do the job"
    assert window.repack_button.isEnabled(), "Repack stays usable after pointing at a newer exe"
