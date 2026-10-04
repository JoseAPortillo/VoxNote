"""Tests for what a run is scoped BY, and for what a run leaves behind.

Run with::

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

THREE CLAIMS, one per class, and the reason they are worth a test at all.

1. PROGRESS IS MEASURED AGAINST THE SLICE, not the file. A scoped run decodes
   one slice and reports ``segment.end / total``. Segment times are ABSOLUTE
   (see ``Transcriber.transcribe_segments``: window offsets are added back), so
   a run over a 10-20 s slice of a 10-minute file tops out near 3% and the bar
   crawls to the end of the run still nowhere near done. The denominator has to
   be the slice's own end, and once it is, the file does not need opening at all
   -- the old code decoded the whole media just to compute a number the slice
   already contained.

2. THE SLICE SURVIVES THE RUN. The old pipeline CLEARED the marks a run had
   used, so a second run of the same slice was silently the whole file. The
   slice is the user's standing intent for a media, not a one-shot ticket, and
   this is the claim that pins it: the stored pair is untouched on disk after a
   successful run.

3. THE DIVERGENCE IS A NOTE, NOT AN ACCUSATION. Selecting a row while another
   media is loaded is ordinary use, not a mistake, so the line that says so is
   short and names what is on screen next to what a run will target.
"""

from __future__ import annotations

import json
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from app.core.models import MediaItem
from app.services.pipeline import Pipeline
from app.services.transcriber import progress_total
from app.storage.repository import MediaRepository

TS = "2026-10-04T12:00:00"

# The slice the run is scoped by, in seconds.
SLICE = [10.0, 20.0]


def write_silent_wav(path: Path, seconds: float = 1.0, rate: int = 16_000) -> None:
    """A real, decodable, silent WAV, written with the stdlib only.

    Not an empty placeholder: PyAV has to be able to open it, because the run
    under test asks for the media's duration. Generating it with ``wave`` keeps
    the fixture honest without making the suite depend on a checked-in binary or
    on an encoder being installed.
    """
    frames = b"\x00\x00" * int(seconds * rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)


class FakeInfo:
    """What ``transcribe_segments`` returns alongside the segments."""

    def __init__(self, duration: float = 600.0, language: str = "en") -> None:
        self.duration = duration
        self.language = language


class FakeSegment:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text


class FakeTranscriber:
    """Returns two absolute-timed segments inside SLICE, without any model."""

    def get_model(self):
        return self

    def transcribe_segments(self, path, task="transcribe", ranges=None):
        return (
            [
                FakeSegment(SLICE[0] + 1.0, SLICE[0] + 4.0, "primero"),
                FakeSegment(SLICE[0] + 6.0, SLICE[1], "segundo"),
            ],
            FakeInfo(),
        )


class TestProgressIsMeasuredAgainstTheSlice(unittest.TestCase):
    """The denominator is the slice's end, and needs no media to compute."""

    def test_a_slice_run_finishes_at_the_slices_end(self):
        # The segments of a scoped run end at the slice end (20 s), so a total of
        # 20 makes the last one read 100%. A 600 s total would read 3.3%.
        self.assertEqual(progress_total("nope.wav", FakeInfo(600.0), [tuple(SLICE)]), 20.0)

    def test_it_does_not_open_the_media_it_already_knows_the_answer_to(self):
        # "nope.wav" does not exist. Reaching for it is the old behaviour: a full
        # container open, on every run, to compute a number the caller already
        # handed in. So a missing path is a legitimate input here, and the old
        # code failing on it is the defect rather than a broken fixture.
        total = progress_total("nope.wav", FakeInfo(600.0), [(0.0, 8.0)])
        self.assertEqual(total, 8.0)

    def test_a_whole_file_run_still_measures_against_the_file(self):
        # No slice: nothing to narrow it to, so the media's own length is the
        # right denominator and info.duration already carries it.
        self.assertEqual(progress_total("nope.wav", FakeInfo(600.0), None), 600.0)
        self.assertEqual(progress_total("nope.wav", FakeInfo(600.0), []), 600.0)

    def test_the_last_range_wins_when_several_are_given(self):
        # The engines still take a list of ranges even though the UI only ever
        # builds one, so the contract has to hold for more than one: the run ends
        # at the last range's end.
        self.assertEqual(
            progress_total("nope.wav", FakeInfo(600.0), [(1.0, 2.0), (30.0, 40.0)]),
            40.0,
        )

    def test_a_zero_length_range_does_not_divide_by_nothing_downstream(self):
        # (5, 5) is a degenerate slice, but it is ABSOLUTE time, not an offset:
        # the end is 5, so 5 is what makes the timeline end. Handing back the
        # LENGTH instead (0) would put the last segment at 5 s over a 0 s total,
        # and the callers' ``if total`` guard would drop the progress entirely.
        self.assertEqual(progress_total("nope.wav", FakeInfo(600.0), [(5.0, 5.0)]), 5.0)


