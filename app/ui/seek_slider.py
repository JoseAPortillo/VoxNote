"""Interactive seek slider with one draggable slice: a band and two handles.

One widget class serves both players: the audio bar of the main window holds it
as ``seek_slider`` and the video window as ``slider``, so painting the slice
here gives the feature to both at once.

The slice lives on the media timeline in SECONDS while both players seek in
MILLISECONDS. That conversion happens in exactly one place,
:meth:`SeekSlider._x_at_seconds`, which maps seconds onto the groove rect
:class:`SeekSlider.groove_rect` derives from the stylesheet metrics. Every
painted thing goes through it -- the band, both handle positions and the
hit-test radius around them -- so "the handle you grab is the handle you see"
is true by construction rather than by two copies of the same formula agreeing.

THE HANDLES ARE ALWAYS PAINTED; THE SLICE EXISTS ONLY IF ONE MOVED. With no
slice the start handle sits at 0 and the end handle at the duration, both
DIMMED, and the persisted state stays ``None``. Dragging either handle off its
extreme creates the slice; returning BOTH to the extremes deletes it. One
gesture, no flag and no menu, and the ``None`` = whole-file contract stays
intact: the engines read an empty range list as "the whole file", so ``[]``
could never be the stored form of "no slice" (see
:func:`app.core.models.normalize_slice`).

The single most important interaction detail here is the collision with the
seek. This widget seeks on ANY left press, so a press that lands on a handle
must NOT reach that path: :meth:`SeekSlider.mousePressEvent` claims the press,
:meth:`SeekSlider.mouseMoveEvent` drags the handle instead of seeking, and
:meth:`SeekSlider.mouseReleaseEvent` commits without seeking. None of the three
calls ``super()`` on that path, which is what keeps QSlider from starting its
own drag, from moving the value, and from emitting ``sliderPressed`` /
``sliderReleased`` -- the two signals the windows translate into an actual seek.

The widget owns no persistence. Every edit is applied in memory, normalized,
and announced through :attr:`SeekSlider.slice_edited`; the owner (the main
window, which holds the repository) writes it and mirrors the new state to the
other player.
"""

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPen,
    QPolygon,
)
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QSlider,
)

from app.core.models import normalize_slice
from app.ui.theme import SEEK_GROOVE_HEIGHT_PX

# Overrides for the properties ``apply_theme`` publishes on the application.
# They are the fallback for a slider built before any theme was applied.
#
# The property NAMES are still the mark-era ones (``markFillColor`` /
# ``markEdgeColor``): they are published by :func:`app.ui.theme.apply_theme`,
# which is outside this task's scope, and renaming them here would mean either
# a half-renamed theme or reading a property nobody publishes. What changed is
# what is painted with them, and that is the slice.
DEFAULT_SLICE_FILL = "#99f2a33c"
DEFAULT_SLICE_EDGE = "#ffc46b"

# Which end of the slice a handle owns. Strings, not an enum: they travel
# through a Qt signal payload and through hit-test return values, and the
# module already traffics in plain ``object`` payloads.
HANDLE_START = "start"
HANDLE_END = "end"

# HOW FAR FROM A HANDLE A PRESS STILL GRABS IT, in pixels.
#
# Chosen just INSIDE ``SEEK_HANDLE_SIZE_PX / 2`` (7 px), the half-width of the
# style's own playhead handle. The reasoning is that this handle must not be
# harder to grab than the playhead it shares the widget and the same cursor
# with -- if aiming at a 14 px target is normal here, a 13 px slice target is
# not a stretch. The other side of the same coin is that the seek has to
# survive everywhere the handles are not, and 6 px is 0.9 % of the 678 px width
# the groove measurements were taken at.
HANDLE_HIT_PX = 6

# Painted silhouette of a handle, in pixels. The bar is a solid grip in the
# edge colour, so it is opaque where the band underneath is translucent and the
# two can never be confused; the pointer sits on its INWARD side and says which
# of the two handles this is, which a bar alone cannot (the design glyphs are
# ``■>`` and ``<■``).
#
# The pointer points inward, so both extremes keep their pointer on-screen: the
# handle at x=0 has its pointer to the right of the bar, the handle at the last
# column has its pointer to the left.
HANDLE_BAR_WIDTH_PX = 7
HANDLE_POINTER_WIDTH_PX = 4

