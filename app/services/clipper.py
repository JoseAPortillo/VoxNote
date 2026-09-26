"""Exact time-range audio decoding with PyAV.

``faster-whisper`` takes a raw ``float32`` mono 16 kHz waveform, so restricting
a run to a marked time range is a decode problem and not a ``faster-whisper``
option: ``clip_timestamps`` only applies inside a single 30 s ``chunk_length``
window and is ignored when ``vad_filter`` is on. This module decodes
``[start, end)`` of a media file into exactly that waveform.
"""

from __future__ import annotations

import av
import numpy as np

#: Sample rate ``faster-whisper`` expects for a raw waveform.
SAMPLE_RATE = 16000

#: Full scale of s16 samples. Skipping this division leaves peaks around 16629
#: and whisper returns nothing at all, with a perfectly correct duration.
S16_FULL_SCALE = 32768.0

#: Seconds to rewind before the requested start.
#:
#: Seeking lands on a keyframe and ``backward=True`` is NOT a guarantee of
#: landing before the target: over a 1139 point grid on the reference MKV, 94
#: points land up to 22 ms AFTER it, and a range decoded from there comes out
#: short and late. A file with a video track lands forward at 594 of 594 points.
#: Backing off 0.25 s removes the case (0 of 1139) at the cost of decoding at
#: most 0.25 s of audio that is then discarded.
SEEK_GUARD = 0.25


class ClipError(RuntimeError):
    """A time range could not be decoded with sample accuracy."""


def duration(path: str) -> float:
    """Total media duration in seconds, or 0.0 when the container reports none."""
    with av.open(path) as container:
        if container.duration is None:
            return 0.0
        return container.duration / av.time_base


def decode_range(path: str, start: float, end: float) -> np.ndarray:
    """Decode ``[start, end)`` seconds of ``path`` as a mono 16 kHz waveform.

    Returns ``float32`` samples scaled to ``[-1.0, 1.0]``: the exact input
    ``WhisperModel.transcribe`` expects for an in-memory waveform. The length
    matches the requested range to within one sample, so the caller can add
    ``start`` to the array-relative timestamps whisper returns and land back on
    the media timeline. A range reaching past the end of the media returns
    fewer samples instead of padding them.

    Raises:
        ClipError: the file has no audio track, or the decoder could not start
            early enough to cover the head of the range.
    """
    start = max(0.0, float(start))
    end = max(start, float(end))
    wanted = int(round((end - start) * SAMPLE_RATE))
    if wanted == 0:
        return np.zeros(0, dtype=np.float32)

    with av.open(path) as container:
        if not container.streams.audio:
            raise ClipError(
                "El archivo no tiene ninguna pista de audio, así que no se "
                "puede transcribir el tramo seleccionado."
            )
        # The audio track is selected by OBJECT on purpose. The integer passed
        # to container.decode() is an ordinal within the per-type stream tuple,
        # so ``audio=0`` does pick audio, yet in a multi-track file it never
        # fails and silently takes track 0.
        stream = container.streams.audio[0]
        seek_to = max(0.0, start - SEEK_GUARD)
        # NOTE: the offset is expressed in stream.time_base units whenever a
        # stream is given, NOT in av.time_base. Handing over seconds here
        # seeks to roughly zero.
        container.seek(
            int(seek_to / float(stream.time_base)),
            backward=True,
            any_frame=False,
            stream=stream,
        )
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        trimmer = _Trimmer(
            target=int(round(start * SAMPLE_RATE)),
            wanted=wanted,
            fallback=int(round(seek_to * SAMPLE_RATE)),
        )
        for frame in container.decode(stream):
            if not trimmer.feed(resampler.resample(frame)):
                break
        else:
            # Flush the resampler so the tail of the range is not lost to the
            # filter group delay.
            trimmer.feed(resampler.resample(None))
    return trimmer.audio()


class _Trimmer:
    """Cuts an exact sample count out of a stream of resampled frames.

    The decoder lands on a keyframe before the requested start, so the head is
    dropped by sample count anchored on the FIRST frame's ``pts``, which carries
    a 1/16000 resolution because the resampler sets the output time base. Every
    later frame is only counted, never measured through ``pts``: the resampler
    emits 341/342 samples per frame while its ``pts`` advances 335..337, so
    trusting ``pts`` drifts up to 336 samples (~21 ms) over a clip. The first
    frame is also short (325 samples) for the same filter reason, which is why
    the length of a range still comes out exact.
    """

    def __init__(self, target: int, wanted: int, fallback: int):
        self._target = target
        self._wanted = wanted
        self._fallback = fallback
        self._drop: int | None = None
        self._held = 0
        self._parts: list[np.ndarray] = []

    def feed(self, frames) -> bool:
        """Add resampled frames; return ``False`` once the range is complete."""
        for frame in frames:
            if not self._push(frame):
                return False
        return True

    def audio(self) -> np.ndarray:
        """The trimmed waveform, shorter than requested when the media ends first."""
        if not self._parts:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._parts)

    def _push(self, frame) -> bool:
        if self._drop is None:
            # Samples between the landing and the requested start.
            self._drop = self._target - self._landing(frame)
            if self._drop < 0:
                raise ClipError(
                    "No se ha podido recortar el tramo con precisión: la "
                    "decodificación ha empezado después de su inicio. Prueba "
                    "con otro tramo."
                )
        samples = frame.to_ndarray().reshape(-1)
        if self._drop:
            if samples.size <= self._drop:
                self._drop -= samples.size
                return True
            samples = samples[self._drop:]
            self._drop = 0
        if self._held + samples.size >= self._wanted:
            self._append(samples[: self._wanted - self._held])
            return False
        self._append(samples)
        return True

    def _landing(self, frame) -> int:
        """Index in the 16 kHz stream of this frame's first sample."""
        if frame.pts is None:  # no timestamp: assume the seek landed where asked
            return self._fallback
        return int(frame.pts)

    def _append(self, samples: np.ndarray) -> None:
        self._parts.append(samples.astype(np.float32) / S16_FULL_SCALE)
        self._held += samples.size
