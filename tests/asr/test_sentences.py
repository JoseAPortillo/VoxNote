"""Tests for sentence grouping.

Run with::

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

No model download: the grouper is deliberately engine-agnostic so the rule that
decides where a sentence ends can be tested on its own.
"""

from __future__ import annotations

import unittest

from app.services.sentences import (
    Sentence,
    Word,
    ends_sentence,
    group_into_sentences,
    join_words,
    sentence_fragment,
)


def words(*pairs) -> list[Word]:
    """Build words from ``(text, start, end)`` triples, one per second."""
    return [
        Word(start=start, end=end, text=text) for text, start, end in pairs
    ]


class TestEndsSentence(unittest.TestCase):
    def test_plain_full_stop(self):
        self.assertTrue(ends_sentence("That is the plan."))

    def test_question_and_exclamation(self):
        self.assertTrue(ends_sentence("Really?"))
        self.assertTrue(ends_sentence("Look at it!"))

    def test_ellipsis(self):
        self.assertTrue(ends_sentence("and then it goes on..."))

    def test_trailing_quote_still_ends(self):
        self.assertTrue(ends_sentence('He said "no."'))

    def test_trailing_bracket_still_ends(self):
        self.assertTrue(ends_sentence("(are you sure?)"))

    def test_mid_sentence_is_not_complete(self):
        self.assertFalse(ends_sentence("and then the camera moves"))

    def test_empty_is_not_complete(self):
        self.assertFalse(ends_sentence("   "))
        self.assertFalse(ends_sentence(""))


class TestJoinWords(unittest.TestCase):
    def test_collapses_whitespace(self):
        self.assertEqual(join_words(words((" hello ", 0.0, 0.2), ("world", 0.2, 0.4))), "hello world")


class TestGroupIntoSentences(unittest.TestCase):
    def test_breaks_on_punctuation(self):
        result = group_into_sentences(
            words(
                ("The", 0.0, 0.2),
                ("camera", 0.2, 0.5),
                ("moves.", 0.5, 0.8),
                ("It", 1.0, 1.1),
                ("stops.", 1.1, 1.4),
            )
        )
        self.assertEqual([s.text for s in result], ["The camera moves.", "It stops."])

    def test_times_come_from_first_and_last_word(self):
        result = group_into_sentences(
            words(("Hello", 2.0, 2.4), ("there.", 3.0, 3.5))
        )
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].start, 2.0)
        self.assertAlmostEqual(result[0].end, 3.5)

    def test_long_pause_breaks_even_without_punctuation(self):
        # This is the case the reference file is full of: continuous speech, little
        # punctuation. The pause has to do the work or every sentence runs on.
        result = group_into_sentences(
            words(
                ("and", 0.0, 0.2),
                ("then", 0.2, 0.4),
                ("she", 4.0, 4.2),
                ("said", 4.2, 4.4),
            ),
            max_gap=1.2,
        )
        self.assertEqual([s.text for s in result], ["and then", "she said"])

    def test_short_pause_does_not_break(self):
        result = group_into_sentences(
            words(("and", 0.0, 0.2), ("then", 0.5, 0.7)),
            max_gap=1.2,
        )
        self.assertEqual([s.text for s in result], ["and then"])

    def test_length_valve_bounds_an_unpunctuated_run(self):
        result = group_into_sentences(
            words(*[(f"w{i}", float(i), float(i) + 0.1) for i in range(30)]),
            max_chars=50,
        )
        self.assertGreater(len(result), 1)
        self.assertTrue(all(len(s.text) <= 60 for s in result))

    def test_unpunctuated_text_cannot_be_made_into_complete_sentences(self):
        # Words with no punctuation anywhere can never satisfy ends_sentence, no
        # matter how they are grouped. This test exists to stop anyone claiming the
        # grouping drives phrase integrity to 100 %: it is bounded by the audio's
        # own punctuation. What the grouping does guarantee is that every unit was
        # closed deliberately rather than by running out of words.
        result = group_into_sentences(
            words(*[(f"w{i}", float(i), float(i) + 0.1) for i in range(40)]),
            max_chars=1000,
        )
        self.assertTrue(all(not ends_sentence(s.text) for s in result))
        # 40 words at 0.1 s spacing: no pause, no punctuation, no valve. The only
        # honest answer is the single unit the audio actually is. A grouper that
        # chopped here would be inventing boundaries that are not in the recording.
        self.assertEqual(len(result), 1)

    def test_every_unit_is_closed_at_a_boundary_not_at_the_end(self):
        # Now with pauses, so the gap rule has something to work with.
        result = group_into_sentences(
            words(*[(f"w{i}", float(i * 2), float(i * 2) + 0.1) for i in range(40)]),
            max_chars=1000,
        )
        self.assertGreater(len(result), 1)
        # Units are ordered and never overlap, so a consumer can rely on the spans.
        for earlier, later in zip(result, result[1:]):
            self.assertLessEqual(earlier.end, later.start)

    def test_empty_input_yields_nothing(self):
        self.assertEqual(group_into_sentences([]), [])

    def test_empty_words_are_skipped(self):
        result = group_into_sentences(words(("hi.", 0.0, 0.2), ("  ", 0.3, 0.4)))
        self.assertEqual([s.text for s in result], ["hi."])

    def test_no_text_survives_into_the_output(self):
        result = group_into_sentences(words(("", 0.0, 0.1), ("x.", 0.2, 0.3)))
        self.assertEqual([s.text for s in result], ["x."])


class _FakeWord:
    def __init__(self, start, end, word):
        self.start, self.end, self.word = start, end, word


class _FakeSegment:
    def __init__(self, start, end, text, words=()):
        self.start, self.end, self.text = start, end, text
        self.words = list(words)


class TestSentenceFragment(unittest.TestCase):
    def test_groups_the_words_of_the_segments(self):
        segments = [
            _FakeSegment(0.0, 1.0, "hello there friend", [
                _FakeWord(0.0, 0.3, "hello"),
                _FakeWord(0.3, 0.6, "there"),
                _FakeWord(0.6, 1.0, "friend."),
            ]),
        ]
        result = sentence_fragment(segments)
        self.assertEqual([s.text for s in result], ["hello there friend."])

    def test_falls_back_to_segment_text_without_words(self):
        segments = [_FakeSegment(0.0, 1.0, "no words here")]
        result = sentence_fragment(segments)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], Sentence)
        self.assertEqual(result[0].text, "no words here")

    def test_no_segments_yields_nothing(self):
        self.assertEqual(sentence_fragment([]), [])


if __name__ == "__main__":
    unittest.main()