"""Background-thread runner for alice-tools commands, so extract/repack
(which shell out and can take a while on large archives) don't freeze the
GUI's event loop."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

from ..alice_tools import AliceToolsError, CommandResult


class CommandWorker(QThread):
    """Runs `job(on_output)` on a background thread.

    `job` should be one of the AliceTools methods (bound, with an
    `on_output` callback slot left to fill), for example

        worker = CommandWorker(lambda on_output: tools.extract(..., on_output=on_output))
    """

    output_line = Signal(str)
    finished_ok = Signal(object)   # CommandResult
    finished_err = Signal(str)

    def __init__(self, job: Callable[[Callable[[str], None]], CommandResult], parent=None):
        super().__init__(parent)
        self._job = job

    def run(self) -> None:
        try:
            result = self._job(self.output_line.emit)
        except AliceToolsError as e:
            self.finished_err.emit(str(e))
        except OSError as e:
            self.finished_err.emit(str(e))
        else:
            self.finished_ok.emit(result)
