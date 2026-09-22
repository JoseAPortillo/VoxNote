"""Long-running work executed off the Qt main thread."""

from typing import Callable

from PySide6.QtCore import QThread, Signal

# Pipeline callables share the signature:
#   (path, on_segment, on_progress, on_status) -> (full_text, language_code)


class InferenceWorker(QThread):
    """Runs a pipeline call and streams its callbacks to the UI."""

    segment_ready = Signal(str)
    progress = Signal(float)
    status = Signal(str)
    done = Signal(str, str)  # (full text, detected language)
    failed = Signal(str)

    def __init__(self, fn: Callable, path: str, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._path = path

    def run(self) -> None:
        try:
            text, language = self._fn(
                self._path,
                on_segment=self.segment_ready.emit,
                on_progress=self.progress.emit,
                on_status=self.status.emit,
            )
            self.done.emit(text, language)
        except Exception as exc:  # surface any failure in the UI
            self.failed.emit(str(exc))