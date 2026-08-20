from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .icon import ICON_PATH
from .main_window import MainWindow


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Alice Censor")
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run())
