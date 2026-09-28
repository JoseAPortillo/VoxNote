"""Interactive seek slider with in/out mark shading, and the mark strip below it.

One widget class serves both players: the audio bar of the main window holds it
as ``seek_slider`` and the video window as ``slider``, so painting the marks
here gives the feature to both at once.

Marks live on the media timeline in SECONDS while both players seek in
MILLISECONDS. That conversion happens in exactly one place,
:meth:`SeekSlider._x_at_seconds`, which maps seconds onto the groove rect
:class:`SeekSlider.groove_rect` derives from the stylesheet metrics. That rect
is not guessed: it is built from the same constants the stylesheet itself is
generated from, and it was confirmed against the pixels PySide6 6.11.2
actually paints. :class:`MarksStrip` then reads those very same rects, so the
shading on the groove and the ``{``/``}`` braces underneath cannot
desynchronize: there is a single geometry behind both, and a start mark that is
still waiting for its end is drawn on it too
(:meth:`SeekSlider.pending_pixel_rect`), so the gesture is visible from the
first click and lands exactly where the committed band will.

The widget owns no persistence. Every edit is applied in memory, normalized,
and announced through :attr:`SeekSlider.marks_edited`; the owner (the main
window, which holds the repository) writes it and mirrors the new state to the
other player.
"""

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QSizePolicy,
    QSlider,
    QStyle,
    QWidget,
)

from app.core.models import normalize_marks
from app.ui.theme import SEEK_GROOVE_HEIGHT_PX

# Overrides for the properties ``apply_theme`` publishes on the application.
# They are the fallback for a slider built before any theme was applied.
DEFAULT_MARK_FILL = "#99f2a33c"
DEFAULT_MARK_EDGE = "#ffc46b"

# A very short range can round to zero groove pixels; keep it visible.
MIN_MARK_WIDTH_PX = 3

# The pending start (an ``in`` with no ``out`` yet) is a tick of this width.
# Same width as MIN_MARK_WIDTH_PX on purpose: it is the width a just-closed
# range would have, so committing it does not resize anything.
PENDING_MARK_WIDTH_PX = 3

# Painted as a DASHED outline over a partly transparent fill, while a committed
# range is a solid outline over the shaded band. Two signals, because at 3 px
# either one alone is weak: an unfilled dashed outline is too faint to see on
# its own, and a solid tick is easy to mistake for a very short range.
PENDING_MARK_DASH = [2.0, 2.0]
PENDING_MARK_FILL_ALPHA = 140

# Height of the brace row under the groove.
STRIP_HEIGHT = 20


def theme_color(property_name: str, default: str) -> QColor:
    """Read a paint-time color published by :func:`app.ui.theme.apply_theme`.

    The mark shading is custom painting, which QSS cannot style, so the theme
    hands its colors to the widgets as application properties. Reading them at
    paint time (instead of caching them) is what makes a theme switch repaint
    the shading and the braces with no extra wiring.
    """
    app = QApplication.instance()
    value = app.property(property_name) if app is not None else None
    color = QColor(value) if isinstance(value, str) and value else QColor(default)
    return color if color.isValid() else QColor(default)


