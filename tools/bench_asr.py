"""Run one transcription and score it against the five structural criteria.

The benchmark deliberately does NOT go through ``Transcriber``. The production
path hardcodes its decode options, and the point of this tool is to vary them and
to compare engines on equal terms. What the app does today is expressed here as the
explicit default option set below, so the baseline is a statement about the app and
not a statement about library defaults.

Usage::

    python tools/bench_asr.py MEDIA
    python tools/bench_asr.py MEDIA --label baseline
    python tools/bench_asr.py MEDIA --set word_timestamps=true --set condition_on_previous_text=false
    python tools/bench_asr.py MEDIA --model large-v3 --band --json out.json

``--set key=value`` maps onto ``WhisperModel.transcribe`` keyword arguments and is
the only way to change decode behaviour, so every result carries the exact options
that produced it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

# Windows consoles still default to a legacy code page, and this report is meant to
# be read in one. Force UTF-8 rather than let a metric name abort the run.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import (  # noqa: E402
    WHISPER_BEAM_SIZE,
    WHISPER_COMPUTE_TYPE,
    WHISPER_DEVICE,
    WHISPER_MODEL,
)
from app.services.clipper import (  # noqa: E402
    RANGE_WARMUP,
    SAMPLE_RATE,
    decode_range,
    duration,
)
from app.services.sentences import (  # noqa: E402
    SENTENCE_MAX_CHARS,
    SENTENCE_MAX_GAP,
    Word,
    group_into_sentences,
)
from tests.asr.metrics import (  # noqa: E402
    Report,
    Segment,
    analyse,
    background_recovery,
    band_pass_fft,
    cost,
)

#: Exactly what ``Transcriber.transcribe_segments`` passes today. Everything else
#: is a faster-whisper default and is deliberately left off this dict, because
#: leaving it off is what the app does.
APP_OPTIONS: dict = {"beam_size": WHISPER_BEAM_SIZE, "vad_filter": True}

#: Values accepted in ``--set`` written as Python literals.
_LITERALS = {"true": True, "false": False, "none": None}


def _coerce(raw: str):
    lowered = raw.lower()
    if lowered in _LITERALS:
        return _LITERALS[lowered]
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            pass
    if "," in raw:
        return [_coerce(part) for part in raw.split(",")]
    return raw


def build_options(pairs: list[str]) -> dict:
    """Parse ``key=value`` overrides onto the app's option set."""
    options = dict(APP_OPTIONS)
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--set expects key=value, got {pair!r}")
        key, _, raw = pair.partition("=")
        options[key.strip()] = _coerce(raw.strip())
    return options


def run(model, waveform: np.ndarray, options: dict) -> tuple[list[Segment], float]:
    """Transcribe one waveform and return segments plus wall seconds."""
    started = time.perf_counter()
    raw, _info = model.transcribe(waveform, **options)
    segments = [
        Segment(start=float(s.start), end=float(s.end), text=s.text or "")
        for s in raw
    ]
    return segments, time.perf_counter() - started


def as_sentences(
    model, waveform: np.ndarray, options: dict, gap: float, chars: int
) -> tuple[list[Segment], list[Segment], float]:
    """Transcribe once, then regroup the words into sentence-sized units.

    Word timestamps are forced on because there is no way to place a sentence
    boundary in time without them. Returns both the window units and the sentence
    units from a single transcription, so a sweep compares like with like instead
    of against a stored number from another run.
    """
    started = time.perf_counter()
    # Materialise the generator. faster-whisper yields lazily, so reading it twice
    # would silently give an empty second pass and a result of zero units.
    raw = list(model.transcribe(waveform, **dict(options, word_timestamps=True))[0])
    elapsed = time.perf_counter() - started
    windows = [
        Segment(start=float(s.start), end=float(s.end), text=s.text or "")
        for s in raw
    ]
    words = [
        Word(start=float(w.start), end=float(w.end), text=w.word or "")
        for s in raw
        for w in (list(getattr(s, "words", None) or []))
    ]
    sentences = [
        Segment(start=s.start, end=s.end, text=s.text)
        for s in group_into_sentences(words, max_chars=chars, max_gap=gap)
    ]
    return windows, sentences, elapsed


