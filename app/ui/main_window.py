"""Main application window."""

from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
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
from app.services.exporter import (
    export as export_transcript,
    export_edited_text,
)
from app.services.pipeline import Pipeline
from app.services.transcriber import Transcriber
from app.storage.repository import MediaRepository
from app.ui.app_icon import header_logo_label
from app.ui.media_list import MediaList
from app.ui.seek_slider import SeekSlider
from app.ui.theme import apply_theme, load_dark_setting, save_dark_setting
from app.ui.video_player_window import VideoPlayerWindow
from app.ui.workers import InferenceWorker


def resolve_slice_position(
    bounds: tuple[int, int] | None, position_ms: int, loop: bool
) -> tuple[int, bool]:
    """Where playback goes when the player reports ``position_ms``.

    ``bounds`` is the loaded media's slice in milliseconds, or ``None`` when it
    has no slice. Returns ``(position_ms, keep_playing)``: the position to show
    and whether playback should continue.

    Three cases, and only three: without a slice nothing is bounded (that is the
    whole file playing freely); inside the slice the reported position stands;
    at or past the end playback jumps back to the start and either keeps going
    with the loop on or stops with it off.

    TWO boundary decisions worth naming. The end is ``end`` itself, not
    ``end - 1``: a millisecond is the player's own resolution and shaving one off
    would clip the tail of every slice. And "at or past" is one case, not two,
    because the player never stops exactly on the boundary -- it reports
    positions at its own cadence, so the first report after the end is already
    over it. Handling only ``== end`` would let the media play on.

    Pure, so the boundary arithmetic is testable without a player or a window.
    """
    if bounds is None or position_ms < bounds[1]:
        return position_ms, True
    return bounds[0], loop


def slice_entry_position(bounds: tuple[int, int] | None, position_ms: int) -> int:
    """Where playback STARTS when the user presses play.

    Separate from :func:`resolve_slice_position` because it answers a different
    question. That one is told where the player already is while it runs; this
    one is asked where to begin on a deliberate user action, when the player may
    legitimately be parked anywhere. The seek is free, so the user may have left
    it past the end on purpose, and pressing play must still start inside the
    slice rather than resume outside it.

    The start itself counts as inside, so a slice paused at its very first
    millisecond resumes instead of restarting; the end does not, so a player
    parked exactly there restarts rather than sitting still.

    Pure, for the same reason as :func:`resolve_slice_position`.
    """
    if bounds is None:
        return position_ms
    start, end = bounds
    return position_ms if start <= position_ms < end else start


def slice_scope_note(loaded: str | None, selected: str | None) -> str:
    """Why the painted slice will NOT scope a run of the selected media.

    Empty when there is nothing to explain. The slice painted in both players
    belongs to the LOADED media, whose duration is the denominator of its
    geometry; a run targets the SELECTED row, because the transcript and
    translation panes have always been per selection. The two are different
    files as soon as the user clicks another row while something is playing, and
    a slice of one can only scope a run of the other when the two ARE the same
    file. So the difference is stated, instead of silently running the whole of
    a media whose slice is painted on screen.

    With nothing loaded there is no divergence: the slice is the selected
    media's own stored one, no other file is on screen to be confused with, and
    the run-start status already says whether it is in force.

    Pure, for the same reason as :func:`resolve_slice_position`: the rule is
    about two paths and nothing else, so it does not need a window to be
    checked, and the previous inline version could only be reached by
    constructing one.
    """
    if loaded is None or selected is None or loaded == selected:
        return ""
    # Both sides named, short enough to sit in a status line. It reads as a
    # statement of fact, not as a warning about something the user did wrong:
    # selecting a row while something else plays is normal use.
    return (
        f"Slice de «{Path(loaded).stem}» (cargado): "
        f"no acota «{Path(selected).stem}», el seleccionado."
    )


