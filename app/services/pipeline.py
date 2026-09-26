"""Orchestration of the transcription / translation workflows."""

from typing import TYPE_CHECKING, Callable, Optional

from app.core.models import RunResult, TimedSegment
from app.services import translator as argos
from app.services.diarizer import Diarizer, assign_speaker, speaker_index
from app.services.transcriber import Transcriber, progress_total

if TYPE_CHECKING:  # storage stays a UI-facing concern; only the type is needed
    from app.storage.repository import MediaRepository


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

    Both also accept ``ranges``, a list of ``(start, end)`` second pairs, to run
    only over the marked parts of the media. The result is the same shape, with
    timestamps absolute against the media, so an exported SRT still subtitles
    the original file. ``ranges=None`` means the whole file.

    When a ``repository`` is supplied, a run that consumed marks clears them
    once it succeeds: the marks existed to scope that run, so leaving them
    behind would make the next transcription of the same media cover the
    fragment just processed and nothing else. That lifecycle lives here, in
    the run, instead of in the UI, because the marks are an input of the
    execution rather than UI state to orchestrate. With no repository the
    pipeline behaves exactly as before.

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
        repository: "MediaRepository | None" = None,
    ):
        self.transcriber = transcriber
        self.target_lang = target_lang
        self.diarizer = diarizer
        self.repository = repository

    # -- public API ----------------------------------------------------------

    def transcribe(
        self,
        path: str,
        on_segment: Callback = None,
        on_progress: ProgressCallback = None,
        on_status: Callback = None,
        diarize: bool = False,
        ranges: list[tuple[float, float]] | None = None,
    ) -> RunResult:
        if diarize:
            result = self._run_diarized(
                path, translate=False, ranges=ranges,
                on_segment=on_segment, on_progress=on_progress, on_status=on_status,
            )
            self._consume_marks(path, ranges)
            return result
        if on_status:
            on_status(self._loading_status(ranges))
        self.transcriber.get_model()
        segments, info = self.transcriber.transcribe_segments(
            path, task="transcribe", ranges=ranges
        )
        total = progress_total(path, info, ranges)
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
        result = RunResult(
            transcript_text="\n".join(lines),
            transcript_lang=info.language,
            transcript_segments=timed,
        )
        self._consume_marks(path, ranges)
        return result

    def transcribe_and_translate(
        self,
        path: str,
        on_segment: Callback = None,
        on_progress: ProgressCallback = None,
        on_status: Callback = None,
        diarize: bool = False,
        ranges: list[tuple[float, float]] | None = None,
    ) -> RunResult:
        if diarize:
            result = self._run_diarized(
                path, translate=True, ranges=ranges,
                on_segment=on_segment, on_progress=on_progress, on_status=on_status,
            )
            self._consume_marks(path, ranges)
            return result
        if on_status:
            on_status(self._loading_status(ranges))
        self.transcriber.get_model()
        segments, info = self.transcriber.transcribe_segments(
            path, task="transcribe", ranges=ranges
        )
        detected = info.language
        if on_status:
            on_status(f"Preparando traducción {detected} → {self.target_lang}…")
        argos.ensure_language_pair(detected, self.target_lang)

        total = progress_total(path, info, ranges)
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
        result = RunResult(
            transcript_text="\n".join(orig_lines),
            transcript_lang=detected,
            transcript_segments=orig_timed,
            translation_text="\n".join(lines),
            translation_lang=self.target_lang,
            translation_segments=timed,
        )
        self._consume_marks(path, ranges)
        return result

    # -- internal ------------------------------------------------------------

    def _consume_marks(
        self, path: str, ranges: list[tuple[float, float]] | None
    ) -> None:
        """Drop the marks that produced this run, now that it has succeeded.

        Called by both public entry points, after the result exists and never
        on the failure path: an exception during transcription, decoding or
        translation propagates first, so the marks the user placed survive a
        failed run and can be tried again.

        Only a run that actually consumed marks clears them. A whole-file run
        (``ranges=None``) used no marks, so any that exist still describe what
        the user wants to scope next and are left in place. The shared
        repository instance is the one the UI holds, so the file is written
        once, from here, and the UI sees the cleared state without a second
        save of its own.
        """
        if self.repository is None or not ranges:
            return
        index = self.repository.index_of(path)
        if index is None:
            return
        self.repository.clear_marks(index)

    @staticmethod
    def _loading_status(ranges) -> str:
        if not ranges:
            return "Cargando modelo de voz…"
        return "Cargando modelo de voz para el tramo marcado…"

    @staticmethod
    def _diarizing_status(ranges) -> str:
        if not ranges:
            return "Analizando hablantes (diarización)…"
        return "Analizando hablantes del tramo marcado (diarización)…"

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
        ranges: list[tuple[float, float]] | None = None,
    ) -> RunResult:
        # Offline pass: who speaks when (slow on CPU, no live output yet).
        if on_status:
            on_status(self._diarizing_status(ranges))
        turns = self._ensure_diarizer().diarize(path, ranges=ranges)
        labels = speaker_index(turns)

        if on_status:
            on_status(self._loading_status(ranges))
        self.transcriber.get_model()
        segments, info = self.transcriber.transcribe_segments(
            path, task="transcribe", ranges=ranges
        )
        detected = info.language

        if translate:
            if on_status:
                on_status(f"Preparando traducción {detected} → {self.target_lang}…")
            argos.ensure_language_pair(detected, self.target_lang)

        total = progress_total(path, info, ranges)
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