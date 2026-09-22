"""Main application window."""

from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
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
from app.core.models import TimedSegment
from app.services.exporter import export as export_transcript
from app.services.pipeline import Pipeline
from app.services.transcriber import Transcriber
from app.storage.repository import MediaRepository
from app.ui.media_list import MediaList
from app.ui.theme import apply_theme, load_dark_setting, save_dark_setting
from app.ui.workers import InferenceWorker


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Transcriptor — transcripción y traducción de audio")
        self.resize(920, 720)

        self.repository = MediaRepository()
        self.pipeline = Pipeline(Transcriber(), target_lang=TRANSLATE_TARGET_LANG)
        self._worker: InferenceWorker | None = None
        self._last_text: str | None = None
        self._last_lang: str | None = None
        self._last_segments: list[TimedSegment] | None = None

        self._dark = load_dark_setting()
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, self._dark)

        self._build_ui()
        self._connect_signals()
        self.media_list.reload()
        self._refresh_buttons()

    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("central")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        hint = QLabel(
            "Arrastra vídeos o audios aquí · doble clic: alias · botón derecho: menú"
        )
        hint.setObjectName("hintLabel")
        header.addWidget(hint)
        header.addStretch(1)
        self.btn_theme = QPushButton()
        self.btn_theme.setObjectName("themeButton")
        self.btn_theme.setAccessibleName("Alternar tema oscuro/claro")
        self.btn_theme.setFixedWidth(42)
        header.addWidget(self.btn_theme)
        layout.addLayout(header)
        self._update_theme_button()

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
        self.btn_transcribe.setObjectName("primaryButton")
        self.btn_translate = QPushButton("Traducir")
        self.btn_save = QPushButton("Guardar TXT + SRT…")
        self.btn_save.setEnabled(False)
        buttons.addWidget(self.chk_diarize)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_transcribe)
        buttons.addWidget(self.btn_translate)
        buttons.addWidget(self.btn_save)
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
        self.btn_save.clicked.connect(self._start_save)
        self.btn_theme.clicked.connect(self._toggle_theme)

    # -- theme ---------------------------------------------------------------
    def _toggle_theme(self) -> None:
        self._dark = not self._dark
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, self._dark)
        save_dark_setting(self._dark)
        self._update_theme_button()

    def _update_theme_button(self) -> None:
        # Icon shows the mode you switch TO (sun in dark mode, moon in light).
        if self._dark:
            self.btn_theme.setText("☀")
            self.btn_theme.setToolTip("Cambiar a tema claro")
        else:
            self.btn_theme.setText("☾")
            self.btn_theme.setToolTip("Cambiar a tema oscuro")

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
        self._last_text = None
        self._last_lang = None
        self._last_segments = None
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

    def _on_done(self, text: str, language: str, segments) -> None:
        self._last_text = text
        self._last_lang = language
        self._last_segments = list(segments) if segments is not None else []
        self.status_label.setText(f"Completado. Idioma detectado: {language}.")
        self._set_busy(False)

    def _on_failed(self, message: str) -> None:
        self._set_busy(False)
        self.status_label.setText("Error.")
        QMessageBox.critical(self, "Error", message)

    def _suggested_save_path(self) -> str:
        """Suggested target for the save dialog: media folder, ``<stem>.<lang>.txt``."""
        source = self.media_list.selected_path()
        if source:
            media_path = Path(source)
            default_dir = str(media_path.parent)
            stem = media_path.stem
        else:
            default_dir = str(Path.home())
            stem = "transcripcion"
        name = f"{stem}.{self._last_lang}.txt" if self._last_lang else f"{stem}.txt"
        return str(Path(default_dir) / name)

    def _start_save(self) -> None:
        if not self._last_segments:
            return
        captured, _selected_filter = QFileDialog.getSaveFileName(
            self, "Guardar transcripción como…", self._suggested_save_path(),
            "Texto (*.txt)",
        )
        if not captured:
            return
        txt_path = Path(captured)
        if txt_path.suffix.lower() != ".txt":
            txt_path = txt_path.with_suffix(".txt")
        base = str(txt_path.with_suffix(""))
        try:
            export_transcript(self._last_segments, base, self._last_lang)
        except Exception as exc:
            self.status_label.setText("Error al guardar.")
            QMessageBox.critical(
                self, "Error", f"No se pudo guardar la transcripción.\n\n{exc}"
            )
            return
        self.status_label.setText(f"Guardado: {base}.txt + .srt")

    def _set_busy(self, busy: bool) -> None:
        self.media_list.setEnabled(not busy)
        self._refresh_buttons(busy)

    def _refresh_buttons(self, busy: bool = False) -> None:
        has_selection = self.media_list.selected_path() is not None
        self.btn_transcribe.setEnabled(not busy and has_selection)
        self.btn_translate.setEnabled(not busy and has_selection)
        self.btn_save.setEnabled(not busy and self._last_segments is not None)

    def closeEvent(self, event) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.status_label.setText("Esperando a que termine la tarea…")
            self._worker.wait()
        super().closeEvent(event)