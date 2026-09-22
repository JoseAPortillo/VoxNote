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
    ):
        """Return ``(segments, info)`` with the configured decode options."""
        return self.get_model().transcribe(
            path,
            language=language,
            task=task,
            beam_size=WHISPER_BEAM_SIZE,
            vad_filter=True,
        )

    def transcribe(
        self,
        path: str,
        language: Optional[str] = None,
        on_segment: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[float], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> tuple[str, str]:
        """Transcribe streaming segment text.

        Returns ``(full_text, detected_language_code)``.
        """
        if on_status:
            on_status("Cargando modelo de voz…")
        self.get_model()
        segments, info = self.transcribe_segments(path, language=language, task="transcribe")
        total = info.duration or 0.0
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