def format_position(ms: int) -> str:
    """Format milliseconds as ``mm:ss`` (or ``h:mm:ss`` past one hour).

    Same shape the two player windows use for their time labels, so the
    context menu speaks about a position with the vocabulary already on screen.
    """
    total_s = max(0, int(ms)) // 1000
    m, s = divmod(total_s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class SeekSlider(QSlider):
    """Horizontal QSlider where a left-click jumps to the clicked position
    and a left-button drag follows the cursor continuously.

    On top of that it paints the in/out marks of the selected media: every
    marked range is shaded over the real groove and mirrored as a ``{``/``}``
    pair by the :class:`MarksStrip` placed underneath. A start mark with no end
    yet is painted too, as a dashed tick and a lone ``{``, so the gesture is
    visible from the first click. A right-click opens a menu to mark the start,
    mark the end, remove the range under the cursor -- named with its own start
    and end when the click landed on one -- or clear every mark; the first two
    act on the current playback position, which the slider mirrors in its own
    value, and both show the position they will use so the behavior needs no
    documentation.

    Default QSlider moves by page-step on click; this overrides that with
    ``QStyle.sliderValueFromPosition``. Standard signals (sliderPressed,
    sliderMoved, sliderReleased, valueChanged) are left untouched.

    Signals:
        marks_edited(object, object, str): the user changed the marks through
            the context menu. Carries ``(marks, pending_in, message)``:
            ``marks`` is already normalized (``None`` when there are none, never
            ``[]``), ``pending_in`` is the start mark still waiting for its end
            (``None`` when there is none) and ``message`` is the Spanish status
            text describing what happened, including why an edit was ignored.
            The owner persists ``marks`` and shows ``message``.
        marks_state_changed(): the marks or the editable state changed, without
            a user edit. Attached strips refresh their text from it.
    """

    marks_edited = Signal(object, object, str)
    marks_state_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._marks: list[list[float]] | None = None
        self._duration_ms = 0
        self._pending_in: float | None = None
        self._marks_editable = True
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.setAccessibleName("Barra de posición del medio")
        self.setToolTip(
            "Clic para buscar. Clic derecho: marcar inicio, marcar fin, "
            "quitar el tramo bajo el cursor (indica sus extremos) o limpiar "
            "todas las marcas."
        )

    def _value_at(self, x: int) -> int:
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), x, self.width()
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setValue(self._value_at(event.position().toPoint().x()))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.setValue(self._value_at(event.position().toPoint().x()))
        super().mouseMoveEvent(event)

    # -- marks state ---------------------------------------------------------
    def marks(self) -> list[list[float]] | None:
        """The marked ranges in seconds, ordered and without overlaps."""
        return self._marks

    def pending_in(self) -> float | None:
        """The start mark in seconds still waiting for its end, if any."""
        return self._pending_in

    def set_marks(
        self,
        marks,
        duration_ms: int,
        pending_in: float | None = None,
    ) -> None:
        """Show ``marks`` (absolute seconds) over a ``duration_ms`` timeline.

        ``duration_ms`` is the media duration, the same value the owner sets as
        the slider range: seconds are placed as a fraction of it, so the two
        must agree. ``None`` and an empty list both mean "no marks", because
        the stored state for no marks is ``None`` (``normalize_marks``) and
        painting an empty list as "everything is marked" would be the one
        wrong reading.

        ``pending_in`` is display state, not stored state: a start mark with no
        end yet cannot become a range on its own, so it is pushed in by the
        owner that owns it.
        """
        self._marks = normalize_marks(marks)
        self._duration_ms = max(0, int(duration_ms))
        self._pending_in = None if pending_in is None else max(0.0, float(pending_in))
        self.marks_state_changed.emit()
        self.update()

    def set_marks_editable(self, editable: bool) -> None:
        """Allow or block mark editing through the context menu.

        The app clears this while an inference is running: the pipeline drops
        the marks from the worker thread when it finishes and the repository
        has no lock, so a mark written by the UI at that moment would be
        overwritten by that cleanup.
        """
        editable = bool(editable)
        if editable == self._marks_editable:
            return
        self._marks_editable = editable
        self.marks_state_changed.emit()

    def marks_editable(self) -> bool:
        return self._marks_editable

    # -- geometry ------------------------------------------------------------
    def _x_at_seconds(self, seconds: float) -> int:
        """Left-edge pixel of ``seconds`` on the groove: the ONE x mapping.

        Clamped to the timeline and rounded the way the rest of this module
        rounds. Both the committed bands and the pending start tick go through
        it, which is what makes "the tick is where the band will start" true by
        construction rather than by two copies of the same formula agreeing.
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
        height and therefore handed back the whole widget -- bands three times
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

    def mark_pixel_rects(self) -> list[QRect]:
        """One rect per marked range, laid over the real groove.

        The only place where a RANGE becomes a rect: a mark is a fraction of the
        media duration, and that fraction is mapped onto the groove rect, so the
        shading always sits on the groove and under the handle whatever the style
        or the theme. Marks are stored in seconds while the duration is kept in
        milliseconds (the unit both players seek in), so the conversion happens
        exactly here, in :meth:`_x_at_seconds`. Disjoint ranges produce disjoint
        rects.

        A range that cannot be drawn is DROPPED, not clamped: one that ends at
        or past the end of the media, or a zero-width timeline. Callers that map
        a click back to a stored range must therefore check that the rects and
        the marks are the same length before pairing them up by index
        (see :meth:`_hit_index`).
        """
        marks = self._marks
        if not marks or self._duration_ms <= 0:
            return []
        groove = self.groove_rect()
        if groove.width() <= 0 or groove.height() <= 0:
            return []
        total_s = self._duration_ms / 1000.0
        left = groove.left()
        span = groove.width()
        rects: list[QRect] = []
        for start, end in marks:
            start_s = min(max(float(start), 0.0), total_s)
            end_s = min(max(float(end), 0.0), total_s)
            if end_s <= start_s:
                continue
            x0 = self._x_at_seconds(start_s)
            x1 = self._x_at_seconds(end_s)
            if x1 - x0 < MIN_MARK_WIDTH_PX:
                x1 = x0 + MIN_MARK_WIDTH_PX
            if x1 > left + span:
                x0 = max(left, x1 - span)
                x1 = left + span
            rects.append(QRect(x0, groove.top(), x1 - x0, groove.height()))
        return rects

    def pending_pixel_rect(self) -> QRect | None:
        """The pending start as a tick over the groove, or ``None``.

        Left-aligned on the column where the band will start, computed by the
        same mapping the bands use, so marking the end only THICKENS the tick
        instead of moving it. The one clamp is the last ``PENDING_MARK_WIDTH_PX``
        pixels of the timeline, where a tick that kept its left edge would hang
        off the groove.

        ``None`` without a pending start or without a duration: with no timeline
        there is no position to convert seconds against.
        """
        if self._pending_in is None or self._duration_ms <= 0:
            return None
        groove = self.groove_rect()
        if groove.width() <= 0 or groove.height() <= 0:
            return None
        width = min(PENDING_MARK_WIDTH_PX, groove.width())
        left = min(
            self._x_at_seconds(self._pending_in), groove.left() + groove.width() - width
        )
        return QRect(left, groove.top(), width, groove.height())

    def _position_seconds_at(self, x: int) -> float:
        """Seconds under widget ``x``, read through the painting groove.

        The removal action uses the same mapping that drew the shading, so
        "the range under the cursor" is literally the range the user sees. The
        groove spans the whole duration, hence the ms -> s conversion that
        keeps the answer comparable with the stored marks.
        """
        total = self._duration_ms
        groove = self.groove_rect()
        if total <= 0 or groove.width() <= 0:
            return 0.0
        ratio = (x - groove.left()) / groove.width()
        return min(max(ratio, 0.0), 1.0) * total / 1000.0

    # -- painting ------------------------------------------------------------
    def paintEvent(self, event) -> None:
        """Paint the style, then shade the marked ranges over the groove.

        A pending start is painted too, as a dashed tick, and FIRST, so the
        committed bands always win where the two overlap: a band starting at
        the pending column covers it rather than the other way round, which is
        exactly what happens when the user marks the end.

        The handle is deliberately NOT repainted on top of the bands. The style
        cannot report where it is on PySide6 6.11.2 (see :meth:`groove_rect`),
        so there is no rect to clip such a redraw to, and deriving one here
        would mean a second value-to-pixel mapping sitting next to the one the
        widget already owns -- a second thing to be wrong, for a repaint that
        changes nothing a user can see.

        The bands do not need it, and that is measured, not assumed. The handle
        is SEEK_HANDLE_SIZE_PX square with a vertical margin of
        SEEK_HANDLE_MARGIN_Y_PX, so on a 15 px widget it owns rows 0..3 and
        10..14 outright; a mark covering the WHOLE timeline leaves every one of
        those pixels byte-identical, because the band is only
        SEEK_GROOVE_HEIGHT_PX tall. Only on the groove rows themselves, where
        the two genuinely overlap, does the handle take the band's colour, and
        even there it stays a distinct shape (measured 1.11..2.59 contrast
        between the marked and the unmarked handle, both themes). The handle
        never disappears, so the old repaint bought nothing.
        """
        super().paintEvent(event)
        pending = self.pending_pixel_rect()
        rects = self.mark_pixel_rects()
        if not rects and pending is None:
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if pending is not None:
                edge = theme_color("markEdgeColor", DEFAULT_MARK_EDGE)
                fill = QColor(edge)
                fill.setAlpha(PENDING_MARK_FILL_ALPHA)
                pen = QPen(edge, 1.0)
                pen.setStyle(Qt.PenStyle.DashLine)
                pen.setDashPattern(PENDING_MARK_DASH)
                painter.setBrush(fill)
                painter.setPen(pen)
                painter.drawRect(pending)
            if not rects:
                return
            painter.setBrush(theme_color("markFillColor", DEFAULT_MARK_FILL))
            painter.setPen(QPen(theme_color("markEdgeColor", DEFAULT_MARK_EDGE), 1.0))
            radius = min(2.0, max(1.0, rects[0].height() / 4.0))
            for rect in rects:
                # Shortened by one pixel on the right so the pen eats the last
                # column of the band instead of the first column of the next
                # one. The 1px pen is centred on the outline, so vertically it
                # straddles the boundary: measured, it puts half a pixel of
                # edge colour on the row ABOVE the groove and keeps the band
                # covering all SEEK_GROOVE_HEIGHT_PX groove rows.
                painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), radius, radius)
        finally:
            painter.end()

    # -- context menu --------------------------------------------------------
    def _hit_index(self, x: int) -> int | None:
        """Index of the marked range a right-click at widget ``x`` landed on.

        Two tests, in this order, because each covers what the other misses.

        1. The PAINTED rect, because that is what the user actually clicked. It
           is only trusted when the painted rects line up one-to-one with the
           stored marks: :meth:`mark_pixel_rects` DROPS a range it cannot draw
           (one that ends past the media, or no timeline at all), so pairing
           survivors by index could name -- and remove -- the wrong range.
        2. The seconds under the click, through :meth:`_position_seconds_at`,
           the same mapping the painting uses, so no second conversion exists.

        The painted rect is the one that matters most: a range shorter than
        ``MIN_MARK_WIDTH_PX`` is widened when painted, so a click on the extra
        columns maps to a position just outside the stored pair and the seconds
        test alone would refuse to remove a mark the user is visibly clicking.
        """
        marks = self._marks or []
        rects = self.mark_pixel_rects()
        if len(rects) == len(marks):
            for index, rect in enumerate(rects):
                if rect.left() <= x <= rect.right():
                    return index
        position_s = self._position_seconds_at(x)
        for index, (start, end) in enumerate(marks):
            if start <= position_s <= end:
                return index
        return None

    def _on_context_menu(self, pos) -> None:
        """Offer the mark actions, each labelled with what it will do.

        The removal names the range under the cursor when the click resolved to
        one, so the user removes a specific mark instead of trusting a generic
        label. When the click is not on any range the generic wording is kept
        (and stays enabled whenever there are marks at all), which is what the
        menu said before the click was resolved.
        """
        if not self._marks_editable or self._duration_ms <= 0:
            # Without a loaded media there is no timeline to place a mark on.
            return
        at = format_position(self.value())
        hit = self._hit_index(pos.x())
        menu = QMenu(self)
        action_in = menu.addAction(f"Marcar inicio en {at}")
        action_out = menu.addAction(f"Marcar fin en {at}")
        menu.addSeparator()
        if hit is None:
            action_remove = menu.addAction("Quitar el tramo bajo el cursor")
        else:
            start, end = self._marks[hit]
            action_remove = menu.addAction(
                f"Quitar el tramo {format_position(round(start * 1000))}"
                f"–{format_position(round(end * 1000))}"
            )
        action_clear = menu.addAction("Limpiar todas las marcas")
        has_marks = bool(self._marks)
        action_remove.setEnabled(has_marks)
        # A pending start is a mark the user can see and wants gone, and
        # _clear_marks() drops it, so the action is enabled for it too.
        action_clear.setEnabled(has_marks or self._pending_in is not None)
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen is action_in:
            self._mark_in(self.value() / 1000.0)
        elif chosen is action_out:
            self._mark_out(self.value() / 1000.0)
        elif chosen is action_remove:
            if hit is not None:
                self._remove_range(hit)
            else:
                self._remove_under_cursor(self._position_seconds_at(pos.x()))
        elif chosen is action_clear:
            self._clear_marks()

    def _publish(self, marks, message: str) -> None:
        """Normalize and store an edit, then announce it to the owner.

        Emitted for rejected edits too, so the reason reaches the status label
        through the same channel as the confirmation.
        """
        self._marks = normalize_marks(marks)
        self.marks_edited.emit(self._marks, self._pending_in, message)
        self.marks_state_changed.emit()

    def _mark_in(self, position_s: float) -> None:
        """Remember a start mark; it becomes a range once its end arrives."""
        self._pending_in = max(0.0, float(position_s))
        self._publish(
            self._marks, f"Inicio pendiente en {format_position(self.value())}."
        )

    def _mark_out(self, position_s: float) -> None:
        """Close the pending start mark into a range, or explain why not."""
        pending = self._pending_in
        if pending is None:
            self._publish(
                self._marks,
                "No hay ningún inicio pendiente: marca antes un inicio.",
            )
            return
        end = max(0.0, float(position_s))
        if end <= pending:
            self._publish(
                self._marks,
                f"El fin ({format_position(round(end * 1000))}) no es posterior "
                f"al inicio ({format_position(round(pending * 1000))}): se ignora.",
            )
            return
        self._pending_in = None
        start_s, end_s = pending, end
        self._publish(
            (self._marks or []) + [[start_s, end_s]],
            f"Tramo marcado de {format_position(round(start_s * 1000))} "
            f"a {format_position(round(end_s * 1000))}.",
        )

    def _remove_range(self, index: int) -> None:
        """Drop stored range ``index`` and say which one went.

        The single writer of a removal: the context menu and the seconds
        fallback both land here, so the range named in the status is the range
        that was actually removed. ``index`` comes from :meth:`_hit_index`, which
        never returns an index it cannot have drawn.
        """
        marks = self._marks
        if not marks or not 0 <= index < len(marks):
            self._publish(marks, "No hay ningún tramo de marcado que quitar.")
            return
        start, end = marks[index]
        kept = marks[:index] + marks[index + 1:]
        self._publish(
            kept,
            f"Tramo quitado: {format_position(round(start * 1000))}"
            f"–{format_position(round(end * 1000))}.",
        )

    def _remove_under_cursor(self, position_s: float) -> None:
        """Drop the range containing ``position_s``, or say that there is none.

        The seconds-based counterpart of :meth:`_hit_index`, kept for the click
        that resolved to no rect: it still goes through the same containment
        test the old removal used, so a click just outside a band changes
        nothing and explains itself.
        """
        marks = self._marks
        if not marks:
            self._publish(marks, "No hay marcas que quitar.")
            return
        kept = [pair for pair in marks if not (pair[0] <= position_s <= pair[1])]
        if len(kept) == len(marks):
            self._publish(
                marks, "No hay ningún tramo de marcado bajo el cursor."
            )
            return
        self._publish(
            kept, f"Tramo quitado en {format_position(round(position_s * 1000))}."
        )

    def _clear_marks(self) -> None:
        if not self._marks and self._pending_in is None:
            self._publish(self._marks, "No hay marcas que limpiar.")
            return
        self._pending_in = None
        self._publish(None, "Marcas limpiadas.")


class MarksStrip(QWidget):
    """Read-only row of ``{``/``}`` braces under a :class:`SeekSlider`.

    It computes no geometry of its own: every brace is placed at the edges of a
    rect returned by :meth:`SeekSlider.mark_pixel_rects` (or at the left edge of
    :meth:`SeekSlider.pending_pixel_rect` for a start mark that has no end yet),
    the same rects the slider fills with the shading, so the two views cannot
    drift apart. Being a real widget in the layout is what makes it follow the
    window on resize, and it carries a Spanish accessible name and tooltip.
    """

    def __init__(self, slider: SeekSlider, parent=None) -> None:
        super().__init__(parent)
        self._slider = slider
        self._editable = True
        self.setFixedHeight(STRIP_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Tira de marcas de inicio y fin")
        self._slider.marks_state_changed.connect(self._refresh_state)
        self._refresh_state()

    # -- state ---------------------------------------------------------------
    def _refresh_state(self) -> None:
        """Restate the marks in the tooltip, which is where they are read."""
        pending = self._slider.pending_in()
        if pending is None:
            head = ""
        else:
            head = f"Inicio pendiente en {format_position(round(pending * 1000))}. "
        marks = self._slider.marks()
        if marks:
            spans = ", ".join(
                f"{format_position(round(start * 1000))}"
                f"–{format_position(round(end * 1000))}"
                for start, end in marks
            )
            body = f"Marcas: {spans}."
        else:
            body = "Sin marcas."
        self.setToolTip(
            f"{head}{body} Se edita con el botón derecho sobre la barra de posición."
        )
        self.update()

    def set_marks_editable(self, editable: bool) -> None:
        """Dim the braces while editing is blocked (a run is in flight)."""
        editable = bool(editable)
        if editable == self._editable:
            return
        self._editable = editable
        self.update()

    # -- painting ------------------------------------------------------------
    def _x_offset(self) -> int:
        """Horizontal distance from this strip to the slider's left edge.

        Both widgets live in the same layout column, so this is normally zero;
        it is measured rather than assumed so the braces cannot drift away from
        the shading they mirror. ``mapToGlobal`` is used instead of ``mapTo``
        because older PySide6 releases crash when mapping one widget onto a
        sibling of its own parent.
        """
        return (
            self._slider.mapToGlobal(QPoint(0, 0)).x()
            - self.mapToGlobal(QPoint(0, 0)).x()
        )

    def paintEvent(self, event) -> None:
        """Draw the pending ``{`` first, then a brace per committed range.

        The pending start is the very brace its committed range will keep, on
        the very rect edge the band will start at, in the muted colour and with
        no ``}`` beside it: so the glyph the user sees from the first click does
        not move when the end arrives, and the missing ``}`` is what says the
        range is not closed yet. Committed braces are painted after it, so they
        win where the two overlap.
        """
        rects = self._slider.mark_pixel_rects()
        pending = self._slider.pending_pixel_rect()
        if not rects and pending is None:
            return
        offset = self._x_offset()
        font = QFont(self.font())
        font.setBold(True)
        painter = QPainter(self)
        try:
            painter.setFont(font)
            metrics = QFontMetrics(font)
            baseline = (self.height() + metrics.ascent() - metrics.descent()) // 2
            glyph_w = metrics.horizontalAdvance("W")
            if pending is not None:
                painter.setPen(
                    QPen(QColor(self.palette().color(QPalette.ColorRole.PlaceholderText)))
                )
                self._draw_brace(painter, "{", pending.left() + offset, baseline, glyph_w)
            if not rects:
                return
            if self._editable:
                color = theme_color("markEdgeColor", DEFAULT_MARK_EDGE)
            else:
                color = QColor(self.palette().color(QPalette.ColorRole.PlaceholderText))
            painter.setPen(QPen(color))
            for rect in rects:
                left = rect.left() + offset
                right = rect.right() + offset
                self._draw_brace(painter, "{", left, baseline, glyph_w)
                # A range narrower than the two glyphs would print them on top
                # of each other; the start brace alone still says "marked".
                if right - left >= 2 * glyph_w:
                    self._draw_brace(painter, "}", right, baseline, glyph_w)
        finally:
            painter.end()

    def _draw_brace(
        self, painter: QPainter, text: str, center_x: int, baseline: int, glyph_w: int
    ) -> None:
        x = int(center_x - glyph_w / 2)
        if x < 0 or x + glyph_w > self.width():
            return  # outside the strip: nothing to anchor the brace to
        painter.drawText(QPoint(x, baseline), text)
