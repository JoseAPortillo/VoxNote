"""Offscreen evidence for the wiring between a slice and the main window.

T3 declared a real coverage hole and refused to paper over it: ``MainWindow`` was
never built in a test, because its ``__init__`` creates a real ``MediaRepository``
and a real ``Pipeline``, so a test that built one would exercise the filesystem
and the model loader as much as the widget. That left the thing T3 actually
changed -- whether ``_on_position_changed`` calls into the boundary helpers --
unverified.

It does not have to be that way. Two names are resolved in
``app.ui.main_window``'s namespace at construction time, so replacing them
redirects every effect the window has:

- ``MediaRepository`` is built with no argument and defaults to ``data/items.json``
  in the repository. Pointed at a temporary file, the window's persistence is real
  code over a scratch file instead of a developer's registry.
- ``Transcriber`` would load a ``large-v3-turbo`` model. Replaced by a double, so
  the window still builds its pipeline for real without the model.

What is exercised below is the window's own logic: the position signal, the play
entry, the loop availability, the divergence note and result persistence. Nothing
here asserts against the helpers in isolation -- T3 already does that. These tests
exist to prove the window is actually WIRED to them.

Offscreen, ``QT_QPA_PLATFORM=offscreen``.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import app.storage.repository as repository_module
import app.ui.main_window as main_window
from app.core.models import MediaItem, TimedSegment
from app.storage.repository import MediaRepository

# One media, one slice, in seconds. The window works in milliseconds.
MEDIA = "C:/medios/entrevista.wav"
SLICE = [10.0, 20.0]
DURATION_MS = 60_000


class _FakeInfo:
    duration = 0.0
    language = "es"


class _FakeTranscriber:
    """Stands in for the model loader without loading a model."""

    def transcribe_segments(self, path, task="transcribe", ranges=None):
        return [], _FakeInfo()

    def transcribe(self, path, language=None):
        return []


class _RecordingPlayer:
    """Stands in for a source-less ``QMediaPlayer`` and records seeks.

    Offscreen, a ``QMediaPlayer`` with no source silently ignores ``setPosition``
    and always reports 0, so asserting on it would test Qt's no-source behaviour
    instead of the window's. Every ``setPosition`` the window issues is recorded,
    which is the whole claim: what position did the window decide on? ``pauses``
    counts the same way, because whether the window paused is the other half of
    the boundary decision.
    """

    def __init__(self, position: int = 0):
        self._position = position
        self.writes: list[int] = []
        self.pauses = 0

    def position(self) -> int:
        return self._position

    def setPosition(self, ms: int) -> None:  # noqa: N802 - Qt's own spelling
        self._position = ms
        self.writes.append(ms)

    # Transport methods the window calls on teardown (``closeEvent`` stops the
    # player). Present so the window can close itself; none of them is what is
    # under test.
    def stop(self) -> None:
        pass

    def play(self) -> None:
        pass

    def pause(self) -> None:
        self.pauses += 1

    def playbackState(self):  # noqa: N802 - Qt's own spelling
        from PySide6.QtMultimedia import QMediaPlayer

        return QMediaPlayer.PlaybackState.StoppedState


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class MainWindowSliceWiring(unittest.TestCase):
    """The window's own logic, over a scratch registry, without a screen."""

    @classmethod
    def setUpClass(cls) -> None:
        _app()

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.items = Path(tmp.name) / "items.json"
        self.media = Path(tmp.name) / "entrevista.wav"
        self.media.write_bytes(b"not real audio; no decoding happens here")

        # Both names are looked up in the module namespace when the window is
        # built, so these two assignments are the whole injection.
        self._saved = (main_window.MediaRepository, main_window.Transcriber)
        main_window.MediaRepository = lambda *a, **k: MediaRepository(self.items)
        main_window.Transcriber = _FakeTranscriber
        self.addCleanup(self._restore)

        self.repo = MediaRepository(self.items)
        self.repo.add(str(self.media))
        self.repo.update_slice(0, list(SLICE))

        self.window = main_window.MainWindow()
        self.addCleanup(self.window.close)

        # The injection is ASSERTED, not assumed. If the window ever stopped
        # resolving these names in its own module namespace, every test here would
        # still pass while quietly loading a multi-gigabyte model or writing to
        # the real ``data/items.json``. Failing here says that instead.
        self.assertIsInstance(self.window.pipeline.transcriber, _FakeTranscriber)
        self.assertEqual(Path(self.window.repository.path), self.items)

    def _restore(self) -> None:
        main_window.MediaRepository, main_window.Transcriber = self._saved

    def _load_with_slice(self) -> None:
        """Put the window in the state it has when that media is loaded."""
        self.window._playing_path = str(self.media)
        self.window._establish_timeline(DURATION_MS)

    def stored_slice(self):
        """Read back from DISK, through a fresh repository."""
        return MediaRepository(self.items).get(0).slice

    # -- the wiring T3 could not reach -------------------------------------

    def test_the_stored_slice_is_painted_on_load(self):
        self._load_with_slice()
        self.assertEqual(self.window._slice_bounds_ms(), (10_000, 20_000))

    def test_the_position_signal_enforces_the_boundary(self):
        # The hole T3 declared: positionChanged is only bounded if the window is
        # actually wired to the helper. Reported past the end, the resolved
        # position must come back to the start and the slider must show it.
        self._load_with_slice()
        self.window._on_position_changed(21_000)
        self.assertEqual(self.window.seek_slider.value(), 10_000)

    def test_a_report_past_the_end_never_reaches_the_display(self):
        # Worth being precise about what free seek does and does not promise. It
        # means the user's seek TARGET is not clamped (T3 pins that on the pure
        # helpers). It does not mean an illegal report is shown: this is the
        # report path, and by the time it runs the player has already left the
        # slice, so the display shows the resolved position instead.
        self._load_with_slice()
        self.window._on_position_changed(25_000)
        self.assertEqual(self.window.seek_slider.value(), 10_000)

    def test_a_legal_position_is_left_alone(self):
        # The overwhelmingly common case, and the one the pure helpers cannot
        # reach: they prove the arithmetic of the decision, but only the window
        # can prove it has NO side effect here. A rewind on a legal report would
        # fight a player that is behaving, and nothing else would catch it.
        self._load_with_slice()
        recorder = _RecordingPlayer(15_000)
        self.window.player = recorder
        self.window._on_position_changed(15_000)
        self.assertEqual(recorder.writes, [])
        self.assertEqual(self.window.seek_slider.value(), 15_000)

    def test_reaching_the_end_without_the_loop_stops_the_player(self):
        # The rewind is only half of this behaviour: with the loop off, playback
        # must also STOP. ``positionChanged`` is the only thing that can pause it
        # now, so the window is what has to issue the pause -- the pure helper's
        # ``keep_playing`` flag is not the transport.
        self._load_with_slice()
        recorder = _RecordingPlayer(20_000)
        self.window.player = recorder
        self.window._on_position_changed(20_000)
        self.assertEqual(recorder.writes, [10_000])
        self.assertEqual(recorder.pauses, 1)

    def test_reaching_the_end_with_the_loop_on_keeps_playing(self):
        # The other half of the same decision, and the branch nothing covered:
        # with the loop on, the slice has to REPEAT, so it rewinds and must NOT
        # pause. A pause here would quietly turn the loop into a stop, which is
        # a defect no user would report as "the loop button is broken".
        self._load_with_slice()
        self.window._on_loop_toggled(True)
        recorder = _RecordingPlayer(21_000)
        self.window.player = recorder
        self.window._on_position_changed(21_000)
        self.assertEqual(recorder.writes, [10_000])
        self.assertEqual(recorder.pauses, 0)

    def test_a_seek_while_the_handle_is_held_is_ignored(self):
        # Dragging the end handle moves the slider past the current position on
        # purpose. Enforcement here would fight the drag, so the whole method
        # bails out -- the slider keeps the value it had, not a rewound one.
        self._load_with_slice()
        self.window.seek_slider.setValue(30_000)
        self.window._seeking = True
        self.window._on_position_changed(30_000)
        self.assertEqual(self.window.seek_slider.value(), 30_000)

    def test_play_from_past_the_end_enters_at_the_start(self):
        # An offscreen QMediaPlayer with no source ignores setPosition, so the
        # player is replaced by a recorder. What is under test is the window's
        # decision -- "play starts inside the slice" -- and not Qt's transport.
        self._load_with_slice()
        recorder = _RecordingPlayer(40_000)
        self.window.player = recorder
        self.window._enter_slice_on_play()
        self.assertEqual(recorder.writes, [10_000])

    def test_play_from_inside_the_slice_does_not_move_the_player(self):
        # Re-entering on every play would fight a user who paused mid-slice and
        # pressed play to resume.
        self._load_with_slice()
        recorder = _RecordingPlayer(15_000)
        self.window.player = recorder
        self.window._enter_slice_on_play()
        self.assertEqual(recorder.writes, [])

    def test_the_loop_control_follows_the_slice(self):
        # A loop control enabled with no slice to loop would promise something
        # the playback cannot deliver.
        self.window._playing_path = None
        self.window._establish_timeline(0)
        self.assertFalse(self.window.btn_loop.isEnabled())
        self._load_with_slice()
        self.assertTrue(self.window.btn_loop.isEnabled())

    def _select(self, path: str) -> None:
        """Put the CURRENT row on ``path``, which is what a click does.

        ``MediaList`` exposes ``selected_path`` but no setter -- selection comes
        from the tree's own current item -- so the row is located and selected the
        way ``QTreeWidget`` does it, and ``selected_path`` is then asked to agree.
        """
        view = self.window.media_list
        for row in range(view.topLevelItemCount()):
            item = view.topLevelItem(row)
            if item.data(0, Qt.UserRole) == path:
                view.setCurrentItem(item)
                break
        self.assertEqual(view.selected_path(), path, "the row did not become current")

    # -- the divergence, through the window --------------------------------

    def test_no_divergence_while_nothing_is_loaded(self):
        self.assertEqual(self.window._slice_scope_note(), "")

    def test_a_second_media_in_the_registry_is_named_on_both_sides(self):
        # The window reads the SELECTED row, so divergence needs a second row.
        # Written through the WINDOW's own repository and followed by a reload:
        # the list holds its own repository reference, so a second instance
        # writing the same file would leave the rows stale.
        other = Path(self.media).parent / "audio-notas.mp3"
        self.window.repository.add(str(other))
        self.window.media_list.reload()
        self.window._playing_path = str(self.media)
        self.window._establish_timeline(DURATION_MS)
        self._select(str(other))
        note = self.window._slice_scope_note()
        self.assertIn("entrevista", note)
        self.assertIn("audio-notas", note)

    def test_the_loaded_media_selected_is_no_divergence(self):
        self._load_with_slice()
        self._select(str(self.media))
        self.assertEqual(self.window._slice_scope_note(), "")

    # -- the T4 acceptance criterion, at the persistence layer ------------

    def test_persisting_results_leaves_the_slice_exactly_where_it_was(self):
        # The acceptance criterion is about the RUN, and the run reaches disk
        # through this window. Persisting a result is the write the run performs,
        # so the slice surviving it is the criterion at the layer where the user
        # would actually lose it.
        before = json.loads(self.items.read_text(encoding="utf-8"))
        self.window._running_path = str(self.media)
        self.window._persist_results(
            transcript_text="primero\nsegundo",
            transcript_segments=[TimedSegment(10.0, 14.0, "primero", None)],
        )
        self.assertEqual(self.stored_slice(), SLICE)
        self.assertEqual(
            json.loads(self.items.read_text(encoding="utf-8"))[0]["transcript_text"],
            "primero\nsegundo",
        )

    def test_persisting_a_second_run_repeats_the_same_result(self):
        # A run that quietly consumed the slice would make the SECOND run of the
        # same slice widen to the whole file. Two identical writes are how that
        # shows up at this layer: the second one must be scoped exactly like the
        # first, so the stored slice is what is checked, never re-asserted.
        self.window._running_path = str(self.media)
        for _ in range(2):
            self.window._persist_results(transcript_text="primero\nsegundo")
        self.assertEqual(self.stored_slice(), SLICE)

    def test_no_running_media_persists_nothing_at_all(self):
        before = json.loads(self.items.read_text(encoding="utf-8"))
        self.window._running_path = None
        self.window._persist_results(transcript_text="fantasma")
        self.assertEqual(json.loads(self.items.read_text(encoding="utf-8")), before)


