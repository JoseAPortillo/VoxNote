"""Main application window."""

from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.config import DIARIZE_SPEAKERS, TRANSLATE_TARGET_LANG
from app.services.pipeline import Pipeline
from app.services.transcriber import Transcriber
from app.storage.repository import MediaRepository
from app.ui.media_list import MediaList
from app.ui.workers import InferenceWorker


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Transcriptor — transcripción y traducción de audio")
        self.resize(920, 720)

        self.repository = MediaRepository()
        self.pipeline = Pipeline(Transcriber(), target_lang=TRANSLATE_TARGET_LANG)
        self._worker: InferenceWorker | None = None

        self._build_ui()
        self._connect_signals()
        self.media_list.reload()
        self._refresh_buttons()

    def _build_ui(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)

        hint = QLabel(
            "Arrastra vídeos o audios aquí · doble clic: alias · botón derecho: menú"
        )
        layout.addWidget(hint)

        self.media_list = MediaList(self.repository)
        layout.addWidget(self.media_list, stretch=3)

        buttons = QHBoxLayout()
        self.chk_diarize = QCheckBox("Separar hablantes")
        self.chk_diarize.setChecked(DIARIZE_SPEAKERS)
        self.chk_diarize.setToolTip(
            "Etiqueta cada línea con su interlocutor ([Hablante N]) usando "
            "PyAnnote. Requiere HF_TOKEN en .env; la primera vez descarga el modelo."
        )
        self.btn_transcribe = QPushButton("Transcribir")
        self.btn_translate = QPushButton("Traducir")
        buttons.addWidget(self.chk_diarize)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_transcribe)
        buttons.addWidget(self.btn_translate)
        layout.addLayout(buttons)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.status_label = QLabel("Listo.")
        layout.addWidget(self.status_label)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText(
            "La transcripción aparecerá aquí.\n"
            "• Transcribir: idioma original del audio.\n"
            f"• Traducir: texto en {TRANSLATE_TARGET_LANG.upper()}."
        )
        layout.addWidget(self.output, stretch=4)

        self.setCentralWidget(central)

    def _connect_signals(self) -> None:
        self.media_list.items_changed.connect(self._refresh_buttons)
        self.media_list.itemSelectionChanged.connect(self._refresh_buttons)
        self.btn_transcribe.clicked.connect(self._start_transcribe)
        self.btn_translate.clicked.connect(self._start_translate)

    # -- actions -------------------------------------------------------------
    def _start_transcribe(self) -> None:
        self._run(self.pipeline.transcribe)

    def _start_translate(self) -> None:
        self._run(self.pipeline.transcribe_and_translate)

    def _run(self, fn) -> None:
        path = self.media_list.selected_path()
        if path is None:
            QMessageBox.information(self, "Selección", "Selecciona un archivo de la lista.")
            return
        diarize = self.chk_diarize.isChecked()
        self.output.clear()
        self.progress.setValue(0)
        self.status_label.setText("Iniciando…")
        self._set_busy(True)

        def runnable(path, on_segment, on_progress, on_status) -> tuple[str, str]:
            return fn(
                path,
                on_segment=on_segment,
                on_progress=on_progress,
                on_status=on_status,
                diarize=diarize,
            )

        worker = InferenceWorker(runnable, path, parent=self)
        worker.segment_ready.connect(self._append_segment)
        worker.progress.connect(self.progress.setValue)
        worker.status.connect(self.status_label.setText)
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _append_segment(self, text: str) -> None:
        self.output.appendPlainText(text)
        scrollbar = self.output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_done(self, text: str, language: str) -> None:
        self.status_label.setText(f"Completado. Idioma detectado: {language}.")
        self._set_busy(False)

    def _on_failed(self, message: str) -> None:
        self._set_busy(False)
        self.status_label.setText("Error.")
        QMessageBox.critical(self, "Error", message)

    def _set_busy(self, busy: bool) -> None:
        self.media_list.setEnabled(not busy)
        self._refresh_buttons(busy)

    def _refresh_buttons(self, busy: bool = False) -> None:
        has_selection = self.media_list.selected_path() is not None
        self.btn_transcribe.setEnabled(not busy and has_selection)
        self.btn_translate.setEnabled(not busy and has_selection)

    def closeEvent(self, event) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.status_label.setText("Esperando a que termine la tarea…")
            self._worker.wait()
        super().closeEvent(event)