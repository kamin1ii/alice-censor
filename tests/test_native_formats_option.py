"""The Advanced menu switch that turns on built in format support.

The switch belongs to the installation rather than to a project, so these
point QSettings at a scratch location instead of writing to wherever the
machine running the tests keeps its real preferences.
"""

import pytest
from PySide6.QtCore import QSettings

from alice_censor.gui import settings as app_settings
from alice_censor.gui.main_window import MainWindow
from alice_censor.manifest import ManifestFormat


@pytest.fixture(autouse=True)
def scratch_settings(tmp_path, monkeypatch):
    """Keep the real preferences out of it, in both directions."""
    store = QSettings(str(tmp_path / "prefs.ini"), QSettings.IniFormat)
    monkeypatch.setattr(app_settings, "_store", lambda: store)
    yield store


def test_it_is_off_until_someone_turns_it_on(qapp):
    """alice-tools is the path that has been used in anger."""
    window = MainWindow()

    assert window._native_action.isChecked() is False
    assert window._use_native_repack() is False


def test_turning_it_on_is_remembered(qapp):
    window = MainWindow()

    window._native_action.setChecked(True)

    assert app_settings.native_formats_enabled() is True
    assert MainWindow()._native_action.isChecked() is True, "a new window agrees"


def test_turning_it_off_again_is_remembered(qapp):
    app_settings.set_native_formats_enabled(True)
    window = MainWindow()
    assert window._native_action.isChecked() is True

    window._native_action.setChecked(False)

    assert app_settings.native_formats_enabled() is False


def test_toggling_it_says_which_way_round_it_is(qapp):
    """The switch changes what overwrites a game archive, so it is logged."""
    window = MainWindow()

    window._native_action.setChecked(True)
    assert "Built in format support on" in window.log_view.toPlainText()

    window._native_action.setChecked(False)
    assert "Built in format support off" in window.log_view.toPlainText()


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
