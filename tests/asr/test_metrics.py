"""Tests for the structural criteria in ``tests.asr.metrics``.

Run with::

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

These use synthetic waveforms, so they are fast and need no model download. That is
the point: a regression guard you cannot run in a second is a guard nobody runs.
The assertions are deliberately loose about exact boundaries, because frame
quantisation makes exact edges an implementation detail rather than a contract.
"""

from __future__ import annotations

import unittest

import numpy as np

from tests.asr.metrics import (
    Segment,
    background_recovery,
    band_pass_fft,
    coverage,
    frame_dbfs,
    noise_floor_db,
    phrase_integrity,
    silence_and_repetition,
    speech_mask,
    speech_regions,
)

SR = 16000


def tone(seconds: float, hz: float = 1000.0, level: float = 0.3) -> np.ndarray:
    """A sine burst standing in for speech energy."""
    t = np.arange(int(seconds * SR)) / SR
    return (level * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.float32)


def loud_gap_file() -> np.ndarray:
    """10 s: loud 2-4 s, quiet gap 4-6 s, loud 6-8 s, silence at both ends."""
    return np.concatenate(
        [silence(2.0), tone(2.0), silence(2.0), tone(2.0), silence(2.0)]
    )


class TestPhraseIntegrity(unittest.TestCase):
    def test_counts_fragments_ending_a_sentence(self):
        segments = [
            Segment(0.0, 1.0, "There it gets a little soft."),
            Segment(1.0, 2.0, "and then the camera moves"),
            Segment(2.0, 3.0, "Really?"),
            Segment(3.0, 4.0, "yes."),
        ]
        result = phrase_integrity(segments)
        self.assertEqual(result["segments"], 4)
        self.assertEqual(result["complete_count"], 3)
        self.assertAlmostEqual(result["complete_frac"], 0.75)

    def test_trailing_quote_still_counts_as_complete(self):
        result = phrase_integrity([Segment(0.0, 1.0, 'He said "no."')])
        self.assertEqual(result["complete_count"], 1)

    def test_ellipsis_counts(self):
        result = phrase_integrity([Segment(0.0, 1.0, "it goes on...")])
        self.assertEqual(result["complete_count"], 1)

    def test_empty_segments_are_not_counted(self):
        result = phrase_integrity([Segment(0.0, 1.0, "   "), Segment(1.0, 2.0, "ok.")])
        self.assertEqual(result["segments"], 1)

    def test_no_segments_is_zero_not_an_error(self):
        result = phrase_integrity([])
        self.assertEqual(result["complete_frac"], 0.0)
        self.assertEqual(result["median_chars"], 0.0)


class TestCoverage(unittest.TestCase):
    def setUp(self):
        self.wave = loud_gap_file()
        frames = frame_dbfs(self.wave, SR)
        self.mask = speech_mask(frames, noise_floor_db(frames))
        self.regions = speech_regions(self.mask)

    def test_finds_the_loud_spans(self):
        self.assertGreaterEqual(len(self.regions), 2)
        self.assertAlmostEqual(self.regions[0][0], 2.0, delta=0.1)
        self.assertAlmostEqual(self.regions[-1][1], 8.0, delta=0.1)

    def test_gap_between_segments_counts_when_speech_is_there(self):
        # Text present 2-4 and 6-8. The 4-6 gap is silent, so it must NOT count.
        segments = [Segment(2.0, 4.0, "hello."), Segment(6.0, 8.0, "again.")]
        result = coverage(segments, 10.0, self.regions)
        self.assertEqual(result["gaps_with_signal"], 0)
        self.assertAlmostEqual(result["tail_lost_s"], 2.0, delta=0.1)

    def test_gap_over_speech_counts_as_a_real_omission(self):
        # Same file, but text only covers 2-4. The 6-8 speech span is now unsaid.
        # The tail gap is 6 s wide but only 2 s of it carries signal, and only
        # that 2 s may be charged: the metric counts lost speech, not lost time.
        segments = [Segment(2.0, 4.0, "hello.")]
        result = coverage(segments, 10.0, self.regions)
        self.assertEqual(result["gaps_with_signal"], 1)
        self.assertGreater(result["speech_in_gaps_s"], 1.5)
        self.assertLess(result["speech_in_gaps_s"], 2.5)

    def test_head_omission_counts_like_any_other(self):
        # Text starts at 3 s, so the 2-3 speech span is lost. A start-to-end span
        # figure cannot see this; gaps_with_signal has to.
        segments = [Segment(3.0, 7.0, "middle only.")]
        result = coverage(segments, 10.0, self.regions)
        self.assertEqual(result["gaps_with_signal"], 2)
        self.assertAlmostEqual(result["first_start_s"], 3.0)

    def test_a_segment_straddling_silence_covers_only_half_the_speech(self):
        # One segment from 3 to 7 spans the silent 4-6 gap without emitting
        # anything for it, so it reaches across the whole file yet says half of
        # what was said. This is the shape plain coverage cannot distinguish.
        segments = [Segment(3.0, 7.0, "middle only.")]
        result = coverage(segments, 10.0, self.regions)
        self.assertAlmostEqual(result["speech_covered_frac"], 0.5, delta=0.05)
        self.assertGreater(result["span_frac"], 0.35)