# Taller than SEEK_GROOVE_HEIGHT_PX so a handle is never read as the band it
# sits on, and centred in the widget exactly as the groove is: 9 px tall in the
# 15 px widget the measurements describe puts it on rows 3..11 against the
# groove's 5..9, leaving rows 0..2 and 12..14 to the playhead.
HANDLE_HEIGHT_PX = 9

# Alpha of a handle whose slice does not exist yet. A REAL dimming, not a
# comment: see ``app.ui.theme`` for how the two theme values were arrived at by
# measuring rendered pixels, and the paint tests for the check that the two
# states really differ. 112/255 keeps the untouched handles clearly weaker than
# the active ones while leaving them findable -- an affordance you cannot see
# is not an affordance.
HANDLE_DIM_ALPHA = 112

# Smallest slice a DRAG can produce, in seconds.
#
# A gesture invariant, not a data invariant: ``normalize_slice`` already rejects
# anything not strictly ordered, and a slice stored before this constant existed
# is displayed as stored rather than silently rewritten. What the constant buys
# is that no drag can hand the engines something below the smallest granularity
# in the chain below the UI:
#
#   * ``clipper.decode_range`` works at ``SAMPLE_RATE = 16000``, so 0.1 s is
#     1600 samples -- a range thinner than a sample decodes to nothing at all.
#   * whisper's encoder frame is 20 ms (a 30 s window over 1500 frames), so
#     anything under one frame has nothing to attend to and returns no segment.
#     That failure is the expensive kind: a run that SUCCEEDS with an empty
#     transcript.
#   * 0.1 s is 5 whisper frames, so it clears every granularity with room.
#
# Against the gesture it is invisible: 0.1 s is ~0.11 px of a 678 px bar over a
# 60 s media, and ~0.02 px of the same bar over a 10 minute one, so the two
# handles never stop feeling free to meet.
MIN_SLICE_SECONDS = 0.1


def theme_color(property_name: str, default: str) -> QColor:
    """Read a paint-time color published by :func:`app.ui.theme.apply_theme`.

    The slice band and the handles are custom painting, which QSS cannot style,
    so the theme hands its colors to the widgets as application properties.
    Reading them at paint time (instead of caching them) is what makes a theme
    switch repaint them with no extra wiring.
    """
    app = QApplication.instance()
    value = app.property(property_name) if app is not None else None
    color = QColor(value) if isinstance(value, str) and value else QColor(default)
    return color if color.isValid() else QColor(default)


