import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from alice_censor.gui import settings as app_settings


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path_factory, monkeypatch):
    """Keep the machine's own preferences out of every test, both ways.

    Application settings live in the registry, so without this a test that
    builds a MainWindow reads whatever the person running the tests last
    chose in the app. That is how it should behave in earnest and is exactly
    wrong here. It cost a hung suite once already, when the built in format
    switch was on in the real app and a repack test silently took that path
    and stopped on a dialog nobody was there to answer.

    Autouse rather than opt in, because the trap is that a test looks like
    it has nothing to do with settings right up until it does.
    """
    store = QSettings(
        str(tmp_path_factory.mktemp("settings") / "prefs.ini"), QSettings.IniFormat
    )
    monkeypatch.setattr(app_settings, "_store", lambda: store)
    yield store