class TestSilenceAndRepetition(unittest.TestCase):
    def test_charges_a_segment_that_sits_in_silence(self):
        result = silence_and_repetition([Segment(4.2, 5.8, "x" * 100)], [])
        self.assertAlmostEqual(result["chars_in_silence"], 100.0, delta=1.0)
        self.assertAlmostEqual(result["chars_in_silence_frac"], 1.0)

    def test_segment_over_real_speech_is_not_charged(self):
        result = silence_and_repetition(
            [Segment(2.0, 4.0, "x" * 100)], [(2.0, 4.0)]
        )
        self.assertAlmostEqual(result["chars_in_silence"], 0.0, delta=1.0)

    def test_partly_silent_segment_is_charged_proportionally(self):
        result = silence_and_repetition(
            [Segment(3.0, 5.0, "x" * 100)], [(2.0, 4.0)]
        )
        self.assertAlmostEqual(result["chars_in_silence"], 50.0, delta=2.0)

    def test_fully_and_partly_silent_segments_are_counted_apart(self):
        # A segment on top of a breath is normal; a whole segment on silence is
        # the thing worth knowing about. They must not share one bucket.
        segments = [
            Segment(4.2, 5.8, "a" * 100),   # entirely silent
            Segment(3.0, 5.0, "b" * 100),   # half silent
            Segment(2.0, 4.0, "c" * 100),   # entirely speech
        ]
        result = silence_and_repetition(segments, [(2.0, 4.0)])
        self.assertEqual(result["segments_fully_in_silence"], 1)
        self.assertEqual(result["segments_partly_in_silence"], 1)

    def test_detects_a_repeat_loop(self):
        looped = " ".join(["and then she said no"] * 12)
        result = silence_and_repetition([Segment(0.0, 10.0, looped)], [(0.0, 10.0)])
        self.assertGreater(result["repeat_ngram_ratio"], 0.4)

    def test_normal_prose_does_not_look_repetitive(self):
        prose = (
            "The camera moves slowly across the room and finds the child asleep "
            "under a blanket near the window."
        )
        result = silence_and_repetition([Segment(0.0, 8.0, prose)], [(0.0, 8.0)])
        self.assertLess(result["repeat_ngram_ratio"], 0.1)


class TestBandPass(unittest.TestCase):
    def test_keeps_a_vocal_tone_and_drops_a_high_one(self):
        vocal = band_pass_fft(tone(1.0, 1000.0), sample_rate=SR)
        hiss = band_pass_fft(tone(1.0, 6000.0), sample_rate=SR)
        self.assertGreater(np.abs(vocal).max(), 0.2)
        self.assertLess(np.abs(hiss).max(), 0.02)

    def test_empty_input_is_returned_untouched(self):
        empty = np.zeros(0, dtype=np.float32)
        self.assertEqual(band_pass_fft(empty).size, 0)


class TestBackgroundRecovery(unittest.TestCase):
    def test_positive_delta_means_the_band_recovered_text(self):
        whole = [Segment(0.0, 1.0, "loud part only.")]
        band = whole + [Segment(1.0, 2.0, "y a lo mejor algo de fondo aqui.")]
        result = background_recovery(whole, band)
        self.assertGreater(result["delta_chars"], 0)
        self.assertGreater(result["delta_frac"], 0.0)

    def test_negative_delta_is_reported_not_hidden(self):
        whole = [Segment(0.0, 1.0, "x" * 200)]
        band = [Segment(0.0, 1.0, "x" * 10)]
        result = background_recovery(whole, band)
        self.assertLess(result["delta_chars"], 0)


if __name__ == "__main__":
    unittest.main()
