"""Background thumbnail loading so opening the gallery doesn't block the
GUI thread decoding hundreds of PNGs. QImage (unlike QPixmap) is safe to
build off the GUI thread, so workers hand back QImages via a signal and the
model converts to QPixmap on receipt.

Every task carries the cancellation flag of the model that queued it. A
gallery can have hundreds of these in flight, and the model they report
back to is destroyed when the project is reloaded or the window closes, so
a task has to be able to find out that nobody is listening any more. See
GalleryModel.shutdown.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage

from ..project import CensorLayer
from .thumbnail_cache import ThumbnailCache


class ThumbnailSignals(QObject):
    ready = Signal(str, QImage)
    failed = Signal(str)


class ThumbnailTask(QRunnable):
    def __init__(
        self,
        key: str,
        source_path: Path,
        cache: ThumbnailCache,
        signals: ThumbnailSignals,
        layers: list[CensorLayer] | None = None,
        sticker_resolver=None,
        cancelled: threading.Event | None = None,
    ):
        super().__init__()
        self.key = key
        self.source_path = source_path
        self.cache = cache
        self.signals = signals
        self.layers = layers
        self.sticker_resolver = sticker_resolver
        self.cancelled = cancelled or threading.Event()

    def _emit(self, signal, *args) -> None:
        """Report back, unless the listener is gone.

        The flag is checked as late as possible, because a task that is
        already decoding when shutdown starts should still be dropped
        rather than delivered into a model that is tearing down.

        The RuntimeError catch is a last resort for a path that skipped
        shutdown entirely, such as the process being torn down without the
        window closing first. Without it Qt prints a traceback per task
        from a thread nobody can see, which reads like a crash and is not.
        """
        if self.cancelled.is_set():
            return
        try:
            signal.emit(*args)
        except RuntimeError:
            pass  # the receiving model was already destroyed

    def run(self) -> None:
        if self.cancelled.is_set():
            return
        cache_path = self.cache.get_or_create(
            self.source_path, layers=self.layers, sticker_resolver=self.sticker_resolver
        )
        if cache_path is None:
            self._emit(self.signals.failed, self.key)
            return
        image = QImage(str(cache_path))
        if image.isNull():
            self._emit(self.signals.failed, self.key)
            return
        self._emit(self.signals.ready, self.key, image)
