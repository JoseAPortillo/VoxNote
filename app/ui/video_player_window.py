"""Dedicated top-level window that plays video media.

The window NEVER touches QMediaPlayer directly: MainWindow owns the shared
player and orchestrates playback through the signals (out) and slots (in)
exposed here. The instance is reused across plays — closeEvent only hides
it (no WA_DeleteOnClose), so a later video play can show it again.
"""

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QRegion
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from app.ui.seek_slider import MarksStrip, SeekSlider


class VideoPlayerWindow(QWidget):
    """Top-level video surface: QVideoWidget + play/pause + SeekSlider + time.

    The in/out marks of the loaded media are shaded on the slider and mirrored
    as ``{``/``}`` braces on the strip under it; both are driven from here so
    this window shows exactly what the main window's bar shows.

    Signals:
        play_toggled(bool): the user clicked the play/pause button; MainWindow
            toggles the shared player to match.
        seek_pressed(): the user pressed the slider (click or drag start).
        seek_released(int): the user released the slider; seek to this ms.
        closed(): the window was closed via the titlebar X.
        marks_changed(object, object, str): the user edited the in/out marks
            from the slider's context menu. The payload is the slider's
            ``marks_edited``: ``(marks, pending_in, message)``.

    Slots (called by MainWindow to sync the UI to the player state):
        set_source_title, set_duration, update_position, set_playing_state,
        reset, set_marks, set_marks_editable.
    """

    play_toggled = Signal(bool)
    seek_pressed = Signal()
    seek_released = Signal(int)
    closed = Signal()
    marks_changed = Signal(object, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("videoPlayerWindow")
        self.setWindowTitle("Video")
        self.resize(640, 420)
        self._duration_ms = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.video_widget = QVideoWidget(self)
        layout.addWidget(self.video_widget, stretch=1)

        controls = QWidget(self)
        controls.setObjectName("videoControls")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(8, 6, 8, 6)
        controls_layout.setSpacing(4)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)

        self.play_button = QPushButton(controls)
        self.play_button.setObjectName("playButton")
        self.play_button.setCheckable(True)
        self.play_button.setIconSize(QSize(16, 16))
        self.play_button.setFixedSize(44, 30)
        self.play_button.setAccessibleName("Reproducir o pausar video")
        self.play_button.setToolTip("Reproducir / Pausar")
        # Signal-to-signal: a user click flips the checkable button, which
        # re-emits as play_toggled. Programmatic syncs go through
        # set_playing_state(), which blocks the button's signals.
        self.play_button.toggled.connect(self.play_toggled)
        bar.addWidget(self.play_button)

        self.slider = SeekSlider(controls)
        self.slider.sliderPressed.connect(self.seek_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        # Signal-to-signal: MainWindow owns persistence and mirrors the edit to
        # the other player, so the window only relays the payload.
        self.slider.marks_edited.connect(self.marks_changed)
        bar.addWidget(self.slider, stretch=1)

        self.time_label = QLabel("00:00 / 00:00", controls)
        self.time_label.setObjectName("videoTimeLabel")
        bar.addWidget(self.time_label)

        controls_layout.addLayout(bar)
        # The braces under the groove: a read-only mirror of the same rects the
        # slider shades, so the marked stretches stay readable in this window.
        self.marks_strip = MarksStrip(self.slider, controls)
        controls_layout.addWidget(self.marks_strip)

        layout.addWidget(controls)
        # Blank state: no source loaded yet (QSlider would default to 0..99).
        self.slider.setRange(0, 0)
        self.set_playing_state(False)

    # -- API called by MainWindow -------------------------------------------
    def set_source_title(self, path: str) -> None:
        """Show the source file name as the window title."""
        self.setWindowTitle(Path(path).name)

    def set_duration(self, ms: int) -> None:
        """Re-range the slider to the new total duration and refresh the label."""
        self._duration_ms = max(0, int(ms))
        self.slider.setRange(0, self._duration_ms)
        self._sync_label()

    def update_position(self, ms: int) -> None:
        """Follow the player position; no-op while the user drags the slider."""
        if self.slider.isSliderDown():
            return
        self.slider.setValue(max(0, int(ms)))
        self._sync_label()

    def set_playing_state(self, playing: bool) -> None:
        """Sync the button to the actual player state.

        Signals are blocked while checking so a programmatic sync never
        re-enters MainWindow through ``play_toggled``. Checked (playing) shows
        the pause glyph on the accent background — the click pauses.
        """
        self.play_button.blockSignals(True)
        try:
            self.play_button.setChecked(playing)
        finally:
            self.play_button.blockSignals(False)
        if playing:
            self.play_button.setIcon(self._white_icon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self.play_button.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
            )

    def reset(self) -> None:
        """Clear position, duration, play state and marks (before a new source).

        The marks go with it because they belong to the media that was loaded;
        MainWindow paints the new media's own marks once its duration is known.
        """
        self._duration_ms = 0
        self.slider.setRange(0, 0)
        self.slider.setValue(0)
        self.time_label.setText("00:00 / 00:00")
        self.set_playing_state(False)
        self.set_marks(None, 0)

    def set_marks(
        self, marks, duration_ms: int, pending_in: float | None = None
    ) -> None:
        """Show the in/out marks of the loaded media over ``duration_ms``.

        MainWindow passes the same marks it shows in its own bar, so the two
        views of one media never disagree.
        """
        self.slider.set_marks(marks, duration_ms, pending_in)
        # The strip repaints itself from marks_state_changed, but whether
        # editing is allowed is its own state, so it follows the slider's.
        self.marks_strip.set_marks_editable(self.slider.marks_editable())

    def set_marks_editable(self, editable: bool) -> None:
        """Allow or block mark editing here and in the brace strip."""
        self.slider.set_marks_editable(editable)
        self.marks_strip.set_marks_editable(editable)

    # -- internals ------------------------------------------------------------
    def _on_slider_released(self) -> None:
        self.seek_released.emit(self.slider.value())
        self._sync_label()

    def _sync_label(self) -> None:
        self.time_label.setText(
            f"{self._fmt_ms(self.slider.value())} / {self._fmt_ms(self._duration_ms)}"
        )

    @staticmethod
    def _fmt_ms(ms: int) -> str:
        """Format milliseconds as ``mm:ss`` (or ``h:mm:ss`` past one hour)."""
        total_s = max(0, int(ms) // 1000)
        m, s = divmod(total_s, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _white_icon(self, sp: QStyle.StandardPixmap) -> QIcon:
        """The QStyle standard glyph recolored white, for contrast on ``:checked``.

        The glyph's alpha shape becomes a clip mask that is filled with pure
        white, so the result keeps the style icon's geometry with a white fill
        (reads clearly on the accent checked background, dark and light).
        """
        glyph = self.style().standardIcon(sp).pixmap(QSize(16, 16))
        mask = glyph.createMaskFromColor(
            Qt.GlobalColor.transparent, Qt.MaskMode.MaskInColor
        )
        out = QPixmap(glyph.size())
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        painter.setClipRegion(QRegion(mask))
        painter.fillRect(out.rect(), QColor("#ffffff"))
        painter.end()
        return QIcon(out)

    # -- events ----------------------------------------------------------------
    def closeEvent(self, event) -> None:
        """Emit ``closed`` (MainWindow stops playback) and accept the close.

        No WA_DeleteOnClose: the window is reused by hiding/showing it.
        """
        self.closed.emit()
        super().closeEvent(event)
