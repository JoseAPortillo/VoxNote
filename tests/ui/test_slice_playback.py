"""Tests for the bounded playback the slice imposes on both players.

Run with::

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

Qt runs offscreen (``QT_QPA_PLATFORM`` is set below, BEFORE PySide6 is imported,
because the platform plugin is chosen at import time).

WHAT IS WORTH A TEST HERE, and why it is split the way it is. The risk in bounded
playback is not the wiring, it is the DECISION at the boundary: whether a given
position keeps playing, stops, or jumps. That decision is a pure function of
three numbers, so it is written as one and tested exactly. The wiring around it
is three lines per play site and is exercised by the app itself, not here.

``MainWindow`` is deliberately never constructed: its ``__init__`` builds a real
``MediaRepository`` (which reads and writes ``items.json``) and a real
``Pipeline``, so a test that built it would be testing the filesystem as much as
the widget. The gap this leaves -- that ``_on_position_changed`` is wired to
these helpers -- is real and is recorded as such in the feature document; it is
covered by the offscreen evidence task, not pretended away here.

The video window IS constructed, because it is pure widgets: no repository, no
player, no threads. That is where the loop button lives in both windows, and a
mirrored control that can desynchronise is worth pinning.
"""

from __future__ import annotations

import os

# Before any PySide6 import: the QPA plugin is chosen when QtGui loads.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtWidgets import QApplication

from app.ui.main_window import resolve_slice_position, slice_entry_position
from app.ui.theme import apply_theme
from app.ui.video_player_window import VideoPlayerWindow

_APP = QApplication.instance() or QApplication([])

# One shared application, guarded so importing this module twice (discovery
# plus a direct run) cannot construct a second one.
_APP = QApplication.instance() or QApplication([])

# A slice in milliseconds: 2.000 s .. 30.000 s of a longer media. Both bounds
# matter -- the start is where a rewind lands, the end is the boundary.
BOUNDS = (2_000, 30_000)

# Well inside, well before, and past the end, so no test relies on a position
# that happens to sit exactly on the boundary unless that is the case under test.
INSIDE = 12_345
BEFORE = 500
PAST_END = 31_500


class TestResolveSlicePosition(unittest.TestCase):
    """Where playback goes when the player reports a position."""

    def test_no_slice_leaves_the_position_and_the_play_state_alone(self):
        self.assertEqual(resolve_slice_position(None, INSIDE, loop=False), (INSIDE, True))
        self.assertEqual(resolve_slice_position(None, INSIDE, loop=True), (INSIDE, True))

    def test_inside_the_slice_nothing_happens(self):
        self.assertEqual(resolve_slice_position(BOUNDS, INSIDE, False), (INSIDE, True))
        self.assertEqual(resolve_slice_position(BOUNDS, INSIDE, True), (INSIDE, True))

    def test_one_millisecond_short_of_the_end_is_still_inside(self):
        # The boundary is ``end`` itself, not ``end - 1``. An off-by-one here
        # would cut the last millisecond of every slice, which is exactly the
        # kind of thing nobody notices until a clip ends a syllable early.
        self.assertEqual(resolve_slice_position(BOUNDS, 29_999, False), (29_999, True))

    def test_reaching_the_end_rewinds_to_the_start_and_stops(self):
        self.assertEqual(resolve_slice_position(BOUNDS, 30_000, False), (2_000, False))

    def test_overshooting_the_end_also_rewinds_and_stops(self):
        # The player does not stop exactly on the boundary: it reports positions
        # at its own cadence, so the first report past the end is already over
        # it. Treating "past" as "at" is what keeps the media from playing on
        # beyond the slice.
        self.assertEqual(resolve_slice_position(BOUNDS, PAST_END, False), (2_000, False))

    def test_reaching_the_end_with_the_loop_on_rewinds_and_keeps_playing(self):
        self.assertEqual(resolve_slice_position(BOUNDS, 30_000, True), (2_000, True))
        self.assertEqual(resolve_slice_position(BOUNDS, PAST_END, True), (2_000, True))

    def test_a_slice_starting_at_zero_rewinds_to_zero(self):
        # Guards against a rewind that lands on ``start + something``: for a
        # slice that begins at the very beginning, the landing position is the
        # beginning itself.
        self.assertEqual(resolve_slice_position((0, 1_000), 1_000, False), (0, False))

    def test_the_rewind_lands_on_the_start_not_on_the_current_position(self):
        position, _ = resolve_slice_position(BOUNDS, PAST_END, True)
        self.assertEqual(position, BOUNDS[0])


