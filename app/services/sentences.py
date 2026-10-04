"""Grouping transcribed words into sentence-sized units.

Whisper emits fixed 30 s windows, so a window boundary lands wherever the clock
happened to be and usually cuts a sentence in half. Measured on the reference
MKV: 45 fragments, 0 of which ended a sentence. That is the mechanism behind the
transcription reading as choppy, and no model or decode option changes it.

The fix is to stop treating the window as the output unit. Word-level timestamps
make the window irrelevant: each word knows when it was spoken, so the boundary
between two sentences can be placed wherever the punctuation says it is.

This module deliberately knows nothing about faster-whisper. It takes words with
times and returns sentences with times, which keeps it unit-testable without a
model download and reusable by any engine that reports word times.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Characters that end a sentence.
SENTENCE_END = ".?!…"

#: Quotes and brackets that legitimately follow the full stop, as in 'said "no."'
#: or "really?)". Without these the last fragment of a quote reads as unfinished.
TRAILING_CLOSERS = "\"'”’)]»"

#: Hard ceiling on one sentence's length, in characters.
#:
#: A safety valve, not a target. The reference MKV's median fragment is 96
#: characters, so a real sentence is well under this and the valve rarely fires.
#: It exists so that a run of speech with no punctuation at all still produces
#: bounded units instead of one unreadable wall.
SENTENCE_MAX_CHARS = 240

#: A pause at least this long between two words is treated as a new utterance.
#:
#: Sits deliberately below the benchmark's own 2.0 s hole threshold: a pause long
#: enough that the metric calls it an omission is certainly long enough that the
#: reader experiences it as a new sentence, so the grouper should break there too.
SENTENCE_MAX_GAP = 1.2


@dataclass(frozen=True)
class Word:
    """One transcribed word on the media timeline."""

    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Sentence:
    """A sentence-sized unit of transcription output."""

    start: float
    end: float
    text: str


def ends_sentence(text: str) -> bool:
    """Whether ``text`` reads as a finished sentence.

    Tolerates trailing whitespace, a closing quote or bracket, and an ellipsis.
    """
    stripped = text.rstrip()
    if not stripped:
        return False
    index = len(stripped) - 1
    while index >= 0 and stripped[index] in TRAILING_CLOSERS:
        index -= 1
        if index < 0:
            return False
    return stripped[index] in SENTENCE_END


def join_words(words: list[Word]) -> str:
    """Render words as one line of text with single spaces."""
    return " ".join(word.text.strip() for word in words if word.text.strip())


def group_into_sentences(
    words: list[Word],
    max_chars: int = SENTENCE_MAX_CHARS,
    max_gap: float = SENTENCE_MAX_GAP,
) -> list[Sentence]:
    """Group timed words into sentences.

    A sentence is closed by the first of three conditions: its text ends a
    sentence, the next word arrives after ``max_gap`` of silence, or it reaches
    ``max_chars``. The gap rule is what makes this work on continuous speech with
    little punctuation, and the length rule is only a bound.

    Empty input yields no sentences. Words with no text are skipped, so a stray
    empty token cannot produce a zero-length unit.
    """
    sentences: list[Sentence] = []
    buffer: list[Word] = []

    def flush() -> None:
        if not buffer:
            return
        text = join_words(buffer)
        if text:
            sentences.append(
                Sentence(
                    start=buffer[0].start,
                    end=buffer[-1].end,
                    text=text,
                )
            )
        buffer.clear()

    length = 0
    previous_end: float | None = None
    for word in words:
        token = word.text.strip()
        if not token:
            continue
        if previous_end is not None and word.start - previous_end > max_gap:
            flush()
            length = 0
        buffer.append(word)
        length += len(token) + 1
        previous_end = word.end
        if ends_sentence(join_words(buffer)) or length >= max_chars:
            flush()
            length = 0
    flush()
    return sentences


def sentence_fragment(segments) -> list[Sentence]:
    """Adapt whisper segments carrying words into sentences.

    Falls back to the segment's own text when the engine reported no words, so a
    run without word timestamps still returns the same units it always did rather
    than nothing.
    """
    collected: list[Word] = []
    fallback: list[Sentence] = []
    for segment in segments:
        words = list(getattr(segment, "words", None) or [])
        if words:
            collected.extend(
                Word(start=float(w.start), end=float(w.end), text=w.word or "")
                for w in words
            )
        elif (segment.text or "").strip():
            fallback.append(
                Sentence(
                    start=float(segment.start),
                    end=float(segment.end),
                    text=segment.text.strip(),
                )
            )
    grouped = group_into_sentences(collected)
    return grouped or fallback