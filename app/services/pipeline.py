"""Orchestration of the transcription / translation workflows."""

from typing import Callable, Optional

from app.core.models import RunResult, TimedSegment
from app.services import translator as argos
from app.services.diarizer import Diarizer, assign_speaker, speaker_index
from app.services.transcriber import Transcriber


Callback = Optional[Callable[[str], None]]
ProgressCallback = Optional[Callable[[float], None]]


class Pipeline:
    """Coordinates the engine for the two user actions.

    - ``transcribe``: transcription in the detected source language.
    - ``transcribe_and_translate``: transcription translated to the target
      language (Spanish by default) via Argos.

    Both accept ``diarize=True`` to label each line with its speaker
    (``[Hablante N]``). Diarization is offline: the speaker turns are computed
    before transcription starts, so the output stops streaming live while the
    diarization pass runs.

    Every entry point returns a :class:`RunResult`: ``transcribe`` fills the
    transcript side; ``transcribe_and_translate`` fills BOTH sides, with the
    transcript side holding the ORIGINAL (untranslated) lines/segments and the
    translation side the translated ones. ``on_segment`` always receives the
    line the app shows live: the raw text for transcribe, the translated text
    for translate.
    """

    def __init__(
        self,
        transcriber: Transcriber,
        target_lang: str = "es",
        diarizer: Diarizer | None = None,
    ):
        self.transcriber = transcriber
        self.target_lang = target_lang
        self.diarizer = diarizer

    # -- public API ----------------------------------------------------------

    def transcribe(
        self,
        path: str,
        on_segment: Callback = None,
        on_progress: ProgressCallback = None,
        on_status: Callback = None,
        diarize: bool = False,
    ) -> RunResult:
        if diarize:
            return self._run_diarized(
                path, translate=False,
                on_segment=on_segment, on_progress=on_progress, on_status=on_status,
            )
        if on_status:
            on_status("Cargando modelo de voz…")
        self.transcriber.get_model()
        segments, info = self.transcriber.transcribe_segments(path, task="transcribe")
        total = info.duration or 0.0
        lines: list[str] = []
        timed: list[TimedSegment] = []
        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            lines.append(text)
            timed.append(TimedSegment(segment.start, segment.end, text, None))
            if on_segment:
                on_segment(text)
            if on_progress and total:
                on_progress(segment.end / total * 100.0)
        return RunResult(
            transcript_text="\n".join(lines),
            transcript_lang=info.language,
            transcript_segments=timed,
        )

    def transcribe_and_translate(
        self,
        path: str,
        on_segment: Callback = None,
        on_progress: ProgressCallback = None,
        on_status: Callback = None,
        diarize: bool = False,
    ) -> RunResult:
        if diarize:
            return self._run_diarized(
                path, translate=True,
                on_segment=on_segment, on_progress=on_progress, on_status=on_status,
            )
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
        timed: list[TimedSegment] = []
        # Keep the ORIGINAL (pre-translation) sides too: the app shows both
        # panes and persists both per media file.
        orig_lines: list[str] = []
        orig_timed: list[TimedSegment] = []
        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            # NOTE: segment-level translation loses cross-sentence context.
            # Acceptable for live output; a whole-text pass is a future option.
            translated = argos.translate_text(text, detected, self.target_lang)
            lines.append(translated)
            timed.append(TimedSegment(segment.start, segment.end, translated, None))
            orig_lines.append(text)
            orig_timed.append(TimedSegment(segment.start, segment.end, text, None))
            if on_segment:
                on_segment(translated)
            if on_progress and total:
                on_progress(segment.end / total * 100.0)
        return RunResult(
            transcript_text="\n".join(orig_lines),
            transcript_lang=detected,
            transcript_segments=orig_timed,
            translation_text="\n".join(lines),
            translation_lang=self.target_lang,
            translation_segments=timed,
        )

    # -- internal ------------------------------------------------------------

    def _ensure_diarizer(self) -> Diarizer:
        if self.diarizer is None:
            from app.core.config import HF_TOKEN

            self.diarizer = Diarizer(HF_TOKEN)
        return self.diarizer

    def _run_diarized(
        self,
        path: str,
        translate: bool,
        on_segment: Callback,
        on_progress: ProgressCallback,
        on_status: Callback,
    ) -> RunResult:
        # Offline pass: who speaks when (slow on CPU, no live output yet).
        if on_status:
            on_status("Analizando hablantes (diarización)…")
        turns = self._ensure_diarizer().diarize(path)
        labels = speaker_index(turns)

        if on_status:
            on_status("Cargando modelo de voz…")
        self.transcriber.get_model()
        segments, info = self.transcriber.transcribe_segments(path, task="transcribe")
        detected = info.language

        if translate:
            if on_status:
                on_status(f"Preparando traducción {detected} → {self.target_lang}…")
            argos.ensure_language_pair(detected, self.target_lang)

        total = info.duration or 0.0
        lines: list[str] = []
        timed: list[TimedSegment] = []
        # Original sides (with the same speaker labels) for the transcript pane.
        orig_lines: list[str] = []
        orig_timed: list[TimedSegment] = []
        current_speaker = None
        current_texts: list[str] = []
        current_bounds: list[tuple[float, float]] = []

        def flush() -> None:
            nonlocal current_texts, current_bounds
            if not current_texts:
                return
            raw = " ".join(current_texts)
            text = argos.translate_text(raw, detected, self.target_lang) if translate else raw
            label = f"[Hablante {labels.get(current_speaker, '?')}] " if current_speaker else ""
            orig_line = f"{label}{raw}"
            line = f"{label}{text}"
            lines.append(line)
            timed.append(
                TimedSegment(
                    start=current_bounds[0][0],
                    end=current_bounds[-1][1],
                    text=line,
                    speaker=labels.get(current_speaker),
                )
            )
            orig_lines.append(orig_line)
            orig_timed.append(
                TimedSegment(
                    start=current_bounds[0][0],
                    end=current_bounds[-1][1],
                    text=orig_line,
                    speaker=labels.get(current_speaker),
                )
            )
            if on_segment:
                on_segment(line)
            current_texts = []
            current_bounds = []

        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            speaker = assign_speaker(segment.start, segment.end, turns)
            if speaker != current_speaker and current_texts:
                flush()
            current_speaker = speaker
            current_texts.append(text)
            current_bounds.append((segment.start, segment.end))
            if on_progress and total:
                on_progress(segment.end / total * 100.0)
        flush()

        if translate:
            return RunResult(
                transcript_text="\n".join(orig_lines),
                transcript_lang=detected,
                transcript_segments=orig_timed,
                translation_text="\n".join(lines),
                translation_lang=self.target_lang,
                translation_segments=timed,
            )
        return RunResult(
            transcript_text="\n".join(lines),
            transcript_lang=detected,
            transcript_segments=timed,
        )