class TestTheSliceSurvivesTheRun(unittest.TestCase):
    """A run does not consume the slice it was scoped by."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.items_file = Path(tmp.name) / "items.json"
        self.media = Path(tmp.name) / "clip.wav"
        write_silent_wav(self.media)
        self.repo = MediaRepository(self.items_file)
        self.repo.add(str(self.media))
        self.repo.update_slice(0, list(SLICE))
        self.stored_before = json.loads(self.items_file.read_text(encoding="utf-8"))
        # Wired the way the app wires it: transcriber only. Consumption was only ever
        # reachable THROUGH a repository, so the pipeline is built here without one
        # on purpose — the test above pins that this is not an oversight but the
        # shape the class now has, and therefore that nothing in a run can reach
        # storage.
        self.pipeline = Pipeline(transcriber=FakeTranscriber())

    def stored_slice(self):
        """Read back from DISK, not from the object that still holds it."""
        return MediaRepository(self.items_file).get(0).slice

    def test_a_successful_run_leaves_the_slice_exactly_where_it_was(self):
        self.pipeline.transcribe(str(self.media), ranges=[tuple(SLICE)])
        self.assertEqual(self.stored_slice(), SLICE)

    def test_the_bytes_on_disk_are_untouched_by_the_run(self):
        # Stronger than the value: the run wrote nothing at all. A second run of
        # the same slice therefore scopes identically, which is the whole point.
        self.pipeline.transcribe(str(self.media), ranges=[tuple(SLICE)])
        self.assertEqual(
            json.loads(self.items_file.read_text(encoding="utf-8")), self.stored_before
        )

    def test_the_result_is_still_the_slices_content(self):
        # The guard above is only meaningful if the run really happened over the
        # slice, so the run's own output is asserted too.
        result = self.pipeline.transcribe(str(self.media), ranges=[tuple(SLICE)])
        self.assertEqual(result.transcript_text, "primero\nsegundo")

    def test_the_pipeline_has_no_storage_dependency_left(self):
        # Consumption was the pipeline's only reason to know about the repository.
        # A service that transcribes has no business writing to storage, and
        # keeping the parameter would let the coupling grow back silently.
        import inspect

        self.assertNotIn("repository", inspect.signature(Pipeline.__init__).parameters)

    def test_it_has_no_repository_attribute_to_hide_one_in(self):
        # Removing the parameter alone would not close the coupling: the storage
        # could still be stashed on the instance and called from anywhere. The
        # attribute is what makes "a run cannot write to storage" structural
        # rather than a promise about the current call sites.
        self.assertFalse(
            [name for name in vars(Pipeline(transcriber=FakeTranscriber())) if "repo" in name]
        )

    def test_a_whole_file_run_also_leaves_the_slice_alone(self):
        # No slice consumed it, but the point is that the slice is the user's
        # standing intent for the media, not something a run retires.
        self.pipeline.transcribe(str(self.media), ranges=None)
        self.assertEqual(self.stored_slice(), SLICE)

    def test_a_failing_run_also_leaves_the_slice_alone(self):
        # The old contract cleared only on success, which was right about failure
        # but is now moot: nothing clears it, ever. Pinned because "the failed run
        # must not eat my slice" is the case a user would hit after a crash.
        class Exploding(FakeTranscriber):
            def transcribe_segments(self, path, task="transcribe", ranges=None):
                raise RuntimeError("decodificación fallida")

        pipeline = Pipeline(transcriber=Exploding())
        with self.assertRaises(RuntimeError):
            pipeline.transcribe(str(self.media), ranges=[tuple(SLICE)])
        self.assertEqual(self.stored_slice(), SLICE)


class TestTheScopeStatusNamesTheSlice(unittest.TestCase):
    """Engine-facing status lines speak in slices, not in legacy marks."""

    def test_a_scoped_run_says_it_is_loading_the_slice(self):
        for ranges in ([tuple(SLICE)], [(0.0, 1.0)]):
            with self.subTest(ranges=ranges):
                self.assertIn("slice", Pipeline._loading_status(ranges).lower())

    def test_a_diarized_scoped_run_says_it_too(self):
        self.assertIn("slice", Pipeline._diarizing_status([tuple(SLICE)]).lower())

    def test_no_status_line_speaks_of_a_mark_any_more(self):
        # "tramo marcado" is the leftover vocabulary of the marks era. The slice
        # is a single pair now, and a status line that says "marked range" sends
        # the user looking for a second handle that does not exist.
        for ranges in (None, [tuple(SLICE)]):
            for status in (
                Pipeline._loading_status(ranges),
                Pipeline._diarizing_status(ranges),
            ):
                with self.subTest(status=status):
                    self.assertNotIn("marcad", status.lower())

    def test_an_unscoped_run_does_not_claim_a_slice(self):
        self.assertNotIn("slice", Pipeline._loading_status(None).lower())


class TestTheDivergenceIsANote(unittest.TestCase):
    """Selecting another row while a media plays is normal use, not a mistake."""

    def test_nothing_loaded_needs_no_note(self):
        from app.ui.main_window import slice_scope_note

        self.assertEqual(slice_scope_note(None, "b.wav"), "")

    def test_the_same_file_on_both_sides_needs_no_note(self):
        from app.ui.main_window import slice_scope_note

        self.assertEqual(slice_scope_note("a.wav", "a.wav"), "")

    def test_no_selection_needs_no_note(self):
        from app.ui.main_window import slice_scope_note

        self.assertEqual(slice_scope_note("a.wav", None), "")

    def test_the_note_names_what_is_on_screen_and_what_will_run(self):
        from app.ui.main_window import slice_scope_note

        note = slice_scope_note("/m/entrevista final.wav", "/m/audio-notas.mp3")
        self.assertIn("entrevista final", note)
        self.assertIn("audio-notas", note)

    def test_the_note_stays_a_note(self):
        # The point of the rewrite: the old line ran on for two clauses and read
        # like a complaint about the user. A bound keeps it that way.
        from app.ui.main_window import slice_scope_note

        note = slice_scope_note("/m/un-medio-muy-largo.wav", "/m/otro.wav")
        self.assertLessEqual(len(note), 90, note)


if __name__ == "__main__":
    unittest.main()