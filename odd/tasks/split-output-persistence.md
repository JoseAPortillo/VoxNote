# Feature: Split Output + Per-Media Persistence (feature/split-output-persistence)

## Objective
Split the single output pane into two side-by-side read-only text boxes: **Transcripción** (original language) and **Traducción** (target language). Results persist per media file: selecting an item in the list restores its previously generated transcription and/or translation; re-running transcribe/translate overwrites the stored result for the selected item.

## Problem
The app can do both transcription and translation but shows one merged text, discards it on close, and cannot recall what a file produced before. The user is forced to re-run or lose track of per-file results.

## Why now
Save TXT/SRT + playback + seek are done; the output model (one pane, transient) is now the weakest part of the UX for real study workflows.

## Scope
- `app/core/models.py`:
  - Extend `MediaItem` with persisted result fields, ALL with `default=None` (old `items.json` entries load unchanged): `transcript_text`, `transcript_lang`, `transcript_segments`, `translation_text`, `translation_lang`, `translation_segments` (segments as `list[dict]`).
  - Add `TimedSegment.to_dict()` / `TimedSegment.from_dict()` for JSON round-trip.
  - Add dataclass `RunResult` with: `transcript_text`, `transcript_lang`, `transcript_segments`; `translation_text`, `translation_lang`, `translation_segments` (all with defaults; empty/None when not produced).
- `app/services/pipeline.py`:
  - `transcribe()` returns `RunResult` with transcript fields set.
  - `transcribe_and_translate()` returns `RunResult` with transcript fields = the ORIGINAL (untranslated) text/segments AND translation fields = the translated text/segments (detected lang + target lang).
  - Diarized paths do the same (transcript = original with `[Hablante N]` labels, translation = translated with labels when translate=True).
- `app/storage/repository.py`:
  - `index_of(path) -> int | None` (case-insensitive, matching `add`'s normalization).
  - `update_results(index, *, transcript_text=..., transcript_lang=..., transcript_segments=..., translation_text=..., translation_lang=..., translation_segments=...)` — only set provided fields, persists.
- `app/ui/workers.py`:
  - `done = Signal(object)` now emits a `RunResult` (replaces the 3-tuple `(str, str, object)`). Keep `segment_ready`/`progress`/`status`/`failed` unchanged.
- `app/ui/main_window.py`:
  - Replace the single `self.output` QPlainTextEdit with a horizontal layout of TWO panes, each a small labeled column: header label ("Transcripción" / "Traducción") + `QPlainTextEdit` (read-only). Equal stretch; placeholder text explaining meaning.
  - `_append_segment(text, pane)`: routes to the currently running pane (transcribe → transcription; translate → translation).
  - At run start: clear the pane being produced (transcribe → transcription; translate → translation). Do NOT clear the other pane.
  - On `done(result: RunResult)`:
    - Transcribe: set transcription pane = `result.transcript_text`; leave translation pane as-is.
    - Translate: set BOTH panes (transcription = original, translation = translated).
    - Persist the produced fields to the repository for the selected item (`update_results`).
  - On selection change (`itemSelectionChanged`, already connected): look up the selected path in the repository; if persisted results exist, fill both panes and set `_last_run` (so Save TXT+SRT works on previously generated results); otherwise clear both panes (placeholders) and null `_last_run`.
  - Replace `_last_text`/`_last_lang`/`_last_segments` with a single `_last_run: RunResult | None`; `_start_save`/`_suggested_save_path` use it (segments = translation segments when present, else transcript segments; lang = translation lang when present else transcript lang).
  - Keep status label flow, busy state, save dialog, playback, seek bar, theme toggle unchanged.
- `app/ui/theme.py`: style the pane header labels (reuse existing `$` palette, e.g. `#hintLabel` style or a muted label) so both themes look consistent.
- No new runtime dependencies.
- `data/items.json` grows new optional fields; old files MUST load with no crash and no data loss; corrupt-file recovery stays as-is.

## Constraints
- Windows 11, Python 3.14.3, venv `.venv`, PySide6.
- `items.json` compatibility: old entries (only `path`/`alias`/`added_at`) must load via `MediaItem(**entry)`.
- Persistence keyed by FILE (path), independent of alias changes.
- Conventional commits, no AI attribution.
- TDD: no test suite → functional checks = offscreen smokes with a TEMP WAV and a TEMP items.json (never the user's real media/items).
- forecast < 400 authored lines → single work-unit commit.

## Checklist
- [x] T1: models: MediaItem result fields (defaults), TimedSegment to/from_dict, RunResult dataclass
- [x] T2: pipeline: transcribe/transcribe_and_translate (incl. diarized) return RunResult with original + translation
- [x] T3: repository: index_of + update_results
- [x] T4: worker: done emits RunResult
- [x] T5: main_window: two panes, routing, done handler with persistence, selection restore, _last_run save flow
- [x] T6: theme pane labels + verification smokes + work-unit commit on feature branch

## Acceptance criteria
- One pane for transcription, one for translation, side by side. ✅
- Transcribir → transcription pane updates; translation pane untouched. ✅ (smoke 5)
- Traducir → BOTH panes filled (original | translated). ✅ (smoke 5)
- Selecting a media item in the list shows its previously generated texts (transcription and/or translation) immediately. ✅ (smoke 5 restore)
- Re-running transcribe/translate on an item overwrites the stored result for that item; other items' results are untouched. ✅ (smoke 5: item[1] stays None)
- Saving TXT+SRT works on freshly generated AND on previously generated (restored) results. ✅ (_last_run-driven _start_save; path/lang asserted in smoke 5)
- Old items.json files load unaffected; removing an item removes its stored results. ✅ (smoke 2 old-JSON load; remove() already deletes the whole entry)

## Verification evidence
- Work-unit commit: `feat: doble panel de transcripcion/traduccion con persistencia por archivo` (full SHA in the writer report; orchestrator backfills here).
- All smokes ran with `QT_QPA_PLATFORM=offscreen`, temp WAV + temp `items.json` under `%LOCALAPPDATA%\Temp\opencode\split-smoke` (real data/items.json untouched — verified via `git status data/`).
- Smoke 1 (models): MediaItem(**old-entry) OK, new fields None; TimedSegment to_dict/from_dict round-trip; RunResult defaults. PASS
- Smoke 2 (repository): old-style 3-field JSON loads (new fields None); index_of case-insensitive; update_results sets only provided fields + persists; reload round-trip; corrupt-file recovery unchanged. PASS
- Smoke 3 (pipeline): fake Transcriber + patched argos — transcribe → transcript-only RunResult; transcribe_and_translate → transcript=ORIGINAL + translation=translated with identical timings, callback streams translated line; diarized translate=True both labeled sets; diarized transcribe transcript-only. PASS
- Smoke 4 (worker): QSignalSpy — done emits the RunResult object. PASS
- Smoke 5 (main_window): two side-by-side panes with headers; empty-selection clears panes; transcribe → left pane only + transcript persisted; translate → both panes + translation persisted (stored transcript untouched); reselect clears/restores; `_suggested_save_path` ends with `tone.es.txt`; playback/seek wiring alive; transcribe/translate/save buttons enable correctly. PASS
  - Bug caught+fixed during smoke: button refresh ordering — `_refresh_buttons` now re-run at the end of `_on_selection_changed` so Save disables/enables with selection restores.
- Smoke 6 (import/theme): `from app.ui.main_window import MainWindow` OK; applied QSS contains `QLabel#paneLabel` rule. PASS
- Diff size: 6 app files, +286/−45 → single work-unit commit (forecast < 400 lines held).