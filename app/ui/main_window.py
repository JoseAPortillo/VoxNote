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

from app.core.config import DIARIZE_SPEAKERS, TRANSLATE_TARGET_LANG, VIDEO_EXTENSIONS
from app.core.models import RunResult, TimedSegment
from app.services.exporter import export as export_transcript
from app.services.pipeline import Pipeline
from app.services.transcriber import Transcriber
from app.storage.repository import MediaRepository
from app.ui.media_list import MediaList
from app.ui.seek_slider import SeekSlider
from app.ui.theme import apply_theme, load_dark_setting, save_dark_setting
from app.ui.video_player_window import VideoPlayerWindow
from app.ui.workers import InferenceWorker


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Transcriptor — transcripción y traducción de audio")
        self.resize(920, 720)

        self.repository = MediaRepository()
        self.pipeline = Pipeline(Transcriber(), target_lang=TRANSLATE_TARGET_LANG)
        self._worker: InferenceWorker | None = None
        self._last_run: RunResult | None = None
        self._active_pane: str = "transcript"  # pane receiving live segments
        self._running_path: str | None = None  # path persisted on done

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self._playing_path: str | None = None
        self._seeking = False
        self._duration_ms = 0
        self.video_window: VideoPlayerWindow | None = None  # lazy; hidden, not deleted

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
            "Arrastra vídeos o audios aquí · doble clic en el nombre: alias · "
            "clic en la anotación: editarla · botón derecho: menú"
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

        # Two side-by-side panes: transcription (original) | translation.
        pane_transcript, self.pane_transcript = self._make_pane(
            "Transcripción",
            "La transcripción (idioma original) aparecerá aquí.",
        )
        pane_translation, self.pane_translation = self._make_pane(
            "Traducción",
            "La traducción aparecerá aquí. Transcribir llena solo la izquierda; "
            "Traducir llena ambas.",
        )
        panes = QHBoxLayout()
        panes.setSpacing(8)
        panes.addWidget(pane_transcript, stretch=1)
        panes.addWidget(pane_translation, stretch=1)
        layout.addLayout(panes, stretch=4)

        self.setCentralWidget(central)

    def _make_pane(self, title: str, placeholder: str) -> tuple[QWidget, QPlainTextEdit]:
        """A labeled read-only text pane: header label over a text box.

        Returns ``(column_widget, edit)``: the column goes into the layout,
        the edit is kept so the window can route segments and restore text.
        """
        pane = QWidget()
        column = QVBoxLayout(pane)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        header = QLabel(title)
        header.setObjectName("paneLabel")
        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setPlaceholderText(placeholder)
        edit.setAccessibleName(title)
        column.addWidget(header)
        column.addWidget(edit, stretch=1)
        return pane, edit

    def _connect_signals(self) -> None:
        self.media_list.items_changed.connect(self._refresh_buttons)
        self.media_list.itemSelectionChanged.connect(self._refresh_buttons)
        self.media_list.itemSelectionChanged.connect(self._on_selection_changed)
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
        self._run(self.pipeline.transcribe, "transcript")

    def _start_translate(self) -> None:
        self._run(self.pipeline.transcribe_and_translate, "translation")

    def _run(self, fn, pane: str) -> None:
        path = self.media_list.selected_path()
        if path is None:
            QMessageBox.information(self, "Selección", "Selecciona un archivo de la lista.")
            return
        self._stop_playback(reset_status=False)
        diarize = self.chk_diarize.isChecked()
        self._running_path = path
        self._active_pane = pane
        self._last_run = None
        # Clear only the pane being produced; the other one keeps its content.
        active_pane = self.pane_transcript if pane == "transcript" else self.pane_translation
        active_pane.clear()
        self.progress.setValue(0)
        self.status_label.setText("Iniciando…")
        self._set_busy(True)

        def runnable(path, on_segment, on_progress, on_status) -> RunResult:
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
        pane = self.pane_transcript if self._active_pane == "transcript" else self.pane_translation
        pane.appendPlainText(text)
        scrollbar = pane.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # -- media playback -------------------------------------------------------
    @staticmethod
    def _is_video(path: str) -> bool:
        """True when ``path`` has a video extension (dedicated window)."""
        return Path(path).suffix.lower() in VIDEO_EXTENSIONS

    def _ensure_video_window(self) -> VideoPlayerWindow:
        """Create the video window once and wire its signals."""
        if self.video_window is not None:
            return self.video_window
        window = VideoPlayerWindow()
        window.play_toggled.connect(self._on_video_play_toggled)
        window.seek_released.connect(self._on_video_seek_released)
        window.closed.connect(self._on_video_window_closed)
        self.video_window = window
        return window

    def _toggle_play(self, path: str) -> None:
        """Play/pause ``path``; video files open the dedicated video window."""
        if self._playing_path == path:
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
                self.status_label.setText("Reproducción pausada.")
                if self._is_video(path):
                    self._sync_video_window_state(False)
            else:
                self.player.play()
                self.status_label.setText(f"Reproduciendo: {Path(path).stem}…")
                if self._is_video(path):
                    self._sync_video_window_state(True)
            return
        self._stop_playback(reset_status=False)
        self._playing_path = path
        # Reset the seek bar for the new source; durationChanged re-ranges it.
        self._seeking = False
        self._duration_ms = 0
        self.seek_slider.setValue(0)
        self.time_label.setText("00:00 / 00:00")
        if self._is_video(path):
            self._start_video(path)
        else:
            self.playback_bar.setVisible(True)
            self.player.setSource(QUrl.fromLocalFile(path))
            self.player.play()
            self.media_list.set_playing(path)
            self.status_label.setText(f"Reproduciendo: {Path(path).stem}…")

    def _start_video(self, path: str) -> None:
        """Route the shared player to the video window and start playback."""
        window = self._ensure_video_window()
        window.reset()
        window.set_source_title(path)
        self.player.setVideoOutput(window.video_widget)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self.media_list.set_playing(path)
        window.show()
        window.raise_()
        window.activateWindow()
        # Desired state comes from the action, not playbackState(): this
        # backend flips to PlayingState asynchronously after play().
        window.set_playing_state(True)
        self.status_label.setText(f"Reproduciendo: {Path(path).stem}…")

    def _sync_video_window_state(self, playing: bool) -> None:
        """Mirror the desired play state onto the window button (icon + checked)."""
        if self.video_window is not None:
            self.video_window.set_playing_state(playing)

    def _on_video_play_toggled(self, checked: bool) -> None:
        """The video window's button was clicked: toggle the shared player."""
        path = self._playing_path
        if path is None or not self._is_video(path):
            # No video active in the window; reset its button to stopped.
            if self.video_window is not None:
                self.video_window.set_playing_state(False)
            return
        if checked:
            self.player.play()
            self.status_label.setText(f"Reproduciendo: {Path(path).stem}…")
        else:
            self.player.pause()
            self.status_label.setText("Reproducción pausada.")
        self.media_list.set_playing(path)
        self._sync_video_window_state(checked)

    def _on_video_seek_released(self, value_ms: int) -> None:
        self.player.setPosition(value_ms)

    def _on_video_window_closed(self) -> None:
        """Titlebar X on the video window: stop playback for that source.

        ``_stop_playback`` hides the window (hide() fires no closeEvent), so
        this handler never re-enters through ``closed`` — no recursion guard
        is needed beyond the state check.
        """
        if self._playing_path is not None and self._is_video(self._playing_path):
            self._stop_playback()

    def _stop_playback(self, reset_status: bool = True) -> None:
        """Stop playback and restore the row's play icon. With
        ``reset_status`` also clears the status label. Any open video window
        is hidden and reset so the audio path is unaffected."""
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
        if self.video_window is not None:
            self.video_window.set_playing_state(False)
            self.video_window.hide()
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
        # Video playback transports in its own window; the main bar stays
        # hidden for videos (it reappears on the next audio source).
        if self._playing_path is None or not self._is_video(self._playing_path):
            self.playback_bar.setVisible(True)
        self._sync_time_label(self.seek_slider.value())
        if self.video_window is not None:
            self.video_window.set_duration(duration)

    def _on_position_changed(self, position: int) -> None:
        if self._seeking:
            return
        self.seek_slider.setValue(position)
        self._sync_time_label(position)
        if self.video_window is not None:
            self.video_window.update_position(position)

    def _on_seek_pressed(self) -> None:
        self._seeking = True

    def _on_seek_released(self) -> None:
        self._seeking = False
        self.player.setPosition(self.seek_slider.value())
        self._sync_time_label(self.seek_slider.value())

    # -- run results ----------------------------------------------------------
    def _on_done(self, result: RunResult) -> None:
        self._last_run = result
        if result.translation_text:
            # Translate produced both sides: original left, translation right.
            # Persist both so a later selection restores the original too.
            self.pane_translation.setPlainText(result.translation_text)
            if result.transcript_text:
                self.pane_transcript.setPlainText(result.transcript_text)
            self._persist_results(
                transcript_text=result.transcript_text,
                transcript_lang=result.transcript_lang,
                transcript_segments=result.transcript_segments,
                translation_text=result.translation_text,
                translation_lang=result.translation_lang,
                translation_segments=result.translation_segments,
            )
        else:
            self.pane_transcript.setPlainText(result.transcript_text)
            self._persist_results(
                transcript_text=result.transcript_text,
                transcript_lang=result.transcript_lang,
                transcript_segments=result.transcript_segments,
            )
        lang = result.translation_lang or result.transcript_lang
        self.status_label.setText(f"Completado. Idioma detectado: {lang or '?'}.")
        self._set_busy(False)

    def _persist_results(self, **fields) -> None:
        """Write the produced result fields for ``_running_path``.

        Segment lists become ``[s.to_dict() for s in ...]``; a None list is
        stored as ``[]`` (the produced kind always gets persisted). Fields not
        passed here are left untouched by :meth:`MediaRepository.update_results`.
        """
        if self._running_path is None:
            return
        idx = self.repository.index_of(self._running_path)
        if idx is None:
            return
        payload: dict = {}
        for name, value in fields.items():
            if name.endswith("_segments"):
                payload[name] = [s.to_dict() for s in value] if value is not None else []
            else:
                payload[name] = value
        self.repository.update_results(idx, **payload)

    def _on_selection_changed(self) -> None:
        """Restore the persisted results of the selected item, or clear panes."""
        path = self.media_list.selected_path()
        if path is None:
            self.pane_transcript.clear()
            self.pane_translation.clear()
            self._last_run = None
            return
        idx = self.repository.index_of(path)
        item = self.repository.get(idx) if idx is not None else None
        if item is not None and (item.transcript_text or item.translation_text):
            self.pane_transcript.setPlainText(item.transcript_text or "")
            self.pane_translation.setPlainText(item.translation_text or "")
            self._last_run = RunResult(
                transcript_text=item.transcript_text or "",
                transcript_lang=item.transcript_lang or "",
                transcript_segments=[
                    TimedSegment.from_dict(d) for d in item.transcript_segments
                ] if item.transcript_segments else None,
                translation_text=item.translation_text or "",
                translation_lang=item.translation_lang or "",
                translation_segments=[
                    TimedSegment.from_dict(d) for d in item.translation_segments
                ] if item.translation_segments else None,
            )
        else:
            self.pane_transcript.clear()
            self.pane_translation.clear()
            self._last_run = None
        self._refresh_buttons()

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
        lang = None
        if self._last_run is not None:
            lang = self._last_run.translation_lang or self._last_run.transcript_lang
        name = f"{stem}.{lang}.txt" if lang else f"{stem}.txt"
        return str(Path(default_dir) / name)

    def _start_save(self) -> None:
        if self._last_run is None:
            return
        segments = (
            self._last_run.translation_segments
            if self._last_run.translation_segments
            else self._last_run.transcript_segments
        )
        if not segments:
            return
        language = self._last_run.translation_lang or self._last_run.transcript_lang
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
            export_transcript(segments, base, language)
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
        self.btn_save.setEnabled(not busy and self._last_run is not None)

    def closeEvent(self, event) -> None:
        self._stop_playback()
        # No orphan top-level window: hide the (reusable) video window too,
        # even when nothing was playing.
        if self.video_window is not None:
            self.video_window.hide()
        # The worker may already be gone after a completed run
        # (finished → deleteLater): a deleted C++ object raises RuntimeError,
        # which here simply means "no longer running".
        if self._worker is not None:
            try:
                running = self._worker.isRunning()
            except RuntimeError:
                running = False
            if running:
                self.status_label.setText("Esperando a que termine la tarea…")
                self._worker.wait()
        super().closeEvent(event)