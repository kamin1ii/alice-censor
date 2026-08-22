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

# On by default. Alice Censor reads and writes every format these archives
# use, so a project needs no alice.exe at all. Turning this off puts extract
# and repack back through alice.exe, which is still worth having for an
# archive holding something this build has not met.
NATIVE_FORMATS_KEY = "experimental/native_formats"
DEFAULT_NATIVE_FORMATS = True


def _store() -> QSettings:
    return QSettings(ORGANISATION, APPLICATION)


def native_formats_enabled() -> bool:
    return bool(_store().value(NATIVE_FORMATS_KEY, DEFAULT_NATIVE_FORMATS, type=bool))


def set_native_formats_enabled(enabled: bool) -> None:
    store = _store()
    store.setValue(NATIVE_FORMATS_KEY, bool(enabled))
    store.sync()
