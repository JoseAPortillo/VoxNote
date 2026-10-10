"""Long-running work executed off the Qt main thread."""

from typing import Callable

from PySide6.QtCore import QThread, Signal

from app.core.models import RunResult

# Pipeline callables share the signature:
#   (path, on_segment, on_progress, on_status) -> RunResult


class InferenceWorker(QThread):
    """Runs a pipeline call and streams its callbacks to the UI."""

    segment_ready = Signal(str)
    progress = Signal(float)
    status = Signal(str)
    done = Signal(object)  # emits the RunResult produced by the pipeline call
    failed = Signal(str)

    def __init__(self, fn: Callable, path: str, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._path = path

    def run(self) -> None:
        try:
            result = self._fn(
                self._path,
                on_segment=self.segment_ready.emit,
                on_progress=self.progress.emit,
                on_status=self.status.emit,
            )
            self.done.emit(result)
        except Exception as exc:  # surface any failure in the UI
            self.failed.emit(str(exc))


class NotesWorker(QThread):
    """Runs a notes-generation call (Gemini API) off the Qt main thread.

    ``partial`` carries the accumulated notes so far as the model streams them,
    so the pane can show the note being written instead of a blind wait; ``done``
    carries the whole text once, for persistence.
    """

    partial = Signal(str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, source: str, generator, parent=None):
        super().__init__(parent)
        self._source = source
        self._generator = generator

    def run(self) -> None:
        try:
            parts: list[str] = []
            for piece in self._generator.stream_notes(self._source):
                parts.append(piece)
                self.partial.emit("".join(parts))
            self.done.emit("".join(parts))
        except Exception as exc:  # surface any failure in the UI
            self.failed.emit(str(exc))