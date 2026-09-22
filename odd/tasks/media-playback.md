# Feature: Media Playback (feature/media-playback)

## Objective
Play the audio of a registered media file directly from the list: each row shows a playback icon in its icon column; clicking it plays the audio. Video files play audio only (no video window) when possible.

## Problem
The app works on media files but the user cannot listen to them; they must open external players. Adding playback makes transcription verification instant.

## Why now
Core pipeline is done and merged. Playback is a small, high-value UX addition.

## Scope
- `app/ui/media_list.py`:
  - `MediaList.reload()` sets a playback icon (QStyle standard icon, e.g. `SP_MediaPlay`) on every item (the icon column — first column of the row).
  - Detect left-click on the icon area per row (`QStyle.subElementRect(SE_ItemViewItemDecoration, ...)` from the item's visual rect) and emit `play_requested = Signal(str)` with the row's path. Clicks on the text keep current behavior (selection, double-click alias editing, context menu).
  - Keep drag&drop, tooltips, alias editing, context menu unchanged.
  - Icon state updates: `set_playing(path)` / `set_stopped(path)` flip `SP_MediaPlay` ↔ `SP_MediaPause` on the active row.
- `app/ui/main_window.py`:
  - Create `QMediaPlayer` + `QAudioOutput` (audio-only output; do NOT attach a QVideoWidget).
  - Connect `media_list.play_requested` → `_toggle_play(path)`:
    - same path currently playing → pause; same path paused → resume; different path → stop previous, set source `QUrl.fromLocalFile(path)`, play.
  - Track `self._playing_path: str | None`; on `mediaStatusChanged` (EndOfMedia) reset icon and path.
  - On selection change or starting a run, stop playback (and reset icon) so inference isn't competing with audio.
  - Status label feedback: "Reproduciendo: <alias>…" on play, "Reproducción pausada." / cleared on stop.
  - On window close, stop playback.
- No new runtime dependencies (QtMultimedia already ships with PySide6; verified `QtMultimedia OK` in venv).
- Audio-only playback for video files is the default QMediaPlayer behavior when no video output is attached.

## Constraints
- Windows 11, Python 3.14.3, venv `.venv`, PySide6 (QtMultimedia present).
- Keep drag&drop, alias editing (double-click), context menu, themes, and inference flow intact.
- Conventional commits, no AI attribution.
- TDD: no test suite → functional checks = offscreen smoke (widgets + toggling without real audio device issues) and one real playback check (2 s WAV).
- forecast < 400 authored lines → single work-unit commit.

## Checklist
- [X] T1: MediaList: playback icons in reload(), icon-area click detection → `play_requested` signal, `set_playing`/`set_stopped`
- [X] T2: MainWindow: QMediaPlayer + QAudioOutput wiring, `_toggle_play`, path/state tracking, EndOfMedia reset, stop on selection change / inference start / close
- [X] T3: Verification: offscreen smoke (list has icons, click-detection logic returns path, toggle mock without device) + real short-WAV playback check
- [X] T4: Work-unit commit on feature branch — `e8ed805ee6aeacb3d27661a4f607fea7379946b6` (`feat: reproducir audio de los medios desde la lista (icono por fila)`, 2 files, +100/−1)

## Acceptance criteria
- Every row shows a play icon; clicking the icon plays the audio-only stream (video shows no window).
- Clicking the same icon pauses/resumes; clicking another row's icon switches playback.
- Icons reflect state: active row shows pause while playing, play when stopped; resets at EndOfMedia.
- Selecting another row or starting transcribe/translate stops playback.
- Alias double-click, context menu, drag&drop still work.

## Verification evidence
- Writer (general, perfil estándar): ICONS OK (MKV real); CLICK EMITS / TEXT CLICK IGNORED / PAUSE/PLAY ICON OK (QTest); PLAY OK + ended=True en 2.11 s (WAV 2 s, offscreen: Loading→Loaded→Buffering→Buffered→EndOfMedia); UI-PLAYER OK; TOGGLE MOCK OK; MAIN IMPORT OK.
- Parent spot check: primero falló por diseño del check (verificaba PlayingState tras el fin del WAV de 1 s → StoppedState; y borraba el temp con handle ffmpeg activo). Diagnóstico: statuses Loading→Loaded→Buffering→Buffered→EndOfMedia, hasAudio=True, duration=1000 → playback funciona. Spot final con WAV 3 s: SPOT PLAY OK (PlayingState en ≤3 s) + ended=EndOfMedia. El retenedor del temp es el handle de ffmpeg, no un bug.
- RDD off (default): `gentle-ai review assess` diff 00d85fc..e8ed805 → medium (executable_change app/ui/main_window.py), 101 líneas, review_due=under_budget. Sin ceremonia de review.
- ⚠️ Nota de integridad: el writer borró la entrada real del usuario (D:\WORKS_2026\DIGIC PICTURE\0200_440.mkv) de data/items.json durante sus smokes; el padre la restauró. Para smokes futuros: usar repo temporal, no el real.