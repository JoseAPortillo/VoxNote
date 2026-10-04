"""Tests for the slice handles on :class:`~app.ui.seek_slider.SeekSlider`.

Run with::

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

Qt runs offscreen (``QT_QPA_PLATFORM`` is set below, BEFORE PySide6 is
imported, because the platform plugin is chosen at import time), so every case
here is part of the same stdlib run as ``tests/asr/`` and ``tests/core/``. No
pytest fixture is used, for the reason recorded in the feature document: the
project interpreter has PySide6 but not pytest.

WHAT IS WORTH A TEST HERE, and why it is split the way it is. Three classes of
behaviour, three different kinds of evidence:

1. GEOMETRY, as pure functions of pixels and seconds: hit-testing, clamping,
   band placement. These are the invariants that decide whether a slice can be
   inverted, lost or stolen by a click, and they are cheap to pin exactly.
2. STATE, including the rule that untouched handles mean ``None`` and that
   returning both handles to the extremes deletes the slice.
3. REAL INTERACTION, synthesised through ``QTest``. The one that matters is the
   collision: this widget already seeks on ANY left press, so a press that lands
   on a handle has to stop doing that. Asserting the slice changed is easy and
   would pass even if the press had ALSO seeked, so the value is asserted
   unchanged alongside it.

MEASURED, not assumed, about ``QTest`` on PySide6 6.11.2 offscreen: after
``QTest.mousePress`` the following ``QTest.mouseMove`` arrives with
``event.buttons() == LeftButton``, and ``mouseRelease`` carries the release
position. A PLAIN ``QSlider`` does not follow that synthesised move at all (its
value moves by a page-step on the press and then never tracks the cursor), so
the seek assertions below can only hold because ``SeekSlider`` overrides
``mouseMoveEvent`` and seeks from the event position itself. The handle-drag
branch keys on the widget's own drag state rather than on ``event.buttons()``,
so it is independent of that detail either way.

A NOTE ON EXPECTATIONS, from the defect T8 shipped: ``set_slice`` normalises on
assignment, so the value handed to it is not necessarily the value read back.
Every expectation here is written against the INVARIANT (valid, ordered, not
zero-length, ``None`` only when both handles sit on the extremes) and never
against the input order.
"""

from __future__ import annotations

import os

# Before any PySide6 import: the QPA plugin is chosen when QtGui loads.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.ui.seek_slider import (
    DEFAULT_SLICE_EDGE,
    HANDLE_BAR_WIDTH_PX,
    HANDLE_END,
    HANDLE_HIT_PX,
    HANDLE_HEIGHT_PX,
    HANDLE_START,
    MIN_SLICE_SECONDS,
    SeekSlider,
)
from app.ui.theme import SEEK_GROOVE_HEIGHT_PX, apply_theme

# One shared application, guarded so importing this module twice (discovery
# plus a direct run) cannot construct a second one.
_APP = QApplication.instance() or QApplication([])

# The geometry the groove rect was measured at (see SeekSlider.groove_rect), so
# the widget under test is the size the measurements describe.
WIDGET_WIDTH = 678
WIDGET_HEIGHT = 15

# 60 s, so every pixel is worth 88.5 ms and a handle is 22.6 px per second.
DURATION_MS = 60_000
DURATION_S = 60.0


def setUpModule() -> None:
    # The paint assertions compare rendered pixels, which only have a fixed
    # meaning under a known palette: the real stylesheet and the real
    # application properties, not Fusion's defaults.
    apply_theme(_APP, dark=True)


def make_slider(duration_ms: int = DURATION_MS) -> SeekSlider:
    """A slider sized and ranged like the one the audio bar shows."""
    slider = SeekSlider()
    slider.resize(WIDGET_WIDTH, WIDGET_HEIGHT)
    slider.setRange(0, duration_ms)
    return slider


def groove_row(slider: SeekSlider) -> int:
    """The vertical centre of the groove, which is also the handle centre."""
    return (slider.height() - SEEK_GROOVE_HEIGHT_PX) // 2 + SEEK_GROOVE_HEIGHT_PX // 2


def channel(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def relative_luminance(color) -> float:
    """WCAG relative luminance of a ``QColor``."""
    return (
        0.2126 * channel(color.redF())
        + 0.7152 * channel(color.greenF())
        + 0.0722 * channel(color.blueF())
    )


def contrast(first, second) -> float:
    """WCAG contrast ratio between two ``QColor``s (1.0 .. 21.0)."""
    a, b = relative_luminance(first), relative_luminance(second)
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)


def render(slider: SeekSlider):
    """Render the widget offscreen and return the resulting ``QImage``."""
    slider.show()
    _APP.processEvents()
    return slider.grab().toImage()