def format_position(ms: int) -> str:
    """Format milliseconds as ``mm:ss`` (or ``h:mm:ss`` past one hour).

    Same shape the two player windows use for their time labels, so the status
    text announces a slice with the vocabulary already on screen.
    """
    total_s = max(0, int(ms)) // 1000
    m, s = divmod(total_s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class SeekSlider(QSlider):
    """Horizontal QSlider where a left-click jumps to the clicked position
    and a left-button drag follows the cursor continuously -- except on a
    slice handle, which drags the slice instead and never seeks.

    On top of that it paints the media's one ``[start, end]`` slice over the
    real groove, bounded by two draggable handles. Both handles are always
    painted; with no slice they sit dimmed on the extremes and the stored state
    is ``None``, so an untouched slider looks like no slice at all while still
    advertising the gesture.

    Default QSlider moves by page-step on click; this overrides that with
    ``QStyle.sliderValueFromPosition``. Standard signals (sliderPressed,
    sliderMoved, sliderReleased, valueChanged) are left untouched for a press
    that is a seek, and deliberately NOT emitted for one that grabs a handle.

    Signals:
        slice_edited(object, str): the user moved a handle and released it.
            Carries ``(slice, message)``: ``slice`` is already normalized
            (``None`` when there is none, never ``[]``) and ``message`` is the
            Spanish status text describing what happened. The owner persists
            ``slice`` and shows ``message``.
    """

    slice_edited = Signal(object, str)

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        # The LIVE handle positions, in seconds, always clamped to
        # ``[0, duration_s]`` and always ordered. They are the single source of
        # truth for the geometry; the stored slice is DERIVED from them (see
        # :meth:`slice`), so a drag and a reload paint through the same values
        # and cannot disagree.
        self._handle_seconds: list[float] = [0.0, 0.0]
        self._duration_ms = 0
        self._slice_editable = True
        # Which handle the current press grabbed, and the pair as it was when
        # that press landed. ``None`` is also "this press is a seek".
        self._drag_handle: str | None = None
        self._drag_origin: list[float] | None = None
        # No context menu: this widget used to offer mark in/out/clear here, and
        # the handles replaced all of it.
        self.setAccessibleName("Barra de posición del medio con handles de slice")
        self._refresh_tooltip()

    # -- slice state ---------------------------------------------------------
    def slice(self) -> list[float] | None:
        """The slice in seconds, or ``None`` when both handles are untouched.

        ``None`` is derived, not stored: it is what the handles being on the two
        extremes means. ``normalize_slice`` is the same gate the storage layer
        applies, so a pair this returns is always storable. A fresh list comes
        back, so a caller cannot reach in and move a handle by mutating it.
        """
        start, end = self._handle_seconds
        if start <= 0.0 and end >= self._duration_ms / 1000.0:
            return None
        return normalize_slice([start, end])

    def set_slice(self, value, duration_ms: int) -> None:
        """Show ``value`` (absolute seconds) over a ``duration_ms`` timeline.

        ``duration_ms`` is the media duration, the same value the owner sets as
        the slider range: seconds are placed as a fraction of it, so the two
        must agree. ``None`` means the whole file, which is what the untouched
        handles show.

        The pair is NORMALIZED here, on assignment, and that is load-bearing: a
        hand-edited ``items.json`` can carry an inverted pair, and painting it
        as given would put the handles in the wrong order. It is also clamped to
        the timeline it is being shown over, because a stored slice can outlast
        a media that got shorter. Neither rewrite the repository -- what is
        stored stays stored until the user touches a handle.

        A value spanning the whole file normalizes to no slice at all: both its
        handles land on the extremes, and that is what the untouched state IS.
        """
        self._duration_ms = max(0, int(duration_ms))
        total_s = self._duration_ms / 1000.0
        pair = normalize_slice(value)
        if pair is None:
            self._handle_seconds = [0.0, total_s]
        else:
            self._handle_seconds = self._clamp_to_timeline(pair, total_s)
        self._refresh_tooltip()
        self.update()

    @staticmethod
    def _clamp_to_timeline(pair, total_s: float) -> list[float]:
        """``pair`` pulled inside ``[0, total_s]`` without inverting it.

        The minimum-slice rule is deliberately NOT applied here: this is a stored
        value being displayed, and a slice somebody set by hand or by an older
        build has to come back exactly as stored. ``MIN_SLICE_SECONDS`` is an
        invariant of the GESTURE, and the drag path enforces it.
        """
        start = min(max(pair[0], 0.0), total_s)
        end = min(max(pair[1], 0.0), total_s)
        if end < start:
            end = start
        return [start, end]

    def set_slice_editable(self, editable: bool) -> None:
        """Allow or block handle dragging.

        The app clears this while an inference is running. The reason is no
        longer the old one (nothing consumes the slice any more, so there is no
        worker-thread write to race): a run in flight is reading the item, and a
        slice edited underneath it would make the status line describe a run
        scoped by something the user has since changed.
        """
        self._slice_editable = bool(editable)

    def slice_editable(self) -> bool:
        return self._slice_editable

    def _refresh_tooltip(self) -> None:
        """Restate the gesture, then the current slice, in the tooltip.

        The brace strip this widget used to carry did double duty as the slice's
        text readout; with it gone the tooltip is the only place the value is
        spelled out, so it is refreshed whenever the value changes rather than
        set once in the constructor.
        """
        gesture = (
            "Clic para buscar. Arrastra los handles ■> (inicio) y <■ (fin) "
            "para acotar el slice; llévalos a los extremos para borrarlo."
        )
        value = self.slice()
        if value is None:
            self.setToolTip(f"{gesture} Ahora: sin slice, archivo completo.")
        else:
            start, end = value
            self.setToolTip(
                f"{gesture} Ahora: de {format_position(round(start * 1000))} "
                f"a {format_position(round(end * 1000))}."
            )

    # -- geometry ------------------------------------------------------------
    def _x_at_seconds(self, seconds: float) -> int:
        """Left-edge pixel of ``seconds`` on the groove: the ONE x mapping.

        Clamped to the timeline and rounded the way the rest of this module
        rounds. The band, both handles and the hit-test radius around them all
        read from here, which is what makes "the handle you grab is the handle
        you see" true by construction rather than by two copies of the same
        formula agreeing.
        """
        groove = self.groove_rect()
        total_s = self._duration_ms / 1000.0
        if total_s <= 0:
            return groove.left()
        ratio = min(max(float(seconds), 0.0), total_s) / total_s
        return groove.left() + int(ratio * groove.width() + 0.5)

    def groove_rect(self) -> QRect:
        """The groove rect the style really paints, in widget coordinates.

        Computed from the stylesheet metrics rather than asked to the style,
        because on PySide6 6.11.2 the style cannot be asked: ``QStyle`` does
        not expose ``SC_SliderGroove``/``SC_SliderHandle`` at all (the name
        silently resolves to a scrollbar alias, so a lookup "succeeds" while
        returning the wrong enum), and ``subElementRect`` raises ``TypeError``
        for every slider sub-control. The one fallback that did run asked the
        style for ``PM_SliderThickness``, which degenerates to the widget
        height and therefore handed back the whole widget -- a band three times
        too tall, overhanging the groove on both sides.

        MEASURED on PySide6 6.11.2, Fusion, this project's stylesheet, a
        678x15 widget with range 0..1000: the groove paints on rows 5..9, i.e.
        ``SEEK_GROOVE_HEIGHT_PX`` tall and vertically centred, and it spans the
        full widget width with no horizontal inset for the handle. Those
        numbers are the ones the stylesheet itself is generated from (see
        ``SEEK_GROOVE_HEIGHT_PX`` in :mod:`app.ui.theme`), so this rect cannot
        drift away from the pixels the style actually paints.
        """
        return QRect(
            0,
            (self.height() - SEEK_GROOVE_HEIGHT_PX) // 2,
            self.width(),
            SEEK_GROOVE_HEIGHT_PX,
        )

    def handle_x(self, which: str) -> int | None:
        """Column of ``which`` handle, or ``None`` without a timeline.

        ``None`` rather than a column when no media is loaded: there is no
        position to convert seconds against, and a handle painted at column 0 of
        an unranged slider would be an affordance for nothing.

        PINNED INTO THE GROOVE. :meth:`_x_at_seconds` returns a LEFT EDGE, which
        is what a band needs and what a point cannot use: at the end of the
        timeline it answers ``groove.left() + width``, one column PAST the last
        one, so an unpinned end handle would be painted half off the widget and
        would sit at a column no cursor can name. Pinned to the last column, the
        end handle is where the cursor can already reach it.
        """
        if self._duration_ms <= 0:
            return None
        index = 0 if which == HANDLE_START else 1
        groove = self.groove_rect()
        column = self._x_at_seconds(self._handle_seconds[index])
        return min(max(column, groove.left()), max(groove.left(), groove.right()))

    def pixel_seconds(self) -> float:
        """Seconds one groove column is worth; ``0.0`` without a timeline.

        The resolution of every gesture on this widget, and what makes an
        extreme reachable at all (see :meth:`_snap_to_boundary`).
        """
        groove = self.groove_rect()
        if self._duration_ms <= 0 or groove.width() <= 0:
            return 0.0
        return self._duration_ms / 1000.0 / groove.width()

    @staticmethod
    def _snap_to_boundary(seconds: float, boundary: float, pixel_s: float) -> float:
        """``seconds`` pulled onto ``boundary`` when the grid cannot express it.

        Without this the end of the timeline is UNREACHABLE by a drag, and with
        it the whole "return both handles to the extremes to delete the slice"
        gesture does not work: the last column a cursor can name maps to
        ``total * (width - 1) / width``, so a slice could be dragged to 59.91 s
        of a 60 s media and never deleted. The gap this closes is exactly one
        column, which the pixel grid could not express anyway.

        It is deliberately NOT a wider magnet. A cursor column further in is a
        real position with a real time, and rounding it away would throw away
        resolution the grid does have.
        """
        if pixel_s <= 0:
            return seconds
        return boundary if abs(seconds - boundary) < pixel_s else seconds

    def hit_handle(self, x: int) -> str | None:
        """Which handle a press at widget column ``x`` grabs, or ``None``.

        Pure geometry, deliberately NOT gated on ``slice_editable`` or on a
        duration: the caller decides whether a press may edit, this only says
        what is under the cursor. A handle without a timeline reports ``None``
        for the same reason :meth:`handle_x` does.

        The NEAREST handle within ``HANDLE_HIT_PX`` wins outright, with no
        fallback to the farther one: when the two are that close, resolving by
        distance is what makes one press mean one thing. An exact tie goes to the
        START handle, purely so the same gesture always resolves the same way.

        The handle wins over the seek on a shared column. That is the precedence
        the interaction needs: the slice handle is the narrow, specific target,
        while the playhead is reachable over the whole line anyway.
        """
        start_x = self.handle_x(HANDLE_START)
        end_x = self.handle_x(HANDLE_END)
        if start_x is None or end_x is None:
            return None
        to_start = abs(x - start_x)
        to_end = abs(x - end_x)
        if to_start <= to_end:
            return HANDLE_START if to_start <= HANDLE_HIT_PX else None
        return HANDLE_END if to_end <= HANDLE_HIT_PX else None

    def handle_rect(self, which: str) -> QRect | None:
        """The painted BAR of ``which`` handle, centred on its own column.

        The pointer that completes the ``■>`` / ``<■`` glyph is not part of the
        rect; it is drawn next to it, inward, by :meth:`_paint_handle_pointer`.
        Centring is ``width // 2`` on both sides, so for the odd bar width the
        silhouette sits half a pixel toward the timeline start -- invisible at
        this size and worth less than a rounding special case.
        """
        x = self.handle_x(which)
        if x is None:
            return None
        return QRect(
            x - HANDLE_BAR_WIDTH_PX // 2,
            (self.height() - HANDLE_HEIGHT_PX) // 2,
            HANDLE_BAR_WIDTH_PX,
            HANDLE_HEIGHT_PX,
        )

    def slice_band_rect(self) -> QRect | None:
        """The slice as a rect over the real groove, or ``None``.

        The only place where a SLICE becomes a rect: a slice is a fraction of the
        media duration, and that fraction is mapped onto the groove rect, so the
        band always sits on the groove whatever the style or the theme. Seconds
        while the duration is kept in milliseconds (the unit both players seek
        in), hence the conversion in :meth:`_x_at_seconds`.

        ``None`` with no slice: painting a band that spans the whole groove for
        the untouched state would claim "everything is marked", which is the one
        wrong reading, and the whole-file run is exactly what an untouched
        slider must say.

        A band narrower than two columns is drawn as nothing (``paintEvent``
        drops it): the handles are the parts that stay visible at that width, so
        there is no need to widen the band and fake a range that is not there.
        """
        if self._duration_ms <= 0 or self.slice() is None:
            return None
        groove = self.groove_rect()
        if groove.width() <= 0 or groove.height() <= 0:
            return None
        start_x = self._x_at_seconds(self._handle_seconds[0])
        end_x = self._x_at_seconds(self._handle_seconds[1])
        return QRect(start_x, groove.top(), end_x - start_x, groove.height())

    def _position_seconds_at(self, x: int) -> float:
        """Seconds under widget ``x``, read through the painting groove.

        The drag uses the same mapping that drew the handles, so the handle
        follows the cursor exactly rather than through a second conversion. The
        groove spans the whole duration, hence the ms -> s step that keeps the
        answer comparable with the stored slice.
        """
        total = self._duration_ms
        groove = self.groove_rect()
        if total <= 0 or groove.width() <= 0:
            return 0.0
        ratio = (x - groove.left()) / groove.width()
        return min(max(ratio, 0.0), 1.0) * total / 1000.0

    @staticmethod
    def _clamp_handle(dragged: str, seconds: float, other: float, total_s: float) -> float:
        """Where the ``dragged`` handle may go, given where the other one is.

        Pure, and the single implementation the drag path uses: three rules, in
        this order.

        1. Inside the timeline. A handle past either end has no position to mean.
        2. Never crossing. The two rules are deliberately ASYMMETRIC, because
           the two handles answer to different rules: the start handle YIELDS
           (it is the one the user pushed), the end handle PUSHES (it stays
           where the user left it).
        3. Never zero-length: ``MIN_SLICE_SECONDS`` apart at all times.

        On a media shorter than the minimum the third rule is unsatisfiable and
        is dropped rather than raised on: the second rule is the one that must
        not break, so the pair stays ordered at the cost of the length promise.
        """
        value = min(max(float(seconds), 0.0), total_s)
        if dragged == HANDLE_START:
            return min(value, max(0.0, other - MIN_SLICE_SECONDS))
        return max(min(value, total_s), min(other + MIN_SLICE_SECONDS, total_s))

    # -- events --------------------------------------------------------------
    def _value_at(self, x: int) -> int:
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), x, self.width()
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Seek, unless the press landed on a grabbable handle.

        On a handle this RETURNS without calling ``super()``, and that omission
        is the whole mechanism: QSlider would otherwise record its own press
        offset, start its drag and emit ``sliderPressed``, which the two windows
        translate into a real seek. The handle drag owns the press from here to
        the matching release.
        """
        if event.button() == Qt.MouseButton.LeftButton:
            x = event.position().toPoint().x()
            if self._slice_editable:
                grabbed = self.hit_handle(x)
                if grabbed is not None:
                    self._drag_handle = grabbed
                    self._drag_origin = list(self._handle_seconds)
                    return
            self.setValue(self._value_at(x))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Follow the cursor as a seek, or drag the grabbed handle.

        The handle branch keys on this widget's own drag state rather than on
        ``event.buttons()``: the press that set it is the authority, and that
        keeps a drag working from a synthesised move as well as from a real one.
        """
        if self._drag_handle is not None:
            dragged = self._drag_handle
            index = 0 if dragged == HANDLE_START else 1
            total_s = self._duration_ms / 1000.0
            moved = self._clamp_handle(
                dragged,
                self._position_seconds_at(event.position().toPoint().x()),
                self._handle_seconds[1 - index],
                total_s,
            )
            # Snapped last, after the clamp, so the two extremes are exactly
            # reachable and the "return both handles home" gesture exists at
            # all. See _snap_to_boundary for why it is one column and not more.
            pixel_s = self.pixel_seconds()
            moved = self._snap_to_boundary(moved, 0.0, pixel_s)
            moved = self._snap_to_boundary(moved, total_s, pixel_s)
            self._handle_seconds[index] = moved
            self._refresh_tooltip()
            self.update()
            return
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.setValue(self._value_at(event.position().toPoint().x()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Commit a handle drag, or hand the release back to the seek path.

        Skipping ``super()`` for a handle release is what keeps the matching
        ``sliderReleased`` -- and therefore the seek to the slider's value --
        from firing after a gesture that only meant to move a handle.
        """
        if (
            self._drag_handle is not None
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self._commit_handle_drag()
            return
        super().mouseReleaseEvent(event)

    def _commit_handle_drag(self) -> None:
        """Announce the slice the drag ended on, or stay silent if it is the same.

        A press that changed nothing announces nothing: a stray click on a handle
        must not rewrite ``items.json`` nor replace the status line with a
        confirmation of something the user did not do. A drag that lands back on
        both extremes announces ``None``, which is how returning the handles home
        deletes the slice.
        """
        origin = self._drag_origin or []
        self._drag_handle = None
        self._drag_origin = None
        if list(self._handle_seconds) == origin:
            return
        value = self.slice()
        if value is None:
            self.slice_edited.emit(None, "Slice borrado: archivo completo.")
            return
        start, end = value
        self.slice_edited.emit(
            value,
            f"Slice de {format_position(round(start * 1000))} "
            f"a {format_position(round(end * 1000))}.",
        )

    # -- painting ------------------------------------------------------------
    def paintEvent(self, event) -> None:
        """Paint the style, then the band over the groove, then the handles.

        The handles go LAST and therefore ON TOP, which is the opposite of what
        this widget used to do with its bands and for a measured reason worth
        keeping in mind: the style's playhead handle occupies the same columns as
        a slice handle whenever the two coincide, and a handle drawn under it
        would be invisible exactly where the user is reaching for it. They are
        small (HANDLE_HEIGHT_PX tall against the playhead's SEEK_HANDLE_SIZE_PX)
        so the playhead still reads around them.

        The band keeps the measured discipline of the marks feature it replaced:
        the 1 px pen is centred on the outline, so the rect is shortened by one
        column on the right for the pen to eat the band's last column rather
        than the first column of whatever is next.
        """
        super().paintEvent(event)
        if self._duration_ms <= 0:
            return
        band = self.slice_band_rect()
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if band is not None and band.width() >= 2:
                radius = min(2.0, max(1.0, band.height() / 4.0))
                painter.setBrush(theme_color("markFillColor", DEFAULT_SLICE_FILL))
                painter.setPen(
                    QPen(theme_color("markEdgeColor", DEFAULT_SLICE_EDGE), 1.0)
                )
                painter.drawRoundedRect(band.adjusted(0, 0, -1, -1), radius, radius)
            self._paint_handles(painter)
        finally:
            painter.end()

    def _paint_handles(self, painter: QPainter) -> None:
        """Both handles, in the edge colour, DIMMED when there is no slice.

        The dimming is one alpha on one colour, so the two states cannot drift
        apart in hue: a dimmed handle is the same handle, quieter. It keys on
        whether a slice EXISTS, not on where each handle sits, which is what
        makes the handle at the extreme still read as active once the other one
        has moved.
        """
        color = theme_color("markEdgeColor", DEFAULT_SLICE_EDGE)
        if self.slice() is None:
            color = QColor(color)
            color.setAlpha(HANDLE_DIM_ALPHA)
        painter.setPen(QPen(Qt.PenStyle.NoPen))
        painter.setBrush(color)
        for which in (HANDLE_START, HANDLE_END):
            rect = self.handle_rect(which)
            if rect is None:
                continue
            painter.drawRoundedRect(rect, 2.0, 2.0)
            self._paint_handle_pointer(painter, which, rect, color)

    @staticmethod
    def _paint_handle_pointer(
        painter: QPainter, which: str, rect: QRect, color: QColor
    ) -> None:
        """The inward triangle that turns a bar into ``■>`` or ``<■``.

        It points toward the other handle, which is the only thing that
        distinguishes the two silhouettes without reading a label: the start of a
        slice opens to the right and its end opens to the left. Because it always
        points inward it stays on-screen at both extremes, where a handle at x=0
        has lost half its bar to the widget edge.
        """
        if which == HANDLE_START:
            base_x = rect.right() + 1
            tip_x = base_x + HANDLE_POINTER_WIDTH_PX
        else:
            base_x = rect.left()
            tip_x = base_x - HANDLE_POINTER_WIDTH_PX
        polygon = QPolygon(
            [
                QPoint(base_x, rect.top()),
                QPoint(tip_x, rect.center().y()),
                QPoint(base_x, rect.bottom() + 1),
            ]
        )
        painter.setBrush(color)
        painter.drawPolygon(polygon)