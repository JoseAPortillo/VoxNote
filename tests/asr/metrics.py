"""Structural reliability criteria for a transcription.

Every function here is pure: it takes segments and a waveform and returns numbers.
Nothing loads a model. That is deliberate. A metric that needs an 800 MB download
cannot be re-run cheaply enough to serve as a regression check, and the point of
these five families is to be run after every change.

The five families, and the symptom each one attacks:

- M1 ``phrase_integrity``            the output stops halfway
- M2 ``coverage``                    the transcription is incomplete
- M3 ``silence_and_repetition``      text is invented, or loops
- M4 ``background_recovery``         the quiet voice is lost
- M5 ``cost``                        what it costs on this machine

**What these numbers cannot tell you.** There is no manual reference transcript in
this repo, so nothing here measures whether a word is the right word. A model can
reach perfect coverage with fluent nonsense and pass every check below. These
criteria detect loss, chop and repetition. Accuracy stays a manual judgement.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

#: Analysis frame length in seconds. 20 ms at 16 kHz is 320 samples, short enough
#: to localise a gap without smearing across a word.
FRAME_SECONDS = 0.020

#: A gap shorter than this is between words, not a hole in the transcription.
MIN_GAP_SECONDS = 2.0

#: How far above the measured noise floor a frame has to sit to count as speech.
#: Calibrated against the reference MKV, where 6 dB produced 744 "speech" regions
#: averaging 0.4 s: the music bed was being counted as voice, and the coverage
#: metric was then reporting the detector's noise as if it were dropped text.
SPEECH_MARGIN_DB = 12.0

#: Intra-sentence pauses. Runs of speech closer together than this are one region,
#: because nobody says a sentence without breathing.
REGION_MERGE_SECONDS = 0.40

#: A blip is not a word. Runs shorter than this are dropped.
REGION_MIN_SECONDS = 0.20

#: Fraction of the frames taken as the quietest ones when estimating the noise
#: floor. Music beds are loud enough that a 10th percentile still lands inside
#: them, which is why this is deliberately generous.
NOISE_PERCENTILE = 20.0

#: n-gram size used to detect a runaway repetition loop.
NGRAM = 4

#: Punctuation that closes a sentence, plus the quotes and brackets that can sit
#: after it ("...and then?" / 'said he.').
SENTENCE_END = ".?!…"
TRAILING_CLOSERS = "\"'”’)]»"


@dataclass
class Segment:
    """One unit of transcription output, on the media timeline."""

    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Report:
    """Metric families keyed by name, plus the context needed to read them."""

    families: dict[str, dict] = field(default_factory=dict)

    def as_text(self) -> str:
        lines: list[str] = []
        for name, values in self.families.items():
            lines.append(name)
            for key, value in values.items():
                lines.append(f"    {key:<28} {_fmt(value)}")
            lines.append("")
        return "\n".join(lines).rstrip()


def _fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _median(values: list[float]) -> float:
    return float(np.median(values)) if values else 0.0


# -- signal helpers ---------------------------------------------------------


def frame_dbfs(waveform: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
    """Per-frame level in dBFS.

    Silent frames floor at -120 dBFS instead of -inf so the downstream thresholds
    stay finite and a fully silent stretch produces no speech rather than an error.
    """
    size = max(1, int(round(FRAME_SECONDS * sample_rate)))
    usable = (len(waveform) // size) * size
    if usable == 0:
        return np.zeros(0, dtype=np.float64)
    frames = waveform[:usable].reshape(-1, size).astype(np.float64)
    rms = np.sqrt(np.mean(np.square(frames), axis=1))
    return np.maximum(20.0 * np.log10(np.maximum(rms, 1e-6)), -120.0)


def noise_floor_db(frames_db: np.ndarray) -> float:
    """Level of the quietest ``NOISE_PERCENTILE`` of frames."""
    if frames_db.size == 0:
        return -120.0
    return float(np.percentile(frames_db, NOISE_PERCENTILE))


def speech_mask(
    frames_db: np.ndarray, floor_db: float, margin_db: float = SPEECH_MARGIN_DB
) -> np.ndarray:
    """Boolean per-frame mask of frames carrying speech."""
    return frames_db > (floor_db + margin_db)


def speech_regions(
    mask: np.ndarray,
    merge_seconds: float = REGION_MERGE_SECONDS,
    min_seconds: float = REGION_MIN_SECONDS,
) -> list[tuple[float, float]]:
    """Contiguous runs of speech as ``(start, end)`` seconds.

    Merges runs closer than ``merge_seconds`` and drops runs shorter than
    ``min_seconds``, because an energy threshold on a music bed produces a rash of
    sub-second blips. Left unmerged they inflate the region count, and every
    downstream metric inherits the error.

    The frame length is fixed by ``FRAME_SECONDS``, so there is no sample rate here
    to be wrong about.
    """
    raw: list[tuple[float, float]] = []
    if mask.size == 0:
        return raw
    start: int | None = None
    for index, value in enumerate(mask):
        if value and start is None:
            start = index
        elif not value and start is not None:
            raw.append((start * FRAME_SECONDS, index * FRAME_SECONDS))
            start = None
    if start is not None:
        raw.append((start * FRAME_SECONDS, mask.size * FRAME_SECONDS))

    merged: list[tuple[float, float]] = []
    for region_start, region_end in raw:
        if merged and region_start - merged[-1][1] < merge_seconds:
            merged[-1] = (merged[-1][0], region_end)
        else:
            merged.append((region_start, region_end))
    return [r for r in merged if r[1] - r[0] >= min_seconds]


def band_pass_fft(
    waveform: np.ndarray,
    low_hz: float = 300.0,
    high_hz: float = 3400.0,
    sample_rate: int = 16000,
) -> np.ndarray:
    """Keep only the vocal band, by zeroing everything else in the spectrum.

    An FFT mask is enough here and keeps the harness free of a filter dependency.
    It is applied to a whole decoded file in one shot, where ringing around the
    band edges is irrelevant next to the 10x change in what the band contains.
    """
    if waveform.size == 0:
        return waveform
    spectrum = np.fft.rfft(waveform)
    freqs = np.fft.rfftfreq(waveform.size, 1.0 / sample_rate)
    spectrum[(freqs < low_hz) | (freqs > high_hz)] = 0.0
    return np.fft.irfft(spectrum, n=waveform.size).astype(np.float32)


def _overlap(span: tuple[float, float], regions: list[tuple[float, float]]) -> float:
    """Seconds of ``span`` covered by ``regions``."""
    total = 0.0
    for start, end in regions:
        total += max(0.0, min(span[1], end) - max(span[0], start))
    return total


# -- M1: phrase integrity ---------------------------------------------------


def phrase_integrity(segments: list[Segment]) -> dict:
    """How often the output ends where a sentence ends.

    The output unit used to be a whisper window rather than a sentence, so this
    read 0 of 45 on the reference MKV: every fragment stopped mid-thought. A
    fragment that ends on punctuation is the cheapest available evidence that the
    unit is a unit a reader can use.
    """
    texts = [s.text.strip() for s in segments if s.text.strip()]
    if not texts:
        return {
            "segments": 0,
            "complete_frac": 0.0,
            "complete_count": 0,
            "median_duration_s": 0.0,
            "median_chars": 0.0,
            "chars_total": 0,
        }
    complete = 0
    for text in texts:
        end = text.rstrip(TRAILING_CLOSERS)
        if end and end[-1] in SENTENCE_END:
            complete += 1
    return {
        "segments": len(texts),
        "complete_frac": complete / len(texts),
        "complete_count": complete,
        "median_duration_s": _median([s.duration for s in segments]),
        "median_chars": _median([len(t) for t in texts]),
        "chars_total": sum(len(t) for t in texts),
    }


# -- M2: coverage -----------------------------------------------------------


def coverage(
    segments: list[Segment],
    total_duration: float,
    mask: np.ndarray,
    regions: list[tuple[float, float]],
) -> dict:
    """Where speech exists and no text was emitted.

    ``gaps_with_signal`` is the number that matters. Plain coverage was already
    99.9 % on the reference file and still looked broken to the maintainer, because
    a hole in the middle is invisible in a start-to-end figure. This counts gaps of
    at least ``MIN_GAP_SECONDS`` that overlap speech, which is the shape of a real
    omission.
    """
    ordered = sorted(
        (s for s in segments if s.text.strip()), key=lambda s: s.start
    )
    gaps_with_signal = 0
    speech_in_gaps = 0.0
    if ordered:
        # The head counts. A transcription that never says the first few seconds
        # has lost them exactly as surely as one that skips its middle, and a
        # start-to-end span figure hides it because the span still looks full.
        head = ordered[0].start
        if head >= MIN_GAP_SECONDS and _overlap((0.0, head), regions) > 0.0:
            gaps_with_signal += 1
            speech_in_gaps += _overlap((0.0, head), regions)
        cursor = ordered[0].start
        for segment in ordered:
            if segment.start - cursor >= MIN_GAP_SECONDS:
                if _overlap((cursor, segment.start), regions) > 0.0:
                    gaps_with_signal += 1
                    speech_in_gaps += _overlap((cursor, segment.start), regions)
            cursor = max(cursor, segment.end)
        tail = total_duration - cursor
        if tail >= MIN_GAP_SECONDS and _overlap((cursor, total_duration), regions) > 0.0:
            gaps_with_signal += 1
            speech_in_gaps += _overlap((cursor, total_duration), regions)
        last_end = ordered[-1].end
    else:
        head = 0.0
        last_end = 0.0
        speech_in_gaps = sum(end - start for start, end in regions)

    spoken = sum(end - start for start, end in regions)
    return {
        "first_start_s": head,
        "last_end_s": last_end,
        "tail_lost_s": max(0.0, total_duration - last_end),
        "span_frac": (last_end - head) / total_duration if total_duration else 0.0,
        "speech_total_s": spoken,
        "speech_covered_frac": (
            sum(_overlap((s.start, s.end), regions) for s in ordered) / spoken
            if spoken
            else 0.0
        ),
        "gaps_with_signal": gaps_with_signal,
        "speech_in_gaps_s": speech_in_gaps,
    }


# -- M3: silence and repetition ---------------------------------------------


def silence_and_repetition(
    segments: list[Segment],
    regions: list[tuple[float, float]],
) -> dict:
    """Text emitted where there is no speech, and repeated text.

    ``chars_in_silence`` is charged in proportion to how much of a segment's span
    is actually silent, so a segment that straddles the edge of real speech is not
    counted as wholly invented. It is the number that separates engines: whisper's
    decoder emits one token at a time and has no way to say nothing, so under a
    music bed it tends to produce text. A model that decides per step, or that is
    not autoregressive at all, is not obliged to.

    ``segments_fully_in_silence`` is reported next to the aggregate on purpose.
    Prose that straddles a breath is normal and a large percentage alone would be
    misread as hallucination; a whole segment sitting on silence is the thing worth
    knowing about. They must not be summarised into one number.

    ``repeat_ngram_ratio`` catches the runaway loop across windows, which is the
    failure ``condition_on_previous_text=True`` invites.
    """
    charged = 0.0
    total_chars = 0
    fully_silent = 0
    partly_silent = 0
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        chars = len(text)
        total_chars += chars
        if segment.duration <= 0.0:
            charged += chars
            fully_silent += 1
            continue
        spoken = _overlap((segment.start, segment.end), regions)
        fraction_silent = max(0.0, 1.0 - spoken / segment.duration)
        charged += chars * fraction_silent
        # The split matters. Prose that straddles a breath is not hallucination;
        # a whole segment sitting on silence is. Without this, one aggregate
        # number cannot tell those two apart.
        if fraction_silent >= 0.95:
            fully_silent += 1
        elif fraction_silent > 0.0:
            partly_silent += 1

    words = " ".join(s.text for s in segments).split()
    grams = [tuple(words[i : i + NGRAM]) for i in range(len(words) - NGRAM + 1)]
    unique = len(set(grams))
    ratio = (1.0 - unique / len(grams)) if grams else 0.0

    return {
        "chars_total": total_chars,
        "chars_in_silence": charged,
        "chars_in_silence_frac": charged / total_chars if total_chars else 0.0,
        "segments_fully_in_silence": fully_silent,
        "segments_partly_in_silence": partly_silent,
        "repeat_ngram_ratio": ratio,
        "repeat_ngram_size": NGRAM,
    }


# -- M4: background recovery -----------------------------------------------


def background_recovery(
    segments: list[Segment],
    vocal_band_segments: list[Segment],
) -> dict:
    """Second instrument for the lost background voice.

    The first instrument is speaker diarization, which lives in the runner because
    it needs the diarizer. This one needs only the transcript: transcribe the same
    audio twice, once whole-band and once restricted to 300-3400 Hz, and compare.

    Positive ``delta_chars`` means the narrow band recovered text the wide band did
    not, which is the signature of speech being masked rather than absent. Two
    instruments that agree are worth more than one that does: if the lost voice
    shows up in only one of them, the defect is in the instrument.
    """
    whole = sum(len(s.text.strip()) for s in segments)
    band = sum(len(s.text.strip()) for s in vocal_band_segments)
    return {
        "chars_whole_band": whole,
        "chars_vocal_band": band,
        "delta_chars": band - whole,
        "delta_frac": (band - whole) / whole if whole else 0.0,
    }


# -- M5: cost ---------------------------------------------------------------


def cost(wall_seconds: float, audio_seconds: float, vram_mb: float = 0.0) -> dict:
    """Throughput on this machine, and peak GPU footprint when the engine has one.

    ``peak_torch_vram_mb`` is named for what it measures. faster-whisper runs on
    CTranslate2 and never touches the torch allocator, so under that engine the
    value is genuinely 0.0 rather than "unknown": the torch engine is not in the
    process. It only becomes meaningful for a torch-based engine.
    """
    return {
        "wall_s": wall_seconds,
        "audio_s": audio_seconds,
        "speed_x": audio_seconds / wall_seconds if wall_seconds else 0.0,
        "peak_torch_vram_mb": vram_mb,
    }


# -- report assembly --------------------------------------------------------


def analyse(
    segments: list[Segment],
    waveform: np.ndarray,
    total_duration: float,
    sample_rate: int = 16000,
) -> Report:
    """Run M1, M2, M3 and the waveform-derived half of M4 over one transcription."""
    frames = frame_dbfs(waveform, sample_rate)
    floor = noise_floor_db(frames)
    mask = speech_mask(frames, floor)
    regions = speech_regions(mask)
    return Report(
        families={
            "signal": {
                "duration_s": total_duration,
                "frames": frames.size,
                "noise_floor_dbfs": floor,
                "speech_margin_db": SPEECH_MARGIN_DB,
                "speech_frac": float(mask.mean()) if mask.size else 0.0,
                # Published so the detector can be checked rather than trusted.
                # The reference file first reported 744 regions averaging 0.4 s,
                # which is a music bed misread as a stutter.
                "speech_regions": len(regions),
                "speech_region_mean_s": (
                    sum(e - s for s, e in regions) / len(regions) if regions else 0.0
                ),
            },
            "M1_phrase_integrity": phrase_integrity(segments),
            "M2_coverage": coverage(segments, total_duration, mask, regions),
            "M3_silence_and_repetition": silence_and_repetition(segments, regions),
        }
    )
