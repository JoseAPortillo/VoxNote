"""Orchestration of the transcription / translation workflows."""

from typing import Callable, Optional

from app.services import translator as argos
from app.services.transcriber import Transcriber


Callback = Optional[Callable[[str], None]]
ProgressCallback = Optional[Callable[[float], None]]


class Pipeline:
    """Coordinates the engine for the two user actions.

    - ``transcribe``: transcription in the detected source language.
    - ``transcribe_and_translate``: transcription translated to the target
      language (Spanish by default) via Argos.
    """

    def __init__(self, transcriber: Transcriber, target_lang: str = "es"):
        self.transcriber = transcriber
        self.target_lang = target_lang

    def transcribe(
        self,
        path: str,
        on_segment: Callback = None,
        on_progress: ProgressCallback = None,
        on_status: Callback = None,
    ) -> tuple[str, str]:
        return self.transcriber.transcribe(
            path,
            on_segment=on_segment,
            on_progress=on_progress,
            on_status=on_status,
        )

    def transcribe_and_translate(
        self,
        path: str,
        on_segment: Callback = None,
        on_progress: ProgressCallback = None,
        on_status: Callback = None,
    ) -> tuple[str, str]:
        if on_status:
            on_status("Cargando modelo de voz…")
        self.transcriber.get_model()
        segments, info = self.transcriber.transcribe_segments(path, task="transcribe")
        detected = info.language
        if on_status:
            on_status(f"Preparando traducción {detected} → {self.target_lang}…")
        argos.ensure_language_pair(detected, self.target_lang)

        total = info.duration or 0.0
        lines: list[str] = []
        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            # NOTE: segment-level translation loses cross-sentence context.
            # Acceptable for live output; a whole-text pass is a future option.
            translated = argos.translate_text(text, detected, self.target_lang)
            lines.append(translated)
            if on_segment:
                on_segment(translated)
            if on_progress and total:
                on_progress(segment.end / total * 100.0)
        return "\n".join(lines), detected