class _EditableTextEdit(QPlainTextEdit):
    """Plain-text editor that marks programmatic document replacements.

    ``QPlainTextEdit.textChanged`` does not identify its source. The small
    override lets the window distinguish its own ``setPlainText``/streaming
    operations from edits made by the user, so restoring a run or a selected
    media file cannot become an autosave.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._programmatic_change = False

    @property
    def programmatic_change(self) -> bool:
        return self._programmatic_change

    def setPlainText(self, text: str) -> None:
        self._programmatic_change = True
        try:
            super().setPlainText(text)
        finally:
            self._programmatic_change = False

    def appendPlainText(self, text: str) -> None:
        self._programmatic_change = True
        try:
            super().appendPlainText(text)
        finally:
            self._programmatic_change = False

    def clear(self) -> None:
        self.setPlainText("")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Transcriptor — transcripción y traducción de audio")
        self.resize(920, 720)

        self.repository = MediaRepository()
        # No repository here: a run does not consume its slice, so the pipeline has
        # no reason to know about storage. This window owns persistence instead,
        # writing results and the slice back through self.repository.
        self.pipeline = Pipeline(Transcriber(), target_lang=TRANSLATE_TARGET_LANG)
        self._worker: InferenceWorker | None = None
        self._last_run: RunResult | None = None
        self._active_pane: str = "transcript"  # pane receiving live segments
        self._running_path: str | None = None  # path persisted on done
        self._scroll_sync_guard = False
        self._scroll_sync_eligible = False

        self._notes_programmatic_update = False
        self._pending_note_saves: dict[str, tuple[str, str]] = {}
        self._notes_save_timer = QTimer(self)
        self._notes_save_timer.setSingleShot(True)
        self._notes_save_timer.setInterval(500)
        self._notes_save_timer.timeout.connect(self._flush_notes_autosave)
        self._run_snapshot: tuple[str, str, RunResult | None] | None = None
        self._run_sides: tuple[str, ...] = ()

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self._playing_path: str | None = None
        self._seeking = False
        self._duration_ms = 0
        # Whether the run in flight was scoped by a slice: decided once, before
        # the worker starts, so the status line can name it on completion.
        self._run_used_slice = False
        # Whether playback loops the slice. UI state, never persisted: it is a
        # preference about THIS viewing session, not a property of the media,
        # and a stored loop would silently bound a run's media on the next open.
        self._loop_enabled = False
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
        # The mark sits left of the hint and takes a fixed 34px, so the hint has
        # that much less room to lay itself out. It stays the widget Qt shrinks
        # first (a non-wrapping QLabel reports a small minimumSizeHint), which is
        # why it is added before the stretch below rather than after.
        logo = header_logo_label()
        if logo is not None:
            header.addWidget(logo)
        hint = QLabel(
            "Arrastra vídeos o audios aquí · doble clic en el nombre: alias · "
            "clic en la anotación: editarla · edita los paneles libremente · "
            "botón derecho: menú"
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
        # source is loaded; durationChanged shows it). The slice is drawn by the
        # slider itself, on its groove, so there is nothing else to add here.
        self.playback_bar = QWidget()
        bar_layout = QHBoxLayout(self.playback_bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(8)
        self.seek_slider = SeekSlider()
        self.time_label = QLabel("00:00 / 00:00")
        bar_layout.addWidget(self.seek_slider, stretch=1)
        # Same position as in the video window, immediately left of the clock, so
        # the control does not move under the user when the two views swap. It is
        # a plain checkable button rather than a checkbox because it is a
        # transport control, not a run option: it changes playback, not the run.
        self.btn_loop = QPushButton("⟳")
        self.btn_loop.setObjectName("loopButton")
        self.btn_loop.setCheckable(True)
        self.btn_loop.setAccessibleName("Repetir el slice en bucle")
        self.btn_loop.setToolTip("Repetir el slice en bucle (A-B)")
        self.btn_loop.toggled.connect(self._on_loop_toggled)
        self.btn_loop.setEnabled(False)
        bar_layout.addWidget(self.btn_loop)
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
        self.chk_scroll_sync = QCheckBox("Sincronizar scroll")
        self.chk_scroll_sync.setAccessibleName(
            "Sincronizar el desplazamiento vertical de transcripción y traducción"
        )
        self.chk_scroll_sync.setToolTip(
            "Mantiene ambos paneles en la misma posición vertical proporcional."
        )
        self.chk_scroll_sync.setChecked(False)
        self.chk_scroll_sync.setEnabled(False)
        self.btn_transcribe = QPushButton("Transcribir")
        self.btn_transcribe.setObjectName("primaryButton")
        self.btn_translate = QPushButton("Traducir")
        self.btn_save = QPushButton("Guardar TXT + SRT…")
        self.btn_save.setEnabled(False)
        self.btn_save_edited = QPushButton("Guardar texto editado…")
        self.btn_save_edited.setEnabled(False)
        buttons.addWidget(self.chk_diarize)
        buttons.addWidget(self.chk_scroll_sync)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_transcribe)
        buttons.addWidget(self.btn_translate)
        buttons.addWidget(self.btn_save)
        buttons.addWidget(self.btn_save_edited)
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

    def _make_pane(
        self, title: str, placeholder: str
    ) -> tuple[QWidget, _EditableTextEdit]:
        """Create a labeled, freely editable multiline text pane."""
        pane = QWidget()
        column = QVBoxLayout(pane)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        header = QLabel(title)
        header.setObjectName("paneLabel")
        edit = _EditableTextEdit()
        edit.setReadOnly(False)
        edit.setPlaceholderText(placeholder)
        edit.setAccessibleName(title)
        column.addWidget(header)
        column.addWidget(edit, stretch=1)
        return pane, edit

    def _pane_for(self, side: str) -> _EditableTextEdit:
        return self.pane_transcript if side == "transcript" else self.pane_translation

    def _set_pane_text(self, side: str, text: str) -> None:
        """Replace a pane without turning the replacement into a user edit."""
        self._notes_programmatic_update = True
        try:
            self._pane_for(side).setPlainText(text)
        finally:
            self._notes_programmatic_update = False

    def _on_pane_text_changed(self, side: str) -> None:
        """Queue a debounced autosave for a real user edit."""
        pane = self._pane_for(side)
        if self._notes_programmatic_update or pane.programmatic_change:
            return
        path = self.media_list.selected_path()
        if path is None:
            return
        self._pending_note_saves[side] = (path, pane.toPlainText())
        self._notes_save_timer.start()
        self._refresh_scroll_sync()

    def _flush_notes_autosave(self) -> None:
        """Persist pending edits, including empty strings, for their media."""
        self._notes_save_timer.stop()
        if not self._pending_note_saves:
            return
        pending = self._pending_note_saves
        self._pending_note_saves = {}

        by_path: dict[str, dict[str, str]] = {}
        for side, (path, text) in pending.items():
            by_path.setdefault(path, {})[side] = text

        for path, values in by_path.items():
            index = self.repository.index_of(path)
            if index is None:
                continue
            self.repository.update_edited_text(
                index,
                transcript_text=values.get("transcript"),
                translation_text=values.get("translation"),
            )

    def _refresh_scroll_sync(self) -> None:
        has_both_text = self._scroll_sync_eligible and bool(
            self.pane_transcript.toPlainText()
            and self.pane_translation.toPlainText()
        )
        self.chk_scroll_sync.setEnabled(has_both_text)
        if not has_both_text:
            self.chk_scroll_sync.setChecked(False)

    def _on_scroll_sync_toggled(self, checked: bool) -> None:
        if checked:
            # Align from the pane the user is currently viewing, not always
            # from the transcript. This keeps the initial toggle intuitive
            # when the translation was the last pane scrolled.
            transcript_bar = self.pane_transcript.verticalScrollBar()
            translation_bar = self.pane_translation.verticalScrollBar()
            if abs(transcript_bar.value() / transcript_bar.maximum()) >= abs(
                translation_bar.value() / translation_bar.maximum()
            ):
                source, target = self.pane_transcript, self.pane_translation
            else:
                source, target = self.pane_translation, self.pane_transcript
            self._synchronize_scroll(source, target, source.verticalScrollBar().value())

    def _synchronize_scroll(
        self,
        source: QPlainTextEdit,
        target: QPlainTextEdit,
        value: int,
    ) -> None:
        if (
            not self.chk_scroll_sync.isChecked()
            or not self.chk_scroll_sync.isEnabled()
            or self._scroll_sync_guard
            or self.pane_transcript.document().characterCount() <= 1
            or self.pane_translation.document().characterCount() <= 1
        ):
            return
        source_bar = source.verticalScrollBar()
        target_bar = target.verticalScrollBar()
        maximum = source_bar.maximum()
        ratio = value / maximum if maximum > 0 else 0.0
        self._scroll_sync_guard = True
        try:
            target_bar.setValue(round(ratio * target_bar.maximum()))
        finally:
            self._scroll_sync_guard = False

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
        self.seek_slider.slice_edited.connect(self._on_slice_edited)
        self.btn_transcribe.clicked.connect(self._start_transcribe)
        self.btn_translate.clicked.connect(self._start_translate)
        self.btn_save.clicked.connect(self._start_save)
        self.btn_save_edited.clicked.connect(self._start_save_edited)
        self.btn_theme.clicked.connect(self._toggle_theme)
        self.chk_scroll_sync.toggled.connect(self._on_scroll_sync_toggled)
        self.pane_transcript.textChanged.connect(
            lambda: self._on_pane_text_changed("transcript")
        )
        self.pane_translation.textChanged.connect(
            lambda: self._on_pane_text_changed("translation")
        )
        self.pane_transcript.verticalScrollBar().valueChanged.connect(
            lambda value: self._synchronize_scroll(
                self.pane_transcript, self.pane_translation, value
            )
        )
        self.pane_translation.verticalScrollBar().valueChanged.connect(
            lambda value: self._synchronize_scroll(
                self.pane_translation, self.pane_transcript, value
            )
        )

    # -- theme ---------------------------------------------------------------
    def _toggle_theme(self) -> None:
        self._dark = not self._dark
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, self._dark)
        # The slice is custom painting, so the theme only hands it new
        # colors; re-pushing it is what repaints the open players with them
        # right away, even when nothing else on screen is moving.
        self._push_slice_to_players(self.seek_slider.slice())
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

    @staticmethod
    def _run_sides_for(pane: str) -> tuple[str, ...]:
        return ("transcript", "translation") if pane == "translation" else ("transcript",)

    def _confirm_replacing_notes(self, item, sides: tuple[str, ...]) -> bool:
        fields = {
            "transcript": "transcript_edited_text",
            "translation": "translation_edited_text",
        }
        edited_sides = [side for side in sides if getattr(item, fields[side], None) is not None]
        if not edited_sides:
            return True
        answer = QMessageBox.question(
            self,
            "Reemplazar texto editado",
            "Hay texto editado en este medio. La nueva inferencia lo reemplazará "
            "solo si termina correctamente. ¿Quieres continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _run(self, fn, pane: str) -> None:
        self._flush_notes_autosave()
        path = self.media_list.selected_path()
        if path is None:
            QMessageBox.information(self, "Selección", "Selecciona un archivo de la lista.")
            return
        index = self.repository.index_of(path)
        item = self.repository.get(index) if index is not None else None
        run_sides = self._run_sides_for(pane)
        if item is not None and not self._confirm_replacing_notes(item, run_sides):
            return
        # The slice is an INPUT of this run, not UI state: read it here,
        # before the worker starts, so the decision cannot be changed between
        # reading and running. None means no slice and scopes nothing.
        #
        # The run targets the SELECTED row, because the transcript and
        # translation panes have always been per selection. The slice painted in
        # both players belongs to the LOADED media instead, because its geometry
        # is a fraction of the loaded media's duration. Those are two different
        # files as soon as the user clicks another row while something is
        # playing, and the slice of one can only scope a run of the other when
        # the two ARE the same file, so a diverged run is unscoped and says so
        # (see _slice_scope_note).
        #
        # _playing_path is read HERE, on purpose: _stop_playback() below sets it
        # to None, so after that line the loaded media is unknowable.
        diverged = self._playing_path is not None and self._playing_path != path
        scope_note = self._slice_scope_note()
        stored = item.slice if (item is not None and not diverged) else None
        # One pair, one range: the engines still take a LIST of ranges, so the
        # slice is wrapped rather than spread. A run with no slice passes None,
        # which every one of them reads as the whole file.
        ranges = [tuple(stored)] if stored else None
        self._run_used_slice = bool(ranges)

        self._stop_playback(reset_status=False)
        diarize = self.chk_diarize.isChecked()
        self._running_path = path
        self._active_pane = pane
        self._run_sides = run_sides
        self._run_snapshot = (
            self.pane_transcript.toPlainText(),
            self.pane_translation.toPlainText(),
            self._last_run,
        )
        self._scroll_sync_eligible = False
        self._last_run = None
        # Clear only the pane being produced; the other one keeps its content.
        self._set_pane_text(pane, "")
        self._refresh_scroll_sync()
        self.progress.setValue(0)
        # A scoped run returns only part of the media, so the status says so
        # before the first segment arrives instead of surprising the user with a
        # short transcript. The parenthesised scope is ALWAYS there now: a run
        # with no slice is the whole file, and saying only "Iniciando…" left the
        # user unable to tell the two cases apart while a slice was on screen.
        # The note then says WHY that slice is not in force.
        scope = "solo el slice" if ranges else "archivo completo"
        start_text = f"Iniciando… ({scope})"
        if scope_note:
            start_text = f"{start_text}. {scope_note}"
        self.status_label.setText(start_text)
        self._set_busy(True)

        def runnable(path, on_segment, on_progress, on_status) -> RunResult:
            return fn(
                path,
                on_segment=on_segment,
                on_progress=on_progress,
                on_status=on_status,
                diarize=diarize,
                ranges=ranges,
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
        pane = self._pane_for(self._active_pane)
        self._notes_programmatic_update = True
        try:
            pane.appendPlainText(text)
        finally:
            self._notes_programmatic_update = False
        scrollbar = pane.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        self._refresh_scroll_sync()

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
        window.slice_changed.connect(self._on_slice_edited)
        window.loop_toggled.connect(self._on_loop_toggled)
        self.video_window = window
        # The window existed before this signal was wired, so its loop button has
        # not been mirrored yet; seed it from the owner instead of waiting for the
        # user to click it.
        window.set_loop_state(self._loop_enabled, self._slice_bounds_ms() is not None)
        return window

    def _slice_bounds_ms(self) -> tuple[int, int] | None:
        """The loaded media's slice in milliseconds, or ``None`` when unbounded.

        Read from the slider rather than from the selected row, because playback
        is bound to what the slider shows: the loaded media's own slice. Reading
        the row instead would enforce one slice while showing another, which is
        exactly the range/slice disagreement this project keeps fixing.

        Zero-length is normalised to ``None`` at every layer below, so an
        inverted or degenerate pair can never produce a position outside the
        media.
        """
        seconds = self.seek_slider.slice()
        if not seconds:
            return None
        start, end = seconds
        return (int(start * 1000), int(end * 1000))

    def _enter_slice_on_play(self) -> None:
        """Move the player inside the slice before a deliberate play.

        Called from the play paths only, never from the seek paths: seeking is
        free, so the user may park the player past the end on purpose, and only
        pressing play is a request to hear the slice. Without this, resuming from
        a position outside the slice would either start where it should not or,
        at the end itself, refuse to start at all.
        """
        bounds = self._slice_bounds_ms()
        if bounds is None:
            return
        target = slice_entry_position(bounds, self.player.position())
        if target != self.player.position():
            self.player.setPosition(target)

    def _on_loop_toggled(self, enabled: bool) -> None:
        """Either loop button was clicked: adopt the state and mirror it."""
        self._loop_enabled = bool(enabled)
        # The signal arrives from the button that was clicked, so that one is
        # already correct and only the OTHER window needs the write. Blocking
        # signals inside the mirror is what stops this from bouncing.
        self.btn_loop.blockSignals(True)
        try:
            self.btn_loop.setChecked(self._loop_enabled)
        finally:
            self.btn_loop.blockSignals(False)
        if self.video_window is not None:
            self.video_window.set_loop_state(
                self._loop_enabled, self._slice_bounds_ms() is not None
            )

    def _refresh_loop_availability(self) -> None:
        """Enable the loop control only where a slice exists for it to loop."""
        available = self._slice_bounds_ms() is not None
        self.btn_loop.setEnabled(available)
        if self.video_window is not None:
            self.video_window.set_loop_state(self._loop_enabled, available)

    def _toggle_play(self, path: str) -> None:
        """Play/pause ``path``; video files open the dedicated video window."""
        if self._playing_path == path:
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
                self.status_label.setText("Reproducción pausada.")
                if self._is_video(path):
                    self._sync_video_window_state(False)
            else:
                self._enter_slice_on_play()
                self.player.play()
                self.status_label.setText(f"Reproduciendo: {Path(path).stem}…")
                if self._is_video(path):
                    self._sync_video_window_state(True)
            return
        self._stop_playback(reset_status=False)
        self._playing_path = path
        # Reset the timeline for the new source; _load_source re-establishes it
        # for this media (a re-entry of the same file announces no new duration,
        # so the re-range cannot wait for a signal). Nothing of the previous
        # source may survive into this one: no range, no painted slice.
        self._seeking = False
        self._establish_timeline(0)
        if self._is_video(path):
            self._start_video(path)
        else:
            self.playback_bar.setVisible(True)
            self._load_source(path)
            self._enter_slice_on_play()
            self.player.play()
            self.media_list.set_playing(path)
            self.status_label.setText(f"Reproduciendo: {Path(path).stem}…")

    def _start_video(self, path: str) -> None:
        """Route the shared player to the video window and start playback."""
        window = self._ensure_video_window()
        window.reset()
        window.set_source_title(path)
        self.player.setVideoOutput(window.video_widget)
        self._load_source(path)
        self._enter_slice_on_play()
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
            self._enter_slice_on_play()
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
        self.playback_bar.setVisible(False)
        if self.video_window is not None:
            self.video_window.set_playing_state(False)
            self.video_window.hide()
        # No media is loaded any more, so no slice can be painted: one left over
        # would otherwise leak into the next source. The clearing goes through
        # _establish_timeline(0) because that is the ONLY place that ranges a
        # slider: assigning _duration_ms here left both bars ranged to the media
        # that had just stopped while the slice was gone, i.e. range and slice
        # disagreeing again -- the very invariant this method exists to keep.
        self._establish_timeline(0)
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
        # Qt refines the duration while the media loads. The timeline is
        # rebuilt from the same single place the hand-over call uses, so range
        # and slice cannot be set by two different paths.
        self._establish_timeline(duration)

    def _load_source(self, path: str) -> None:
        """Hand ``path`` to the player and re-establish the timeline for it.

        Every ``setSource`` in this window goes through here. That is what makes
        the timeline follow the LOADED media even when the player stays silent
        about it; see :meth:`_establish_timeline` for the measurement behind
        that.
        """
        self.player.setSource(QUrl.fromLocalFile(path))
        self._establish_timeline()

    def _establish_timeline(self, duration: int | None = None) -> None:
        """Rebuild the seek range AND the slice painting from one duration.

        The only place that ranges a slider, and it paints the slice in the same
        breath from the same duration and the same ``_playing_path``. Both views
        of the timeline therefore share one denominator and cannot drift apart,
        and a media whose duration is unknown gets neither a range nor a slice:
        there is nothing to place a handle against.

        ``duration=None`` asks the player instead of trusting an announced value.

        WHY THE CALLER MUST ASK, NOT ONLY LISTEN. The bug this replaces was a
        video window that stopped advancing its timeslider, and stopped showing
        the slice, after re-entering a file it had already played: ``reset()``
        left the range at ``0..0``, ``update_position`` clamped the value to 0
        and ``_load_slice_for`` never ran, so both symptoms were the single fact
        that ``_on_duration_changed`` was the only thing that could restore the
        timeline. MEASURED on PySide6 6.11.2 with the ffmpeg backend, offscreen,
        for both a 30 s wav and the 292 s MKV: setting a source that is ALREADY
        the player's source emits NO LoadingMedia, NO sourceChanged, NO
        durationChanged and NO LoadedMedia -- only BufferingMedia /
        BufferedMedia plus the transition to PlayingState. The backend keeps the
        loaded media, so ``player.duration()`` still answers correctly on the
        very next line, but nothing announces it.

        Forcing a real source change (``setSource(QUrl())`` before the real URL)
        does bring durationChanged back, verified, but it re-probes the file on
        every entry and throws the timeline away for one event, so the app asks
        the player for the duration it already has instead of making it work for
        a notification.

        The same measurement settles the other half, WHY IT IS SAFE TO ASK
        UNCONDITIONALLY: on a genuinely NEW source ``player.duration()`` answers
        0 on the very next line -- never the previous media's value -- and
        durationChanged arrives with the real one shortly after. So asking can
        never place the new media's slice along the old media's timeline; the
        worst it does is range nothing for the fraction of a second before the
        signal lands, and a media whose duration is genuinely unknown gets no
        range and no slice, which is the truth.
        """
        known = int(self.player.duration()) if duration is None else int(duration)
        self._duration_ms = max(0, known)
        self.seek_slider.setRange(0, self._duration_ms)
        if self.video_window is not None:
            self.video_window.set_duration(self._duration_ms)
        # The slice is read for the LOADED media, which is the media whose
        # duration just became the denominator of its geometry, and it is cleared
        # outright when there is no timeline to place a handle on.
        self._load_slice_for(self._playing_path if self._duration_ms > 0 else None)
        if self._duration_ms > 0 and (
            self._playing_path is None or not self._is_video(self._playing_path)
        ):
            # Video playback transports in its own window; the main bar stays
            # hidden for videos (it reappears on the next audio source).
            self.playback_bar.setVisible(True)
        self._sync_time_label(self.seek_slider.value())

    def _enforce_slice_boundary(self, position: int) -> tuple[int, bool]:
        """Apply the slice boundary to ``position`` and report what happened.

        Returns ``(resolved_position, moved)``. ``moved`` is False when the
        position was already legal, which is what lets the caller skip the UI
        work on the overwhelmingly common in-slice case.

        One caller, one body. ``_on_position_changed`` reacts to the player's own
        notifications, and that is the whole enforcement path: the boundary is
        applied from that signal alone. A 100 ms poll of ``player.position()``
        used to run alongside it as a safety net, on the theory that the player's
        notification cadence is undocumented and could therefore leave the end
        slack. Measured on a real file the poll changed nothing: both paths
        overshoot by the same 32-43 ms, which is the player's own
        position-reporting quantum (42-76 ms). So it was removed rather than kept
        on a promise it did not keep.
        """
        target, keep_playing = resolve_slice_position(
            self._slice_bounds_ms(), position, self._loop_enabled
        )
        if target == position:
            return target, False
        # Reached or passed the end of the slice. The order here is load bearing:
        # rewind FIRST, then pause. Pausing first would let the player report one
        # more position past the end, and the loop would bounce to the end again
        # instead of settling on the start.
        self.player.setPosition(target)
        if not keep_playing:
            self.player.pause()
            self.status_label.setText("Fin del slice.")
            self._sync_video_window_state(False)
        return target, True

    def _on_position_changed(self, position: int) -> None:
        if self._seeking:
            return
        target, _moved = self._enforce_slice_boundary(position)
        # The slider and both clocks show the resolved position, not the reported
        # one, so the display cannot sit past the end the player already left.
        self.seek_slider.setValue(target)
        self._sync_time_label(target)
        if self.video_window is not None:
            self.video_window.update_position(target)

    def _on_seek_pressed(self) -> None:
        self._seeking = True

    def _on_seek_released(self) -> None:
        self._seeking = False
        self.player.setPosition(self.seek_slider.value())
        self._sync_time_label(self.seek_slider.value())

    # -- slice ---------------------------------------------------------------
    def _push_slice_to_players(self, value) -> None:
        """Show the same slice in both players, over the current duration.

        The slice belongs to one media, so the audio bar and the video window
        must always agree: one of them is where the user just dragged and the
        other is the mirror. ``value`` is already normalized by the slider; the
        duration is the denominator of the handle geometry, so both widgets get
        the same one and stay in step.

        This cannot re-enter :meth:`_on_slice_edited`: only a user drag emits
        ``slice_edited``, and ``set_slice`` does not.
        """
        self.seek_slider.set_slice(value, self._duration_ms)
        if self.video_window is not None:
            self.video_window.set_slice(value, self._duration_ms)
        # A slice appearing or disappearing changes whether the loop has
        # anything to loop, so the control's availability follows the slice.
        self._refresh_loop_availability()

    def _slice_scope_note(self) -> str:
        """Why the painted slice will NOT scope a run of the selected media.

        Delegates to :func:`slice_scope_note`, which holds the rule; this only
        supplies the two paths it needs. The rule is worth testing without a
        window, and it was previously buried in a method that could only be
        reached by constructing a ``MainWindow``.
        """
        return slice_scope_note(self._playing_path, self.media_list.selected_path())

    def _load_slice_for(self, path: str | None) -> None:
        """Show the stored slice of ``path`` in both players.

        Called once the duration is known, because the duration is what places
        the handles on the timeline. ``None`` (nothing loaded, or not registered)
        means no slice, which is also the state a media without a slice stores.
        """
        value = None
        if path is not None:
            index = self.repository.index_of(path)
            item = self.repository.get(index) if index is not None else None
            if item is not None:
                value = item.slice
        self._push_slice_to_players(value)

    def _on_slice_edited(self, value, message: str) -> None:
        """Persist a slice edit made in either player and mirror it to both.

        The slice belongs to the media whose timeline is LOADED IN THE PLAYER
        (``self._playing_path``), not to the list selection: clicking a play
        icon does not change the selection, so the two can be different files,
        and the bar the user dragged on shows the loaded one. Writing it to the
        selection would store it against a media nobody is looking at.

        ``None`` is a real write, not a no-op: it is how returning both handles
        to the extremes deletes the slice.

        A run in flight cannot race this write: ``_set_busy`` blocks handle
        dragging, so no edit can be made while the worker is reading the item.
        """
        path = self._playing_path
        if path is None:
            # Belt and braces: handles are ungrabbable without a duration.
            return
        index = self.repository.index_of(path)
        if index is not None:
            self.repository.update_slice(index, value)
        self._push_slice_to_players(value)
        self.status_label.setText(message)

    # -- run results ----------------------------------------------------------
    @staticmethod
    def _stored_run_result(item) -> RunResult | None:
        """Rebuild base-result metadata without losing empty values."""
        fields = (
            "transcript_text",
            "transcript_lang",
            "transcript_segments",
            "translation_text",
            "translation_lang",
            "translation_segments",
        )
        if all(getattr(item, name, None) is None for name in fields):
            return None
        return RunResult(
            transcript_text=item.transcript_text or "",
            transcript_lang=item.transcript_lang or "",
            transcript_segments=(
                [TimedSegment.from_dict(d) for d in item.transcript_segments]
                if item.transcript_segments is not None
                else None
            ),
            translation_text=item.translation_text or "",
            translation_lang=item.translation_lang or "",
            translation_segments=(
                [TimedSegment.from_dict(d) for d in item.translation_segments]
                if item.translation_segments is not None
                else None
            ),
        )

    def _clear_successful_run_notes(self, path: str | None, sides: tuple[str, ...]) -> None:
        """Clear replaced note fields only after a successful inference."""
        if path is None:
            return
        index = self.repository.index_of(path)
        if index is None:
            return
        item = self.repository.get(index)
        fields = {
            "transcript": "transcript_edited_text",
            "translation": "translation_edited_text",
        }
        if not any(getattr(item, fields[side], None) is not None for side in sides):
            return
        self.repository.clear_edited_text(
            index,
            transcript="transcript" in sides,
            translation="translation" in sides,
        )

    def _on_done(self, result: RunResult) -> None:
        self._last_run = result
        running_path = self._running_path
        run_sides = self._run_sides or self._run_sides_for(self._active_pane)

        if self._active_pane == "translation":
            # Translate produced both sides: original left, translation right.
            # Persist both so a later selection restores the original too.
            self._set_pane_text("transcript", result.transcript_text)
            self._set_pane_text("translation", result.translation_text)
            self._persist_results(
                transcript_text=result.transcript_text,
                transcript_lang=result.transcript_lang,
                transcript_segments=result.transcript_segments,
                translation_text=result.translation_text,
                translation_lang=result.translation_lang,
                translation_segments=result.translation_segments,
            )
        else:
            self._set_pane_text("transcript", result.transcript_text)
            self._persist_results(
                transcript_text=result.transcript_text,
                transcript_lang=result.transcript_lang,
                transcript_segments=result.transcript_segments,
            )

        # The note fields are deliberately cleared only now, after the worker
        # has produced a result successfully.
        self._clear_successful_run_notes(running_path, run_sides)
        self._scroll_sync_eligible = bool(
            self.pane_transcript.toPlainText() and self.pane_translation.toPlainText()
        )
        self._refresh_scroll_sync()
        lang = result.translation_lang or result.transcript_lang
        used_slice = self._run_used_slice
        # The slice is NOT consumed by a run, so re-reading it here changes
        # nothing on screen: it is kept so the painted handles can only ever
        # come from storage, never from a value this window held on to.
        self._load_slice_for(self._playing_path)
        text = f"Completado. Idioma detectado: {lang or '?'}."
        if used_slice:
            text += " Slice aplicado."
        self.status_label.setText(text)
        self._running_path = None
        self._run_snapshot = None
        self._run_sides = ()
        self._run_used_slice = False
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
        """Restore edited notes over the original base results, if present.

        It also announces a slice/selection divergence the moment it exists.
        The slice stays painted, because it is the LOADED media's and its
        geometry is true for what is loaded -- clearing it would be a lie in the
        other direction ("this media has no slice") and would take away the
        user's ability to edit or remove it. Leaving it up silently is what made
        the run read as scoped by a slice that was not scoping it, so the status
        line names the two files instead. :meth:`_run` repeats the same note at
        run start, when the scope actually matters.
        """
        self._flush_notes_autosave()
        path = self.media_list.selected_path()
        self._scroll_sync_eligible = False
        if path is None:
            self._set_pane_text("transcript", "")
            self._set_pane_text("translation", "")
            self._last_run = None
            self._refresh_scroll_sync()
            self._refresh_buttons()
            return
        idx = self.repository.index_of(path)
        item = self.repository.get(idx) if idx is not None else None
        if item is not None:
            transcript_text = (
                item.transcript_edited_text
                if item.transcript_edited_text is not None
                else item.transcript_text or ""
            )
            translation_text = (
                item.translation_edited_text
                if item.translation_edited_text is not None
                else item.translation_text or ""
            )
            self._set_pane_text("transcript", transcript_text)
            self._set_pane_text("translation", translation_text)
            self._last_run = self._stored_run_result(item)
        else:
            self._set_pane_text("transcript", "")
            self._set_pane_text("translation", "")
            self._last_run = None
        self._scroll_sync_eligible = bool(
            self.pane_transcript.toPlainText() and self.pane_translation.toPlainText()
        )
        self._refresh_scroll_sync()
        self._refresh_buttons()
        note = self._slice_scope_note()
        if note:
            self.status_label.setText(note)

    def _on_failed(self, message: str) -> None:
        if self._run_snapshot is not None:
            transcript_text, translation_text, last_run = self._run_snapshot
            self._set_pane_text("transcript", transcript_text)
            self._set_pane_text("translation", translation_text)
            self._last_run = last_run
        self._scroll_sync_eligible = bool(
            self.pane_transcript.toPlainText() and self.pane_translation.toPlainText()
        )
        self._refresh_scroll_sync()
        # A failed run changed nothing, so the slice is still stored: re-read
        # it and repaint, and the UI can never disagree with storage.
        self._load_slice_for(self._playing_path)
        self._running_path = None
        self._run_snapshot = None
        self._run_sides = ()
        self._run_used_slice = False
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

    def _suggested_edited_save_path(self) -> str:
        source = self.media_list.selected_path()
        if source:
            media_path = Path(source)
            default_dir = str(media_path.parent)
            stem = media_path.stem
        else:
            default_dir = str(Path.home())
            stem = "transcripcion"
        return str(Path(default_dir) / f"{stem}.edited.txt")

    def _start_save_edited(self) -> None:
        if self.media_list.selected_path() is None:
            return
        captured, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Guardar texto editado como…",
            self._suggested_edited_save_path(),
            "Texto (*.txt)",
        )
        if not captured:
            return
        txt_path = Path(captured)
        if txt_path.suffix.lower() != ".txt":
            txt_path = txt_path.with_suffix(".txt")
        try:
            export_edited_text(
                self.pane_transcript.toPlainText(),
                self.pane_translation.toPlainText(),
                str(txt_path),
            )
        except Exception as exc:
            self.status_label.setText("Error al guardar.")
            QMessageBox.critical(
                self, "Error", f"No se pudo guardar el texto editado.\n\n{exc}"
            )
            return
        self.status_label.setText(f"Guardado: {txt_path}")

    def _set_busy(self, busy: bool) -> None:
        self.media_list.setEnabled(not busy)
        self.pane_transcript.setEnabled(not busy)
        self.pane_translation.setEnabled(not busy)
        # Not cosmetics: a run in flight is reading this item, and the
        # repository has no lock. Blocking the edit for the whole run keeps a
        # slice written by the UI from contradicting the run the status line is
        # about to report.
        self.seek_slider.set_slice_editable(not busy)
        if self.video_window is not None:
            self.video_window.set_slice_editable(not busy)
        self._refresh_buttons(busy)

    def _refresh_buttons(self, busy: bool = False) -> None:
        has_selection = self.media_list.selected_path() is not None
        self.btn_transcribe.setEnabled(not busy and has_selection)
        self.btn_translate.setEnabled(not busy and has_selection)
        self.btn_save.setEnabled(not busy and self._last_run is not None)
        self.btn_save_edited.setEnabled(not busy and has_selection)
        self._refresh_scroll_sync()

    def closeEvent(self, event) -> None:
        self._flush_notes_autosave()
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