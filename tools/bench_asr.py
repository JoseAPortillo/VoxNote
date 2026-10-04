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
from app.services.clipper import SAMPLE_RATE, decode_range, duration  # noqa: E402
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
    segments, wall = run(model, waveform, options)
    report = analyse(segments, waveform, total, SAMPLE_RATE)
    report.families["M5_cost"] = cost(wall, total, peak_vram_mb())

    if args.band:
        narrow = band_pass_fft(waveform, sample_rate=SAMPLE_RATE)
        band_segments, _band_wall = run(model, narrow, options)
        report.families["M4_background_recovery"] = background_recovery(
            segments, band_segments
        )

    report.families["context"] = {
        "label": label,
        "model": args.model,
        "media": media.name,
        "options": {k: str(v) for k, v in options.items()},
    }
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
