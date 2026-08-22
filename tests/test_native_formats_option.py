"""The Advanced menu switch that turns on built in format support.

The switch belongs to the installation rather than to a project. The
isolated_settings fixture in conftest keeps every test here, and everywhere
else, away from the machine's real preferences.
"""

import pytest
from PySide6.QtWidgets import QDialog

from alice_censor.gui import settings as app_settings
from alice_censor.gui.main_window import MainWindow
from alice_censor.manifest import ManifestFormat


def test_it_is_on_unless_someone_turns_it_off(qapp):
    """Alice Censor reads and writes every format these archives use, so a
    project needs no alice.exe and should not be made to find one."""
    window = MainWindow()

    assert window._native_action.isChecked() is True
    assert window._use_native_formats() is True


def test_turning_it_off_is_remembered(qapp):
    window = MainWindow()

    window._native_action.setChecked(False)

    assert app_settings.native_formats_enabled() is False
    assert MainWindow()._native_action.isChecked() is False, "a new window agrees"


def test_turning_it_back_on_is_remembered(qapp):
    app_settings.set_native_formats_enabled(False)
    window = MainWindow()
    assert window._native_action.isChecked() is False

    window._native_action.setChecked(True)

    assert app_settings.native_formats_enabled() is True


def test_toggling_it_says_which_way_round_it_is(qapp):
    """The switch changes what overwrites a game archive, so it is logged."""
    window = MainWindow()

    window._native_action.setChecked(False)
    assert "Built in format support off" in window.log_view.toPlainText()

    window._native_action.setChecked(True)
    assert "Built in format support on" in window.log_view.toPlainText()


def test_the_switch_is_checkable_and_lives_under_advanced(qapp):
    window = MainWindow()

    menus = [a.text() for a in window.menuBar().actions()]
    assert "&Advanced" in menus
    assert window._native_action.isCheckable()
    assert "experimental" in window._native_action.text().lower()


# ===== which repack path a project takes


class _Session:
    def __init__(self, fmt):
        self.manifest = type("M", (), {"archive_format": fmt})()


@pytest.mark.parametrize("enabled,expected", [(False, "export"), (True, "native")])
def test_an_afa_goes_the_way_the_switch_says(qapp, monkeypatch, enabled, expected):
    window = MainWindow()
    window._native_action.setChecked(enabled)
    window.session = _Session(ManifestFormat.AFA)
    taken = []
    monkeypatch.setattr(window, "_tools_usable", lambda session: True)
    monkeypatch.setattr(window, "_repack_blocked_reason", lambda: None)
    monkeypatch.setattr(window, "_run_native_afa_repack", lambda s: taken.append("native"))
    monkeypatch.setattr(window, "_run_worker", lambda *a, **k: taken.append("export"))
    monkeypatch.setattr(window, "_run_ald_repack", lambda s: taken.append("ald"))

    window.run_repack()

    assert taken == [expected]


def test_an_ald_ignores_the_switch_because_it_has_no_other_path(qapp, monkeypatch):
    """alice-tools cannot write .ald at all, so that path is always built in."""
    window = MainWindow()
    window._native_action.setChecked(True)
    window.session = _Session(ManifestFormat.ALD)
    taken = []
    monkeypatch.setattr(window, "_tools_usable", lambda session: True)
    monkeypatch.setattr(window, "_repack_blocked_reason", lambda: None)
    monkeypatch.setattr(window, "_run_native_afa_repack", lambda s: taken.append("native"))
    monkeypatch.setattr(window, "_run_ald_repack", lambda s: taken.append("ald"))

    window.run_repack()

    assert taken == ["ald"]


# ===== what a new project asks for


def test_a_new_project_does_not_ask_for_alice_exe(qapp):
    """The whole point. Nothing shells out, so nothing needs finding."""
    from alice_censor.gui.new_project_dialog import NewProjectDialog

    dialog = NewProjectDialog(needs_alice_exe=False)

    assert not dialog.alice_exe_row.isVisibleTo(dialog)


def test_a_new_project_asks_for_alice_exe_when_the_switch_is_off(qapp):
    from alice_censor.gui.new_project_dialog import NewProjectDialog

    dialog = NewProjectDialog(needs_alice_exe=True)

    assert dialog.alice_exe_row.isVisibleTo(dialog)


def test_a_new_project_can_be_started_with_no_alice_exe(qapp, tmp_path):
    from alice_censor.gui.new_project_dialog import NewProjectDialog

    dialog = NewProjectDialog(needs_alice_exe=False)
    dialog.archive_row.set_text(str(tmp_path / "Game.afa"))
    dialog.output_row.set_text(str(tmp_path / "out"))
    dialog.project_file_row.set_text(str(tmp_path / "out" / "p.acproj.json"))

    dialog._on_accept()

    assert dialog.result() == QDialog.Accepted
    assert dialog.values()[1] == "", "and no path where one is not needed"


def test_a_new_project_still_needs_alice_exe_when_the_switch_is_off(qapp, tmp_path):
    from alice_censor.gui.new_project_dialog import NewProjectDialog

    dialog = NewProjectDialog(needs_alice_exe=True)
    dialog.archive_row.set_text(str(tmp_path / "Game.afa"))
    dialog.output_row.set_text(str(tmp_path / "out"))
    dialog.project_file_row.set_text(str(tmp_path / "out" / "p.acproj.json"))

    dialog._on_accept()

    assert dialog.result() != QDialog.Accepted


def test_the_dialog_a_new_project_opens_follows_the_switch(qapp, monkeypatch):
    """Off means ask for alice.exe, on means do not."""
    from alice_censor.gui import main_window as module

    asked = []

    class FakeDialog:
        def __init__(self, parent=None, *, needs_alice_exe=False):
            asked.append(needs_alice_exe)

        def exec(self):
            return 0

    monkeypatch.setattr(module, "NewProjectDialog", FakeDialog)

    window = MainWindow()
    window.new_project()
    window._native_action.setChecked(False)
    window.new_project()

    assert asked == [False, True]