class TheRealRegistryIsNeverTouched(unittest.TestCase):
    """A guard on the scratch registry, so the tests above cannot drift into lying.

    The injection in ``setUp`` is what keeps these tests off the developer's real
    ``data/items.json``. If it ever stopped taking effect, every test above would
    still pass while writing to the real registry, so the real file is read before
    and after the window is built and must not change.

    Asserting that the file does not exist would be the wrong claim: it legitimately
    exists in a working checkout, because the app itself creates it.
    """

    def test_the_project_registry_is_a_different_file_than_the_scratch_one(self):
        from app.core.config import ITEMS_FILE

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.assertNotEqual(ITEMS_FILE, Path(tmp.name) / "items.json")

    def test_building_the_window_does_not_write_the_project_registry(self):
        from app.core.config import ITEMS_FILE

        before = ITEMS_FILE.read_bytes() if ITEMS_FILE.exists() else None
        _app()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        items = Path(tmp.name) / "items.json"

        saved = (main_window.MediaRepository, main_window.Transcriber)
        main_window.MediaRepository = lambda *a, **k: MediaRepository(items)
        main_window.Transcriber = _FakeTranscriber
        try:
            window = main_window.MainWindow()
            window.close()
        finally:
            main_window.MediaRepository, main_window.Transcriber = saved

        after = ITEMS_FILE.read_bytes() if ITEMS_FILE.exists() else None
        self.assertEqual(before, after, f"{ITEMS_FILE} changed while building the window")


if __name__ == "__main__":
    unittest.main()