class SliceHandleTestCase(unittest.TestCase):
    """Shared setup: a 60 s slider with a known slice."""

    def setUp(self) -> None:
        self.slider = make_slider()
        self.slider.set_slice([2.0, 30.0], DURATION_MS)

    def assert_valid_slice(self, value, *, expected_length: int | None = None) -> None:
        """The invariants every slice read back from the widget must hold.

        Written as an invariant check rather than an equality against an input,
        because the widget normalises on assignment: an expectation that named
        the input order would be testing ``normalize_slice``, not the slider.
        """
        self.assertIsNotNone(value)
        self.assertEqual(len(value), 2)
        start, end = value
        self.assertGreaterEqual(start, 0.0)
        self.assertLessEqual(start, DURATION_S)
        self.assertLessEqual(end, DURATION_S)
        self.assertGreaterEqual(end - start, MIN_SLICE_SECONDS - 1e-9)


class TestHandleHitTesting(SliceHandleTestCase):
    """Which handle, if any, a press at a given column claims."""

    def test_no_timeline_claims_nothing(self):
        slider = make_slider(duration_ms=0)
        slider.set_slice(None, 0)
        self.assertIsNone(slider.hit_handle(0))
        self.assertIsNone(slider.hit_handle(WIDGET_WIDTH // 2))
        self.assertIsNone(slider.hit_handle(WIDGET_WIDTH - 1))

    def test_exactly_on_a_handle_claims_it(self):
        # Zero distance is the case a "strictly inside the radius" test would
        # refuse, and it is the most common press of all.
        self.assertEqual(
            self.slider.hit_handle(self.slider.handle_x(HANDLE_START)), HANDLE_START
        )
        self.assertEqual(
            self.slider.hit_handle(self.slider.handle_x(HANDLE_END)), HANDLE_END
        )

    def test_every_column_inside_the_radius_claims_the_handle(self):
        for which in (HANDLE_START, HANDLE_END):
            centre = self.slider.handle_x(which)
            for offset in range(-HANDLE_HIT_PX, HANDLE_HIT_PX + 1):
                with self.subTest(handle=which, offset=offset):
                    self.assertEqual(self.slider.hit_handle(centre + offset), which)

    def test_one_column_past_the_radius_claims_nothing(self):
        for which in (HANDLE_START, HANDLE_END):
            centre = self.slider.handle_x(which)
            for sign in (-1, 1):
                with self.subTest(handle=which, sign=sign):
                    self.assertIsNone(self.slider.hit_handle(centre + sign * (HANDLE_HIT_PX + 1)))

    def test_middle_of_the_timeline_claims_nothing(self):
        # The seek is the primary gesture on this widget; it must survive
        # everywhere the handles are not. A column that happens to BE a handle is
        # not the test -- the slice in setUp puts the end handle near the middle.
        plain_x = (self.slider.handle_x(HANDLE_START) + self.slider.handle_x(HANDLE_END)) // 2
        self.assertIsNone(self.slider.hit_handle(plain_x))
        self.assertIsNone(self.slider.hit_handle(WIDGET_WIDTH - 1))

    def test_untouched_handles_sit_on_the_extremes_and_are_grabbable(self):
        slider = make_slider()
        slider.set_slice(None, DURATION_MS)
        # The END handle sits on the LAST COLUMN, not one past it: _x_at_seconds
        # is a left edge, and a column no cursor can name is not a handle.
        self.assertEqual(slider.handle_x(HANDLE_START), 0)
        self.assertEqual(slider.handle_x(HANDLE_END), WIDGET_WIDTH - 1)
        self.assertEqual(slider.hit_handle(0), HANDLE_START)
        self.assertEqual(slider.hit_handle(WIDGET_WIDTH - 1), HANDLE_END)

    def test_the_nearest_handle_wins_when_the_two_are_close(self):
        # A slice shorter than twice the hit radius puts both targets in reach
        # of one press; picking the nearer one is what makes that press mean one
        # thing instead of two. 0.5 s is 5.6 px apart on this bar.
        slider = make_slider()
        slider.set_slice([0.0, 0.5], DURATION_MS)
        start_x = slider.handle_x(HANDLE_START)
        end_x = slider.handle_x(HANDLE_END)
        self.assertLess(abs(end_x - start_x), 2 * HANDLE_HIT_PX)
        self.assertEqual(slider.hit_handle(start_x), HANDLE_START)
        self.assertEqual(slider.hit_handle(end_x), HANDLE_END)
        self.assertEqual(slider.hit_handle(start_x + 1), HANDLE_START)

    def test_an_exact_tie_goes_to_the_start_handle(self):
        # The tie can only matter where the two handles are BOTH within reach of
        # one press, so the fixture is a short slice: 0.55 s puts the end handle
        # on column 6, an EVEN distance from the start handle at 0, so column 3
        # is a genuine tie. The tie must resolve the same way every time, which
        # is the whole point of naming it.
        slider = make_slider()
        slider.set_slice([0.0, 0.55], DURATION_MS)
        start_x = slider.handle_x(HANDLE_START)
        end_x = slider.handle_x(HANDLE_END)
        self.assertEqual((start_x, end_x), (0, 6))
        midpoint = (start_x + end_x) // 2
        self.assertEqual(abs(midpoint - start_x), abs(midpoint - end_x))
        self.assertLessEqual(abs(midpoint - start_x), HANDLE_HIT_PX)
        self.assertEqual(slider.hit_handle(midpoint), HANDLE_START)

    def test_a_handle_wins_over_the_seek_on_the_same_column(self):
        # Documented precedence: the slice handle is the narrower, more specific
        # target and claims the press even where the playhead also sits.
        slider = make_slider()
        slider.set_slice([0.0, 30.0], DURATION_MS)
        slider.setValue(0)
        start_x = slider.handle_x(HANDLE_START)
        self.assertEqual(slider.value(), 0)
        self.assertEqual(slider.hit_handle(start_x), HANDLE_START)


class TestHandleClamping(unittest.TestCase):
    """The pure clamping rule behind both handles.

    Called through the static method because that IS the single implementation
    the drag path uses; the drag itself is exercised separately, so a change
    that routed drags around the clamp would fail here too.
    """

    def clamp_start(self, seconds: float, other: float = 30.0) -> float:
        return SeekSlider._clamp_handle(HANDLE_START, seconds, other, DURATION_S)

    def clamp_end(self, seconds: float, other: float = 2.0) -> float:
        return SeekSlider._clamp_handle(HANDLE_END, seconds, other, DURATION_S)

    def test_a_handle_never_leaves_the_timeline(self):
        # ``other`` is kept away from the dragged handle so the no-crossing rule
        # does not bind first and mask the timeline bound being tested here.
        self.assertEqual(self.clamp_start(-50.0, other=5.0), 0.0)
        self.assertEqual(self.clamp_end(999.0, other=55.0), DURATION_S)

    def test_the_end_handles_lower_bound_is_the_other_handle(self):
        # The mirror of the case above: the end handle stays where the user left
        # it, so a drag can only push the pair further OPEN, never closed.
        self.assertEqual(self.clamp_end(-50.0, other=55.0), 55.0 + MIN_SLICE_SECONDS)
        self.assertEqual(self.clamp_end(-50.0, other=0.0), MIN_SLICE_SECONDS)

    def test_the_start_handles_upper_bound_is_the_other_handle(self):
        # Not a special case: the start handle yields, so its ceiling is
        # ``other - MIN_SLICE_SECONDS`` wherever that is below the timeline, and
        # there is no ``other`` that would let it reach the end of the media.
        self.assertEqual(
            self.clamp_start(999.0, other=5.0), 5.0 - MIN_SLICE_SECONDS
        )
        self.assertEqual(
            self.clamp_start(999.0, other=DURATION_S), DURATION_S - MIN_SLICE_SECONDS
        )

    def test_values_inside_the_timeline_pass_through(self):
        self.assertAlmostEqual(self.clamp_start(12.5), 12.5, places=6)
        self.assertAlmostEqual(self.clamp_end(12.5), 12.5, places=6)

    def test_the_start_handle_yields_before_it_crosses(self):
        # Dragged past the end handle: it stops one minimum-slice short of it
        # rather than swapping sides, which is what keeps the pair ordered.
        clamped = self.clamp_start(45.0, other=30.0)
        self.assertAlmostEqual(clamped, 30.0 - MIN_SLICE_SECONDS, places=9)

    def test_the_end_handle_pushes_the_pair_open(self):
        clamped = self.clamp_end(1.5, other=2.0)
        self.assertAlmostEqual(clamped, 2.0 + MIN_SLICE_SECONDS, places=9)

    def test_the_minimum_slice_is_never_smaller_than_the_constant(self):
        # Both directions, and from several starting distances, because the
        # clamp is the only thing standing between a sloppy drag and a
        # zero-length range the engines would silently widen.
        for other in (2.0, 5.0, 20.0, 59.5):
            with self.subTest(other=other):
                start = self.clamp_start(other + 10.0, other=other)
                end = self.clamp_end(other - 10.0, other=other)
                self.assertGreaterEqual(other - start + 1e-9, MIN_SLICE_SECONDS)
                self.assertGreaterEqual(end - other + 1e-9, MIN_SLICE_SECONDS)

    def test_a_sub_minimum_media_keeps_the_pair_ordered(self):
        # A 50 ms media cannot hold a 100 ms slice. The minimum is then
        # unsatisfiable and the only promise left is the ordering one: the
        # clamp must not raise, and must not hand back an inverted pair.
        sub = 0.05
        for dragged, seconds in ((HANDLE_START, 0.05), (HANDLE_END, 0.0)):
            with self.subTest(dragged=dragged):
                value = SeekSlider._clamp_handle(dragged, seconds, 0.0, sub)
                self.assertLessEqual(value, sub)
                self.assertGreaterEqual(value, 0.0)

    def test_the_start_clamp_never_exceeds_the_end(self):
        for other in (0.0, 1e-6, MIN_SLICE_SECONDS, 1.0, DURATION_S):
            with self.subTest(other=other):
                self.assertLessEqual(self.clamp_start(other, other=other), other)

    def test_the_end_clamp_never_falls_below_the_start(self):
        for other in (0.0, 1e-6, MIN_SLICE_SECONDS, 1.0, DURATION_S):
            with self.subTest(other=other):
                self.assertGreaterEqual(self.clamp_end(other, other=other), other)


class TestBoundarySnapping(unittest.TestCase):
    """The extremes have to be reachable, or the delete gesture cannot exist.

    ``_x_at_seconds`` is a LEFT EDGE, so the end of the timeline is one column
    past the last one and no cursor can name it. These are the cases where that
    would silently cost the user the ability to remove a slice.
    """

    def test_a_column_worth_of_slack_snaps_onto_the_boundary(self):
        pixel_s = 60.0 / 678
        self.assertEqual(SeekSlider._snap_to_boundary(0.0, 0.0, pixel_s), 0.0)
        self.assertAlmostEqual(
            SeekSlider._snap_to_boundary(0.5 * pixel_s, 0.0, pixel_s), 0.0
        )
        self.assertAlmostEqual(
            SeekSlider._snap_to_boundary(60.0 - 0.5 * pixel_s, 60.0, pixel_s), 60.0
        )

    def test_a_real_position_one_column_in_is_not_snapped(self):
        pixel_s = 60.0 / 678
        # Two columns from the end is a place the cursor CAN name, so rounding it
        # onto the boundary would throw away resolution the grid does have.
        self.assertAlmostEqual(
            SeekSlider._snap_to_boundary(60.0 - 2 * pixel_s, 60.0, pixel_s),
            60.0 - 2 * pixel_s,
        )

    def test_no_timeline_means_no_snapping(self):
        self.assertEqual(SeekSlider._snap_to_boundary(5.0, 0.0, 0.0), 5.0)

    def test_the_pixel_is_the_groove_column_in_seconds(self):
        slider = make_slider()
        slider.set_slice(None, DURATION_MS)
        self.assertAlmostEqual(slider.pixel_seconds(), 60.0 / WIDGET_WIDTH, places=9)
        slider.set_slice(None, 0)
        self.assertEqual(slider.pixel_seconds(), 0.0)


class TestSliceState(SliceHandleTestCase):
    """Reading the slice back, and the one state that is not a slice."""

    def test_a_pair_is_returned_in_seconds(self):
        value = self.slider.slice()
        self.assert_valid_slice(value)
        self.assertAlmostEqual(value[0], 2.0, places=6)
        self.assertAlmostEqual(value[1], 30.0, places=6)

    def test_set_slice_normalises_on_assignment(self):
        # The documented lesson from T8: the input order is not what comes back,
        # so the expectation is the invariant, never the input.
        self.slider.set_slice([30.0, 2.0], DURATION_MS)
        self.assertIsNone(self.slider.slice())

    def test_untouched_handles_read_as_none(self):
        slider = make_slider()
        slider.set_slice(None, DURATION_MS)
        self.assertIsNone(slider.slice())
        self.assertEqual(slider.handle_x(HANDLE_START), 0)
        self.assertEqual(slider.handle_x(HANDLE_END), WIDGET_WIDTH - 1)

    def test_a_slice_covering_the_whole_file_reads_as_none(self):
        # Both handles on the extremes IS the untouched state: "no slice" is what
        # the design means by both-at-the-extremes, and the engines read None as
        # the whole file, so this round-trips to the same behaviour.
        slider = make_slider()
        slider.set_slice([0.0, DURATION_S], DURATION_MS)
        self.assertIsNone(slider.slice())

    def test_a_slice_starting_at_zero_is_still_a_slice(self):
        # One handle off the extreme is enough; the other may stay put.
        slider = make_slider()
        slider.set_slice([0.0, 30.0], DURATION_MS)
        value = slider.slice()
        self.assert_valid_slice(value)
        self.assertAlmostEqual(value[0], 0.0, places=6)

    def test_the_slice_is_clamped_to_the_duration_it_is_shown_over(self):
        # A hand-edited items.json can carry a slice longer than the media.
        slider = make_slider()
        slider.set_slice([1.0, 900.0], DURATION_MS)
        value = slider.slice()
        self.assert_valid_slice(value)
        self.assertLessEqual(value[1], DURATION_S)

    def test_the_returned_pair_cannot_mutate_the_widget(self):
        self.slider.slice()[0] = 999.0
        self.assertAlmostEqual(self.slider.slice()[0], 2.0, places=6)

    def test_a_short_slice_survives_a_reload(self):
        # The value read back must be storable, so it has to clear the same
        # bar the storage layer applies.
        self.slider.set_slice([0.0, MIN_SLICE_SECONDS * 2], DURATION_MS)
        value = self.slider.slice()
        self.assert_valid_slice(value)
        self.slider.set_slice(value, DURATION_MS)
        self.assert_valid_slice(self.slider.slice())

    def test_editable_state_round_trips(self):
        self.assertTrue(self.slider.slice_editable())
        self.slider.set_slice_editable(False)
        self.assertFalse(self.slider.slice_editable())
        self.slider.set_slice_editable(True)
        self.assertTrue(self.slider.slice_editable())


class TestBandGeometry(SliceHandleTestCase):
    """The painted band comes from the one pixel mapping, or not at all."""

    def test_no_band_without_a_slice(self):
        slider = make_slider()
        slider.set_slice(None, DURATION_MS)
        self.assertIsNone(slider.slice_band_rect())

    def test_no_band_without_a_timeline(self):
        self.slider.set_slice([2.0, 30.0], 0)
        self.assertIsNone(self.slider.slice_band_rect())

    def test_the_band_edges_are_the_handle_pixels(self):
        band = self.slider.slice_band_rect()
        self.assertIsNotNone(band)
        # Both edges come from the one mapping, which returns a LEFT EDGE, so
        # the rect spans from the start handle up to (excluding) the end one.
        self.assertEqual(band.left(), self.slider.handle_x(HANDLE_START))
        self.assertEqual(
            band.left() + band.width(), self.slider.handle_x(HANDLE_END)
        )

    def test_the_band_sits_on_the_groove_rows(self):
        band = self.slider.slice_band_rect()
        groove = self.slider.groove_rect()
        self.assertEqual(band.top(), groove.top())
        self.assertEqual(band.height(), groove.height())

    def test_the_handles_sit_on_the_groove_rows_and_centre_on_their_pixel(self):
        groove = self.slider.groove_rect()
        for which in (HANDLE_START, HANDLE_END):
            with self.subTest(handle=which):
                rect = self.slider.handle_rect(which)
                centre = rect.left() + rect.width() // 2
                self.assertEqual(centre, self.slider.handle_x(which))
                # Taller than the groove, so a handle is never mistaken for the
                # band it sits on, and centred on the same middle.
                self.assertGreater(rect.height(), groove.height())
                self.assertEqual(
                    rect.center().y(),
                    (groove.top() + groove.bottom()) // 2,
                )


class TestHandleDragInteraction(SliceHandleTestCase):
    """Real presses and drags through ``QTest``, offscreen."""

    def setUp(self) -> None:
        super().setUp()
        self.slider.show()
        _APP.processEvents()
        self.edits: list[tuple] = []
        self.slider.slice_edited.connect(lambda value, msg: self.edits.append((value, msg)))

    def drag(self, from_x: int, to_x: int) -> None:
        """Press, move and release at ``from_x`` / ``to_x`` on the groove row."""
        row = groove_row(self.slider)
        QTest.mousePress(
            self.slider, Qt.MouseButton.LeftButton, pos=QPoint(from_x, row)
        )
        QTest.mouseMove(self.slider, pos=QPoint(to_x, row))
        QTest.mouseRelease(
            self.slider, Qt.MouseButton.LeftButton, pos=QPoint(to_x, row)
        )

    def test_a_press_on_a_handle_moves_the_slice_and_does_not_seek(self):
        # THE regression this task exists for. The press must not run through
        # the seek path: value stays at 0 while the slice start moves.
        self.slider.setValue(0)
        start_x = self.slider.handle_x(HANDLE_START)
        row = groove_row(self.slider)

        QTest.mousePress(self.slider, Qt.MouseButton.LeftButton, pos=QPoint(start_x, row))
        self.assertEqual(self.slider.value(), 0, "a press on a handle must not seek")

        QTest.mouseMove(self.slider, pos=QPoint(start_x + 60, row))
        self.assertEqual(self.slider.value(), 0, "dragging a handle must not seek")
        value = self.slider.slice()
        self.assert_valid_slice(value)
        self.assertGreater(value[0], 2.0)

        QTest.mouseRelease(
            self.slider, Qt.MouseButton.LeftButton, pos=QPoint(start_x + 60, row)
        )
        self.assertEqual(self.slider.value(), 0, "releasing a handle must not seek")
        self.assert_valid_slice(self.slider.slice())

    def test_releasing_commits_and_announces_once(self):
        start_x = self.slider.handle_x(HANDLE_START)
        self.drag(start_x, start_x + 30)
        self.assertEqual(len(self.edits), 1)
        value, message = self.edits[0]
        self.assert_valid_slice(value)
        self.assertAlmostEqual(value[0], self.slider.slice()[0], places=9)
        self.assertTrue(message, "the owner needs something to put in the status line")

    def test_the_announced_pair_is_what_the_widget_holds(self):
        start_x = self.slider.handle_x(HANDLE_START)
        self.drag(start_x, start_x + 30)
        value, _ = self.edits[0]
        self.assertEqual(list(value), list(self.slider.slice()))

    def test_a_press_away_from_the_handles_still_seeks(self):
        self.slider.setValue(0)
        plain_x = WIDGET_WIDTH - 1 - (HANDLE_HIT_PX + 1)
        row = groove_row(self.slider)
        self.assertIsNone(self.slider.hit_handle(plain_x))

        QTest.mousePress(self.slider, Qt.MouseButton.LeftButton, pos=QPoint(plain_x, row))
        # The pre-existing seek path is setValue(_value_at(x)) followed by
        # QSlider's own press, which adds one page step on top. Asserting the
        # exact milliseconds would be asserting that page step rather than the
        # behaviour under test, so the window is one page step wide.
        expected = plain_x * DURATION_MS // WIDGET_WIDTH
        self.assertLessEqual(abs(self.slider.value() - expected), self.slider.pageStep())
        self.assertGreater(self.slider.value(), 0)
        QTest.mouseRelease(self.slider, Qt.MouseButton.LeftButton, pos=QPoint(plain_x, row))

    def test_dragging_away_from_the_handles_still_seeks(self):
        self.slider.setValue(0)
        plain_x = WIDGET_WIDTH - 1 - (HANDLE_HIT_PX + 1)
        row = groove_row(self.slider)
        QTest.mousePress(self.slider, Qt.MouseButton.LeftButton, pos=QPoint(plain_x, row))
        QTest.mouseMove(self.slider, pos=QPoint(plain_x - 100, row))
        self.assertLess(self.slider.value(), plain_x * DURATION_MS // WIDGET_WIDTH)
        QTest.mouseRelease(self.slider, Qt.MouseButton.LeftButton, pos=QPoint(plain_x - 100, row))

    def test_dragging_both_handles_to_the_extremes_deletes_the_slice(self):
        self.assertIsNotNone(self.slider.slice())
        start_x = self.slider.handle_x(HANDLE_START)
        end_x = self.slider.handle_x(HANDLE_END)
        self.drag(start_x, 0)
        self.drag(self.slider.handle_x(HANDLE_END), WIDGET_WIDTH - 1)
        self.assertIsNone(self.slider.slice())
        self.assertEqual(self.slider.handle_x(HANDLE_START), 0)
        self.assertEqual(self.slider.handle_x(HANDLE_END), WIDGET_WIDTH - 1)
        self.assertIsNone(self.slider.slice_band_rect())
        self.assertEqual(self.edits[-1][0], None)

    def test_dragging_the_start_past_the_end_cannot_invert_the_pair(self):
        start_x = self.slider.handle_x(HANDLE_START)
        end_x = self.slider.handle_x(HANDLE_END)
        self.drag(start_x, end_x + 10)
        value = self.slider.slice()
        self.assert_valid_slice(value)
        self.assertLessEqual(value[0], value[1] - MIN_SLICE_SECONDS + 1e-9)
        # The end handle did not move: it belongs to the untouched side.
        self.assertAlmostEqual(value[1], 30.0, places=3)

    def test_a_drag_without_a_timeline_cannot_edit(self):
        slider = make_slider(duration_ms=0)
        slider.set_slice(None, 0)
        slider.show()
        _APP.processEvents()
        self.assertIsNone(slider.hit_handle(0))
        row = (slider.height() - SEEK_GROOVE_HEIGHT_PX) // 2
        QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=QPoint(0, row))
        QTest.mouseMove(slider, pos=QPoint(50, row))
        QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=QPoint(50, row))
        self.assertIsNone(slider.slice())

    def test_a_blocked_edit_falls_through_to_seeking(self):
        # While a run is in flight the slider is not editable. A handle press
        # must not then drag a handle the owner is not persisting, so it has to
        # behave like any other press: a seek, and no slice edit announced.
        self.slider.set_slice_editable(False)
        self.slider.setValue(0)
        before = list(self.slider.slice())
        start_x = self.slider.handle_x(HANDLE_START)
        row = groove_row(self.slider)
        QTest.mousePress(self.slider, Qt.MouseButton.LeftButton, pos=QPoint(start_x, row))
        QTest.mouseMove(self.slider, pos=QPoint(start_x + 60, row))
        QTest.mouseRelease(
            self.slider, Qt.MouseButton.LeftButton, pos=QPoint(start_x + 60, row)
        )
        self.assertEqual(self.edits, [])
        self.assertEqual(list(self.slider.slice()), before)
        self.assertGreater(self.slider.value(), 0)


class TestPaintDistinguishesHandles(SliceHandleTestCase):
    """The dimmed/active split is measured on rendered pixels, not asserted in
    a comment.

    Both samples are taken from the SAME column of the SAME widget with the
    same geometry (the start handle sitting on the extreme at x=0), so the only
    difference between them is whether a slice exists. If the two states painted
    identically, this fails.
    """

    HANDLE_SAMPLE_X = 2

    def sample_handle(self, value):
        slider = make_slider()
        slider.set_slice(value, DURATION_MS)
        image = render(slider)
        self.assertEqual(image.width(), WIDGET_WIDTH)
        self.assertEqual(image.height(), WIDGET_HEIGHT)
        return image.pixelColor(self.HANDLE_SAMPLE_X, groove_row(slider))

    def sample_groove(self):
        slider = make_slider()
        slider.set_slice(None, DURATION_MS)
        image = render(slider)
        return image.pixelColor(WIDGET_WIDTH - 4, groove_row(slider))

    def test_untouched_and_active_handles_paint_differently(self):
        dimmed = self.sample_handle(None)
        active = self.sample_handle([0.0, 30.0])
        self.assertNotEqual(
            dimmed,
            active,
            "the dimmed/active split must be visible, not only documented",
        )
        self.assertGreater(contrast(dimmed, active), 1.05)

    def test_the_dimmed_handle_is_still_faintly_readable(self):
        # Dimmed is not invisible: the handles are the only affordance offering
        # a slice, and a target you cannot see is not offered at all.
        dimmed = self.sample_handle(None)
        groove = self.sample_groove()
        self.assertGreater(contrast(dimmed, groove), 1.2)

    def test_the_active_handle_stands_out_from_the_groove(self):
        groove = self.sample_groove()
        self.assertGreater(contrast(self.sample_handle([0.0, 30.0]), groove), 2.0)

    def test_the_active_handle_stands_out_from_the_band_it_sits_on(self):
        # The band is deliberately translucent, so it must not swallow the
        # handle that owns its edge.
        slider = make_slider()
        slider.set_slice([2.0, 30.0], DURATION_MS)
        image = render(slider)
        row = groove_row(slider)
        handle = image.pixelColor(slider.handle_x(HANDLE_START), row)
        middle_x = (slider.handle_x(HANDLE_START) + slider.handle_x(HANDLE_END)) // 2
        band = image.pixelColor(middle_x, row)
        self.assertGreater(contrast(handle, band), 2.0)


class TestPaintInBothThemes(SliceHandleTestCase):
    """The ratios the fallback colours satisfy, in BOTH real themes.

    This class exists because the tests above do NOT catch a themed contrast
    failure. ``make_slider`` builds a widget against the unstyled Fusion
    palette, so they measure ``DEFAULT_SLICE_FILL`` / ``DEFAULT_SLICE_EDGE``
    only -- the properties ``apply_theme`` publishes are never read. A theme can
    therefore ship a band and a handle that collapse into each other while every
    test above still passes, which is exactly what the light theme did: measured
    1.97:1 for the handle against its own band.

    So these three ratios are asserted per theme, on rendered pixels. 3.0 is the
    WCAG 1.4.11 floor for identifying a UI component's boundary, and a handle
    bar qualifies: it is the boundary of the region you drag. All THREE have to
    hold at once because the END handle overhangs the band's right edge, so part
    of it sits on the bare groove -- separation from the band cannot be bought
    with separation from the groove.
    """

    THEMES = (True, False)  # (dark, light)

    # The handle is the boundary of the region you drag, so WCAG 1.4.11 applies
    # and 3.0 is the floor -- against the groove AND against its own band.
    HANDLE_FLOOR = 3.0

    # The band is a translucent overlay indicating a range, not an interactive
    # boundary, and its floor is NOT the same number: the dark theme's 28% amber
    # measures 1.86 against the dark groove and that is a PRE-EXISTING,
    # documented and deliberate house value, not something this task introduced.
    # Re-litigating it here would be scope creep, so the floor is set where it
    # still catches a band that genuinely vanished -- the light theme's rejected
    # ``#4dde8f18`` measured 1.21, i.e. invisible. 1.8 sits above that.
    BAND_FLOOR = 1.8

    # Mid-groove, far from both handles and from any band, so it is a real
    # groove sample and not a pixel of the thing being compared against it.
    GROOVE_SAMPLE_X = 400

    def setUp(self) -> None:
        super().setUp()
        self._previous = {
            key: _APP.property(key)
            for key in ("markFillColor", "markEdgeColor")
        }

    def tearDown(self) -> None:
        for key, value in self._previous.items():
            _APP.setProperty(key, value)

    def measure(self, dark):
        """``(band/groove, handle/groove, handle/band)`` on rendered pixels."""
        apply_theme(_APP, dark=dark)
        slider = make_slider()
        slider.set_slice([2.0, 30.0], DURATION_MS)
        image = render(slider)
        row = groove_row(slider)
        start_x = slider.handle_x(HANDLE_START)
        end_x = slider.handle_x(HANDLE_END)
        handle = image.pixelColor(start_x, row)
        band = image.pixelColor((start_x + end_x) // 2, row)
        groove = image.pixelColor(self.GROOVE_SAMPLE_X, row)
        return contrast(band, groove), contrast(handle, groove), contrast(handle, band)

    def test_the_band_is_visible_against_the_groove_in_both_themes(self):
        for dark in self.THEMES:
            with self.subTest(theme="dark" if dark else "light"):
                band_vs_groove, _, _ = self.measure(dark)
                self.assertGreaterEqual(band_vs_groove, self.BAND_FLOOR)

    def test_the_handle_is_visible_against_the_groove_in_both_themes(self):
        for dark in self.THEMES:
            with self.subTest(theme="dark" if dark else "light"):
                _, handle_vs_groove, _ = self.measure(dark)
                self.assertGreaterEqual(handle_vs_groove, self.HANDLE_FLOOR)

    def test_the_handle_is_not_swallowed_by_the_band_in_both_themes(self):
        # The regression this class was written for: light-theme mark_edge and
        # mark_fill were the SAME hue, so the composited band and the opaque bar
        # measured 1.97:1 and the grip disappeared into its own range.
        for dark in self.THEMES:
            with self.subTest(theme="dark" if dark else "light"):
                _, _, handle_vs_band = self.measure(dark)
                self.assertGreaterEqual(handle_vs_band, self.HANDLE_FLOOR)

    def test_the_dimmed_handle_survives_in_both_themes(self):
        for dark in self.THEMES:
            with self.subTest(theme="dark" if dark else "light"):
                apply_theme(_APP, dark=dark)
                slider = make_slider()
                slider.set_slice(None, DURATION_MS)
                image = render(slider)
                row = groove_row(slider)
                dimmed = image.pixelColor(slider.handle_x(HANDLE_END), row)
                groove = image.pixelColor(self.GROOVE_SAMPLE_X, row)
                # Faint, not gone: this is the only affordance offering a slice.
                self.assertGreater(contrast(dimmed, groove), 1.2)
                self.assertLess(contrast(dimmed, groove), self.HANDLE_FLOOR)

    def test_the_two_themes_really_paint_different_pixels(self):
        # Non-vacuity: were these reading the unstyled fallback, both themes
        # would publish the same colours. Note the DARK edge legitimately IS
        # DEFAULT_SLICE_EDGE, so the edge alone is not a usable witness -- the
        # fill is, because the two themes ship different fills.
        published = {}
        for dark in self.THEMES:
            apply_theme(_APP, dark=dark)
            published[dark] = (
                QColor(_APP.property("markFillColor")),
                QColor(_APP.property("markEdgeColor")),
            )
        dark_fill, dark_edge = published[True]
        light_fill, light_edge = published[False]
        self.assertNotEqual(dark_fill, light_fill)
        self.assertNotEqual(dark_edge, light_edge)
        # The handle is painted opaque; the band is the translucent one.
        self.assertEqual(dark_edge.alpha(), 255)
        self.assertEqual(light_edge.alpha(), 255)
        self.assertLess(light_fill.alpha(), 255)


class TestAccessibilityCopy(unittest.TestCase):
    """The widget describes itself, and it describes what it now does."""

    def setUp(self) -> None:
        self.slider = make_slider()

    def test_the_accessible_name_names_the_handles(self):
        name = self.slider.accessibleName()
        self.assertTrue(name)
        self.assertIn("handle", name.lower())

    def test_the_tooltip_describes_dragging_and_not_the_old_menu(self):
        tip = self.slider.toolTip()
        self.assertIn("Arrastra", tip)
        self.assertIn("handle", tip.lower())
        # The right-click menu it replaced must not be advertised any more.
        self.assertNotIn("Clic derecho", tip)

    def test_the_tooltip_reports_the_current_slice(self):
        self.slider.set_slice([2.0, 30.0], DURATION_MS)
        tip = self.slider.toolTip()
        self.assertIn("00:02", tip)
        self.assertIn("00:30", tip)

    def test_the_tooltip_says_when_there_is_no_slice(self):
        self.slider.set_slice(None, DURATION_MS)
        self.assertIn("archivo completo", self.slider.toolTip())