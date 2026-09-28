"""faster-whisper wrapper: lazy model loading and streaming transcription."""

from typing import Callable, Optional

from faster_whisper import WhisperModel

from app.core.config import (
    WHISPER_BEAM_SIZE,
    WHISPER_COMPUTE_TYPE,
    WHISPER_DEVICE,
    WHISPER_MODEL,
)


class Transcriber:
    """Loads a Whisper model once and transcribes with per-segment callbacks."""

    def __init__(self, model_name: str = WHISPER_MODEL):
        self.model_name = model_name
        self._model: Optional[WhisperModel] = None

    def get_model(self) -> WhisperModel:
        if self._model is None:
            self._model = WhisperModel(
                self.model_name,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE_TYPE,
            )
        return self._model

    def transcribe_segments(
        self,
        path: str,
        language: Optional[str] = None,
        task: str = "transcribe",
        ranges: Optional[list[tuple[float, float]]] = None,
    ):
        """Return ``(segments, info)`` with the configured decode options.

        ``ranges`` restricts the run to the given ``(start, end)`` time ranges
        in seconds. Each range is decoded with a margin of neighbouring audio
        (``clipper.RANGE_WARMUP``) and the segments are trimmed back to the
        range, because a range handed to whisper bare loses its edge speech and
        folds its quiet interjections into one long segment. The returned
        segment times are absolute, so the caller can align the result with the
        media it came from. ``ranges=None`` (or empty) means the whole file,
        which is handed to whisper untouched.

        The language is detected on the first range and then pinned for the
        remaining ones, so a single run cannot mix languages. Short ranges are
        where whisper guesses the language worst, a known limitation below
        roughly 30 s of audio that the whole-file path already has for short
        files.
        """
        if not ranges:
            return self.get_model().transcribe(
                path,
                language=language,
                task=task,
                beam_size=WHISPER_BEAM_SIZE,
                vad_filter=True,
            )

        from app.services.clipper import RANGE_WARMUP, decode_range, duration

        model = self.get_model()
        detected: Optional[str] = language
        segments: list = []
        info = None
        total = duration(path)
        for start, end in ranges:
            # The model is handed the range plus a margin, and the result is
            # trimmed back. A bare range has no context at all: the first and
            # last window of an isolated clip are the worst conditioned ones, so
            # speech at the edges gets clipped and the quiet interjections inside
            # get folded into one long segment. Diarization already decodes with
            # this same margin for its own reason; see clipper.RANGE_WARMUP for
            # the measurements behind the value.
            low = max(0.0, start - RANGE_WARMUP)
            high = end + RANGE_WARMUP
            if total:
                high = min(high, total)
            audio = decode_range(path, low, high)
            # A range over silence legitimately yields no segment at all, so
            # none is assumed here and no_speech_threshold is left alone.
            found, range_info = model.transcribe(
                audio,
                language=detected,
                task=task,
                beam_size=WHISPER_BEAM_SIZE,
                vad_filter=True,
            )
            # whisper timestamps are relative to the array it was given, so the
            # window offset is what puts them back on the media timeline. The
            # generator is drained here, inside the range it belongs to. A
            # segment that reaches into the margin keeps its text and is clamped
            # to the range; one lying wholly in the margin is dropped, which is
            # what keeps the margin from leaking into the result.
            for segment in found:
                segment.start = max(start, segment.start + low)
                segment.end = min(end, segment.end + low)
                if segment.end > segment.start:
                    segments.append(segment)
            if info is None:
                info = range_info
            if detected is None:
                detected = range_info.language
        return segments, info

    def transcribe(
        self,
        path: str,
        language: Optional[str] = None,
        on_segment: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[float], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        ranges: Optional[list[tuple[float, float]]] = None,
    ) -> tuple[str, str]:
        """Transcribe streaming segment text.

        Returns ``(full_text, detected_language_code)``.
        """
        if on_status:
            on_status("Cargando modelo de voz…")
        self.get_model()
        segments, info = self.transcribe_segments(
            path, language=language, task="transcribe", ranges=ranges
        )
        total = progress_total(path, info, ranges)
        lines: list[str] = []
        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            lines.append(text)
            if on_segment:
                on_segment(text)
            if on_progress and total:
                on_progress(segment.end / total * 100.0)
        return "\n".join(lines), info.language


def progress_total(
    path: str,
    info,
    ranges: Optional[list[tuple[float, float]]] = None,
) -> float:
    """Timeline length, in seconds, that progress is measured against.

    ``info.duration`` is whisper's view of what it was handed: the whole file
    when there are no ranges, but only the decoded chunk when there are. With
    ranges the segment times are absolute, so progress has to be read on the
    media timeline or a run over one range of a long file would claim to be
    finished at the end of that range.
    """
    if not ranges:
        return info.duration or 0.0
    from app.services.clipper import duration

    return duration(path)