def chunk_ranges(total: float, chunk: float) -> list[tuple[float, float]]:
    """Split ``[0, total]`` into contiguous ``chunk``-second ranges.

    Contiguous rather than speech-region ranges, which is the whole point: the
    punctuation fix needs a *fresh* decoder prior at a regular cadence, and speech
    regions would give an irregular one.
    """
    if chunk <= 0:
        raise SystemExit(f"--chunks expects a positive number, got {chunk!r}")
    ranges: list[tuple[float, float]] = []
    start = 0.0
    while start < total:
        end = min(start + chunk, total)
        ranges.append((start, end))
        start = end
    return ranges


def as_chunked_sentences(
    model, media: str, total: float, options: dict, chunk: float, warmup: float,
    gap: float, chars: int,
) -> tuple[list[Segment], dict]:
    """Decode chunk by chunk, re-prompting each time, then group into sentences.

    faster-whisper applies ``initial_prompt`` once per ``transcribe()`` call, and
    ``condition_on_previous_text=True`` makes each internal window inherit the
    previous window's output. Over a long file that reinforcement collapses
    punctuation to nothing. Re-prompting per chunk resets the prior, which is the
    only lever found to sustain punctuation across a whole file.

    Each chunk is decoded with a warmup margin and then filtered by word midpoint,
    so a word belongs to exactly one chunk and cannot be duplicated where two
    overlapping decodes both saw it. The margin pattern mirrors
    ``Transcriber.transcribe_segments``; the midpoint filter replaces that
    clamping because these ranges are contiguous, not disjoint.
    """
    from app.services.clipper import decode_range

    started = time.perf_counter()
    words: list[Word] = []
    seen = 0
    per_chunk: list[dict] = []

    for low, high in chunk_ranges(total, chunk):
        pad_low = max(0.0, low - warmup)
        pad_high = min(total, high + warmup)
        audio = decode_range(media, pad_low, pad_high)
        if audio.size == 0:
            per_chunk.append({"low": low, "high": high, "words": 0, "dropped": 0})
            continue
        # Materialise: faster-whisper yields lazily, and this generator is drained
        # once here on purpose.
        raw = list(model.transcribe(audio, **dict(options, word_timestamps=True))[0])
        kept = dropped = 0
        for s in raw:
            for w in list(getattr(s, "words", None) or []):
                seen += 1
                start_abs = float(w.start) + pad_low
                end_abs = float(w.end) + pad_low
                if (start_abs + end_abs) / 2.0 < low or (start_abs + end_abs) / 2.0 >= high:
                    dropped += 1
                    continue
                words.append(Word(start=start_abs, end=end_abs, text=w.word or ""))
                kept += 1
        per_chunk.append({"low": low, "high": high, "words": kept, "dropped": dropped})

    words.sort(key=lambda w: w.start)
    sentences = [
        Segment(start=s.start, end=s.end, text=s.text)
        for s in group_into_sentences(words, max_chars=chars, max_gap=gap)
    ]
    stats = {
        "chunk_s": chunk,
        "warmup_s": warmup,
        "chunks": len(per_chunk),
        "words_seen": seen,
        "words_kept": len(words),
        "words_dropped_in_overlap": seen - len(words),
        "wall_s": round(time.perf_counter() - started, 2),
        "per_chunk": per_chunk,
    }
    return sentences, stats


