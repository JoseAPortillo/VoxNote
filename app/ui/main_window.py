"""Main application window."""

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
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
from app.ui.seek_slider import SeekSlider
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

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self._playing_path: str | None = None
        self._seeking = False
        self._duration_ms = 0

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

        # Playback bar: seek slider + elapsed/total time (hidden until a
        # source is loaded; durationChanged shows it).
        self.playback_bar = QWidget()
        bar_layout = QHBoxLayout(self.playback_bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(8)
        self.seek_slider = SeekSlider()
        self.time_label = QLabel("00:00 / 00:00")
        bar_layout.addWidget(self.seek_slider, stretch=1)
        bar_layout.addWidget(self.time_label)
        layout.addWidget(self.playback_bar)
        self.playback_bar.setVisible(False)

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
        self.media_list.play_requested.connect(self._toggle_play)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.positionChanged.connect(self._on_position_changed)
        self.seek_slider.sliderPressed.connect(self._on_seek_pressed)
        self.seek_slider.sliderReleased.connect(self._on_seek_released)
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
        self._stop_playback(reset_status=False)
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

    # -- media playback -------------------------------------------------------
    def _toggle_play(self, path: str) -> None:
        """Play/pause the audio of ``path`` (audio-only, no video window)."""
        if self._playing_path == path:
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
                self.status_label.setText("Reproducción pausada.")
            else:
                self.player.play()
                self.status_label.setText(f"Reproduciendo: {Path(path).stem}…")
            return
        self._stop_playback(reset_status=False)
        self._playing_path = path
        # Reset the seek bar for the new source; durationChanged re-ranges it.
        self._seeking = False
        self._duration_ms = 0
        self.seek_slider.setValue(0)
        self.time_label.setText("00:00 / 00:00")
        self.playback_bar.setVisible(True)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self.media_list.set_playing(path)
        self.status_label.setText(f"Reproduciendo: {Path(path).stem}…")

    def _stop_playback(self, reset_status: bool = True) -> None:
        """Stop playback and restore the row's play icon. With
        ``reset_status`` also clears the status label."""
        if self._playing_path is None:
            return
        self.player.stop()
        self.media_list.set_stopped(self._playing_path)
        self._playing_path = None
        self._seeking = False
        self._duration_ms = 0
        self.seek_slider.setValue(0)
        self.time_label.setText("00:00 / 00:00")
        self.playback_bar.setVisible(False)
        if reset_status:
            self.status_label.setText("Listo.")

    def _on_media_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._stop_playback()

    # -- seek bar -------------------------------------------------------------
    @staticmethod
    def _fmt_ms(ms: int) -> str:
        """Format milliseconds as ``mm:ss`` (or ``h:mm:ss`` past one hour)."""
        total_s = max(0, int(ms) // 1000)
        m, s = divmod(total_s, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _sync_time_label(self, elapsed_ms: int) -> None:
        self.time_label.setText(
            f"{self._fmt_ms(elapsed_ms)} / {self._fmt_ms(self._duration_ms)}"
        )

    def _on_duration_changed(self, duration: int) -> None:
        self._duration_ms = duration
        self.seek_slider.setRange(0, duration)
        self.playback_bar.setVisible(True)
        self._sync_time_label(self.seek_slider.value())

    def _on_position_changed(self, position: int) -> None:
        if self._seeking:
            return
        self.seek_slider.setValue(position)
        self._sync_time_label(position)

    def _on_seek_pressed(self) -> None:
        self._seeking = True

    def _on_seek_released(self) -> None:
        self._seeking = False
        self.player.setPosition(self.seek_slider.value())
        self._sync_time_label(self.seek_slider.value())

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
        self._stop_playback()
        if self._worker is not None and self._worker.isRunning():
            self.status_label.setText("Esperando a que termine la tarea…")
            self._worker.wait()
        super().closeEvent(event)