class TestSliceEntryPosition(unittest.TestCase):
    """Where playback starts when the user presses play.

    Separate from the end-of-slice rule because the two answer different
    questions. This one runs on a deliberate user action, where the player may
    legitimately be parked anywhere -- including past the end, because the seek
    is free and the user may well have parked there on purpose.
    """

    def test_no_slice_plays_from_wherever_the_player_is(self):
        self.assertEqual(slice_entry_position(None, INSIDE), INSIDE)
        self.assertEqual(slice_entry_position(None, 45_000), 45_000)

    def test_before_the_start_jumps_to_it(self):
        self.assertEqual(slice_entry_position(BOUNDS, BEFORE), 2_000)

    def test_exactly_at_the_start_stays_there(self):
        # The start IS inside the slice. A >= here would restart a slice the
        # user paused at its very first millisecond.
        self.assertEqual(slice_entry_position(BOUNDS, 2_000), 2_000)

    def test_inside_the_slice_resumes_where_it_was_paused(self):
        self.assertEqual(slice_entry_position(BOUNDS, INSIDE), INSIDE)

    def test_at_the_end_restarts_from_the_start(self):
        # Paused exactly on the end is the state the bounded stop leaves behind,
        # so pressing play again has to restart rather than sit still.
        self.assertEqual(slice_entry_position(BOUNDS, 30_000), 2_000)

    def test_past_the_end_restarts_from_the_start(self):
        self.assertEqual(slice_entry_position(BOUNDS, 45_000), 2_000)

    def test_a_zero_width_slice_cannot_pin_play_at_one_instant(self):
        # Not reachable through the UI (``MIN_SLICE_SECONDS`` forbids it), but the
        # helper must still terminate rather than trap the position: a zero-width
        # slice makes every position either before it or at/past its end, so play
        # parks on the start instead of oscillating between start and end.
        position = slice_entry_position((5_000, 5_000), 5_000)
        self.assertEqual(position, 5_000)


class TestVideoLoopButton(unittest.TestCase):
    """The loop toggle exists in both windows and mirrors without feedback.

    The window is pure widgets -- no repository, no player, no worker -- so it
    can be built here where ``MainWindow`` cannot.
    """

    def setUp(self) -> None:
        self.window = VideoPlayerWindow()

    def tearDown(self) -> None:
        self.window.close()

    def test_it_starts_unchecked(self):
        # Not persisted: a fresh window has no loop, whatever the last session did.
        self.assertFalse(self.window.loop_button.isChecked())

    def test_clicking_it_announces_the_loop_once(self):
        seen: list[bool] = []
        self.window.loop_toggled.connect(seen.append)
        self.window.loop_button.click()
        self.assertEqual(seen, [True])

    def test_a_programmatic_sync_does_not_announce_back(self):
        # The owner mirrors the state of the OTHER window's button into this
        # one. If that re-emitted, mirroring would toggle the owner's own state
        # back off -- the loop would switch itself off as soon as it was switched
        # on. This is the regression that pins it.
        seen: list[bool] = []
        self.window.loop_toggled.connect(seen.append)
        self.window.set_loop_state(True, available=True)
        self.assertTrue(self.window.loop_button.isChecked())
        self.assertEqual(seen, [])

    def test_a_programmatic_sync_can_also_switch_it_off(self):
        seen: list[bool] = []
        self.window.loop_toggled.connect(seen.append)
        self.window.set_loop_state(True, available=True)
        self.window.set_loop_state(False, available=True)
        self.assertFalse(self.window.loop_button.isChecked())
        self.assertEqual(seen, [])

    def test_without_a_slice_the_button_is_disabled(self):
        # Availability is mirrored too. A button that stayed live with no slice
        # would let the loop be switched on for nothing, and then start looping
        # as soon as the user drew a slice -- a trap set by a click that appeared
        # to do nothing.
        seen: list[bool] = []
        self.window.loop_toggled.connect(seen.append)
        self.window.set_loop_state(False, available=False)
        self.assertFalse(self.window.loop_button.isEnabled())
        self.window.loop_button.click()
        self.assertEqual(seen, [], "a disabled button must not announce anything")
        self.assertFalse(self.window.loop_button.isChecked())

    def test_a_slice_makes_the_button_live_without_turning_the_loop_on(self):
        self.window.set_loop_state(False, available=True)
        self.assertTrue(self.window.loop_button.isEnabled())
        self.assertFalse(
            self.window.loop_button.isChecked(),
            "a slice appearing must not silently switch the loop on",
        )

    def test_it_says_what_it_does(self):
        name = self.window.loop_button.accessibleName()
        self.assertTrue(name, "a glyph-only control needs an accessible name")
        tip = self.window.loop_button.toolTip()
        self.assertTrue(tip)
        # Both mentions of "loop" in the name and the tooltip are what make the
        # glyph readable to a screen reader and on hover.
        self.assertIn("bucle", f"{name} {tip}".lower())

    def test_checking_it_actually_changes_how_it_looks(self):
        # The state assertions above all pass on a button that toggles with NO
        # visible change, because ``isChecked()`` never consults the stylesheet.
        # That is the one failure mode a checkable transport control has that a
        # label cannot: the user clicks it, the model says "looping", and the
        # screen looks identical. So the claim under test is the pixels: the
        # rendered button must differ once checked.
        #
        # Sampled under both real themes, because a rule added for the dark
        # palette only is the same invisible control wearing a stylesheet.
        for dark in (True, False):
            with self.subTest(dark=dark):
                apply_theme(_APP, dark=dark)
                unchecked = self._sample_loop_button(False)
                checked = self._sample_loop_button(True)
                self.assertNotEqual(
                    checked,
                    unchecked,
                    f"loop button looks identical checked and unchecked (dark={dark})",
                )

    def _sample_loop_button(self, checked: bool):
        """The background colour the loop button actually renders with."""
        button = self.window.loop_button
        button.blockSignals(True)
        try:
            button.setChecked(checked)
        finally:
            button.blockSignals(False)
        button.show()
        _APP.processEvents()
        # Just inside the button's own edge, above and below the glyph, so the
        # sample is the background and never the text.
        image = button.grab().toImage()
        return image.pixelColor(4, 4).name()