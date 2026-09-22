# Feature: Save Transcripts (feature/save-transcripts)

## Objective
After a transcription or translation finishes, let the user save the result as TXT + SRT via a "Guardar como…" dialog with an automatically suggested filename.

## Problem
The app shows the result in the output pane but discards it on close; there is no way to persist transcriptions/translations. faster-whisper provides per-segment timestamps (`segment.start/end`) but the pipeline currently drops them.

## Why now
Transcription/translation is the core value; results must be usable beyond the window (study, subtitles, sharing). TXT + SRT covers both reading and video subtitling.

## Scope
- New dataclass `TimedSegment(start, end, text, speaker)` in `app/core/models.py`.
- `Pipeline` collects `TimedSegment` list in every mode:
  - plain transcribe/translate: one segment per whisper segment.
  - diarized: one segment per speaker turn (start = first whisper segment of the turn, end = last), preserving `[Hablante N]` label in text.
  - Live `on_segment` streaming text callbacks stay unchanged (UI behavior untouched).
- Pipeline return changes to `(text, language, segments)` so the UI can offer saving.
- New `app/services/exporter.py`:
  - `format_ts(seconds) -> "HH:MM:SS,mmm"` (SRT) and `format_ts_txt(seconds) -> "HH:MM:SS"` (TXT).
  - `export(segments, base_path, language)` writes `<base>.txt` (`[HH:MM:SS] text` per line) and `<base>.srt` (numbered cues, worst-case zero-duration = 1 ms).
- `app/ui/workers.py`: `done` signal gains the segments payload (emit `(text, language, segments)`).
- `app/ui/main_window.py`: after `_on_done`, enable a "Guardar TXT + SRT…" button; disabled while busy; on click, `QFileDialog.getSaveFileName` with suggested `<stem>.<lang>.txt` (same stem for `.srt`), then `exporter.export`, status label + message box on failure.
- No autosave; saving is always explicit via dialog.

## Constraints
- Windows 11, Python 3.14.3, venv `.venv`, PySide6, faster-whisper segments must be consumed eagerly (they are a generator) before the worker finishes.
- No new runtime dependencies.
- Conventional commits, no AI attribution.
- TDD: no test suite → functional checks = offscreen smoke + real export to temp dir.
- forecast < 400 authored lines → single work-unit commit.

## Checklist
- [X] T1: Add `TimedSegment` dataclass + update pipeline to collect segments (all 4 modes: transcribe/translate × diarize on/off)
- [X] T2: Create `app/services/exporter.py` (TXT + SRT writers, timestamp formatters)
- [X] T3: Update worker `done` signal to carry segments
- [X] T4: MainWindow: "Guardar TXT + SRT…" button (disabled while busy), save dialog with suggested filename, error handling
- [X] T5: Verification: offscreen smoke (transcribe/translate flow, save button enable/disable, fake export to temp with fixture segments) + real export check
- [X] T6: Work-unit commit on feature branch — `00d85fcf292c87942f5149afe164360f4db9e968` (`feat: guardar transcripciones/traducciones en TXT y SRT`, 5 files, +171/−18)

## Acceptance criteria
- After any completed run, "Guardar TXT + SRT…" is enabled; clicking opens save dialog with suggested `<stem>.<lang>.txt`.
- Saving produces both `.txt` and `.srt` with correct timestamps (SRT format `HH:MM:SS,mmm`, sequential cues) and speaker labels preserved in diarized output.
- Failure to write surfaces in the status label and a message box; button disabled while busy and after a run before done.
- Existing behavior (streaming output, buttons, themes) unchanged.

## Verification evidence
- Writer (general, perfil estándar): EXPORTER OK (timestamps, SRT, TXT); PIPELINE OK + DIARIZED OK (mock, 1 turno → `[Hablante 1] ...`, bounds 0.0–4.0); UI OK (btn_save disabled/enabled); IMPORT OK; ZERO-DUR OK (cue 0ms → +1ms).
- Parent spot check: MainWindow offscreen + `_on_done` con 2 TimedSegment → `SPOT CHECK OK`.
- RDD off (default): `gentle-ai review assess` diff 8ed4515..00d85fc → medium (executable_change), 189 líneas, review_due=under_budget. Sin ceremonia de review; entrega sigue política ordinaria.
- Notas del writer: diarizado+translate no mockeado (Argos real); si el usuario teclea `.srt` en el diálogo se fuerza `.txt` (diseño); transcripción silenciosa → export vacío aceptable.