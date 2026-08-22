"""Preferences that belong to the installation rather than to a project.

Kept out of the project file on purpose. Whether this build reads and writes
archive formats itself says nothing about the censor work, and putting it in
the project would carry a choice made on one machine over to another that
might not want it, or might be an older build that does not have it.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings

ORGANISATION = "Alice Censor"
APPLICATION = "Alice Censor"

# Off by default. alice-tools is the path that has been used in anger, and
# the built in one takes over the step that overwrites a game archive.
NATIVE_FORMATS_KEY = "experimental/native_formats"


def _store() -> QSettings:
    return QSettings(ORGANISATION, APPLICATION)


def native_formats_enabled() -> bool:
    return bool(_store().value(NATIVE_FORMATS_KEY, False, type=bool))


def set_native_formats_enabled(enabled: bool) -> None:
    store = _store()
    store.setValue(NATIVE_FORMATS_KEY, bool(enabled))
    store.sync()
