"""Shared path to the app icon, used for both the QApplication-level icon
(taskbar/alt-tab) and the main window's title bar icon."""

from __future__ import annotations

from pathlib import Path

ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"