def peak_vram_mb() -> float:
    """Peak CUDA allocation for this process, or 0.0 when running on CPU."""
    try:
        import torch

        if torch.cuda.is_available():
            return torch.cuda.max_memory_allocated() / (1024 * 1024)
    except Exception:  # pragma: no cover - diagnostics must never fail the run
        pass
    return 0.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("media", help="audio or video file to transcribe")
    parser.add_argument("--model", default=WHISPER_MODEL, help="whisper model name")
    parser.add_argument("--label", default="", help="name for this configuration")
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="override a transcribe() keyword argument; repeatable",
    )
    parser.add_argument(
        "--band",
        action="store_true",
        help="also transcribe the 300-3400 Hz vocal band (M4 second instrument)",
    )
    parser.add_argument(
        "--sentences",
        action="store_true",
        help="group words into sentence-sized units (F3 candidate)",
    )
    parser.add_argument(
        "--gap",
        type=float,
        default=SENTENCE_MAX_GAP,
        help=f"pause that closes a sentence, seconds (default {SENTENCE_MAX_GAP})",
    )
    parser.add_argument(
        "--chars",
        type=int,
        default=SENTENCE_MAX_CHARS,
        help=f"length valve for a sentence (default {SENTENCE_MAX_CHARS})",
    )
    parser.add_argument(
        "--chunks",
        type=float,
        metavar="SECONDS",
        help="decode in contiguous chunks, re-applying --set per chunk (F6 candidate)",
    )
    parser.add_argument(
        "--warmup",
        type=float,
        default=RANGE_WARMUP,
        help=f"margin decoded on each side of a chunk and filtered back (default {RANGE_WARMUP})",
    )
    parser.add_argument("--json", metavar="PATH", help="write the report as JSON")
    args = parser.parse_args(argv)

    media = Path(args.media)
    if not media.is_file():
        raise SystemExit(f"no such file: {media}")

    options = build_options(args.overrides)
    total = duration(str(media))
    waveform = decode_range(str(media), 0.0, total)
    if waveform.size == 0:
        raise SystemExit(f"decoded no audio from {media}")

    from faster_whisper import WhisperModel

    label = args.label or args.model
    print(f"== {label} ==")
    print(f"media      {media.name}")
    print(f"duration   {total:.2f} s")
    print(f"model      {args.model}  device={WHISPER_DEVICE} compute={WHISPER_COMPUTE_TYPE}")
    print(f"options    {options}")
    print()

    model = WhisperModel(
        args.model, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE_TYPE
    )

    if args.chunks:
        # Chunks re-prompt, so they must always be grouped: the point of the run is
        # to see where the chunk seams land, and word timestamps are what make a
        # seam visible. Forcing them here keeps that from being an easy mistake.
        sentences, chunk_stats = as_chunked_sentences(
            model, str(media), total, options, args.chunks, args.warmup,
            args.gap, args.chars,
        )
        report = analyse(sentences, waveform, total, SAMPLE_RATE)
        report.families["M5_cost"] = cost(
            chunk_stats["wall_s"], total, peak_vram_mb()
        )
        report.families["chunking"] = chunk_stats
        report.families["context"] = {
            "label": label,
            "model": args.model,
            "media": media.name,
            "options": {k: str(v) for k, v in options.items()},
            "units": "sentences from chunked re-prompted decode",
        }
    elif args.sentences:
        # One transcription, scored twice: the window units whisper produced and the
        # sentence units built from the same words. Comparing across runs would
        # confound the grouping with decoder nondeterminism.
        windows, sentences, wall = as_sentences(
            model, waveform, options, args.gap, args.chars
        )
        report = analyse(sentences, waveform, total, SAMPLE_RATE)
        report.families["M5_cost"] = cost(wall, total, peak_vram_mb())
        report.families["grouping"] = {
            "max_gap_s": args.gap,
            "max_chars": args.chars,
            "window_units": len(windows),
            "sentence_units": len(sentences),
        }
        baseline = analyse(windows, waveform, total, SAMPLE_RATE)
        report.families["window_baseline"] = {
            "complete_frac": baseline.families["M1_phrase_integrity"]["complete_frac"],
            "speech_covered_frac": baseline.families["M2_coverage"]["speech_covered_frac"],
            "gaps_with_signal": baseline.families["M2_coverage"]["gaps_with_signal"],
            "chars_total": baseline.families["M1_phrase_integrity"]["chars_total"],
        }
        report.families["context"] = {
            "label": label,
            "model": args.model,
            "media": media.name,
            "options": {k: str(v) for k, v in options.items()},
            "units": "sentences",
        }
    else:
        segments, wall = run(model, waveform, options)
        report = analyse(segments, waveform, total, SAMPLE_RATE)
        report.families["M5_cost"] = cost(wall, total, peak_vram_mb())
        report.families["context"] = {
            "label": label,
            "model": args.model,
            "media": media.name,
            "options": {k: str(v) for k, v in options.items()},
        }

    if args.band:
        narrow = band_pass_fft(waveform, sample_rate=SAMPLE_RATE)
        band_segments, _band_wall = run(model, narrow, options)
        report.families["M4_background_recovery"] = background_recovery(
            segments if not args.sentences else windows, band_segments
        )

    print(report.as_text())

    if args.json:
        payload = {
            "label": label,
            "model": args.model,
            "media": media.name,
            "duration_s": total,
            "options": {k: str(v) for k, v in options.items()},
            "families": report.families,
        }
        Path(args.json).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
