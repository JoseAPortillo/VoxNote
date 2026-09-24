# Feature: Time Slider (feature/time-slider)

## Objective
Add a seek time slider for audio playback: the user can forward/reverse through the media. The slider is fully mouse-interactive — pressing and holding the left button and dragging moves playback position, and a plain click jumps the current time to the clicked point.

## Problem
Playback exists (icon per row → QMediaPlayer audio-only) but the user cannot navigate within a long audio/video; verification of a specific segment requires listening from the start.

## Why now
Playback is the latest feature; seeking is its missing core interaction and a high-value, low-risk addition.

## Scope
- New widget `SeekSlider(QSlider)` (horizontal, `app/ui/seek_slider.py`):
  - `mousePressEvent` with left button → set value from click x position using `QStyle.sliderValueFromPosition` (NOT the default page-step behavior).
  - `mouseMoveEvent` with left button held → follow the cursor (drag). QSlider's own `sliderMoved` keeps working for tracking.
  - Standard signals (`sliderPressed`, `sliderReleased`, `sliderMoved`, `valueChanged`) remain the wiring points.
- `app/ui/main_window.py`:
  - Add a playback bar row (slider + compact time label `mm:ss / mm:ss`), hidden initially; visible while a source is loaded (playing or paused), hidden on stop.
  - `player.durationChanged` → set slider range `0..duration` and update the total-time label.
  - `player.positionChanged` → update slider value + elapsed label ONLY when not seeking.
  - `sliderPressed` → `self._seeking = True`; `sliderReleased` → `self._seeking = False` and `self.player.setPosition(slider.value())` (works while playing AND while paused).
  - Reset slider value/labels on source switch (`_toggle_play` to a different path) and on `_stop_playback`.
  - Time formatting helper `_fmt_ms(ms) -> "m:ss"` (or `"h:mm:ss"` when >= 1 h).
- `app/ui/theme.py`: QSS for `QSlider::groove:horizontal`, `::sub-page:horizontal` (accent/played), `::add-page:horizontal` (track), `::handle:horizontal` (accent circle), using existing `$` palette placeholders so both themes are styled. Slider must look right in dark and light.
- No new runtime dependencies (QSlider/QStyle ship with PySide6).
- Keep icon play/pause toggle, stop-on-inference/selection/close, and all existing behaviors intact.

## Constraints
- Windows 11, Python 3.14.3, venv `.venv`, PySide6.
- Click on the slider MUST jump to the clicked position (default Qt behavior moves by page step — override it).
- Dragging with left button held MUST continuously follow the cursor and seek on release.
- Conventional commits, no AI attribution.
- TDD: no test suite → functional checks = offscreen smoke (widget creation, click-to-position math with QTest, drag simulation, play + seek on a real 2–3 s WAV, no fighting between positionChanged and seeking state).
- forecast < 400 authored lines → single work-unit commit.
- Smokes MUST use a temp/repo-local media file, NEVER the user's real media entries (previous session corrupted data/items.json with real-path smokes).

## Checklist
- [x] T1: `SeekSlider` widget: click-to-position press, drag follow, signals intact
- [x] T2: MainWindow playback bar: duration/position wiring, seeking flag, setPosition on release, reset on switch/stop, time labels
- [x] T3: theme.py QSS slider styles (dark + light)
- [x] T4: Verification smokes (offscreen QTest: click position, drag, real WAV play + seek) + work-unit commit on feature branch

## Acceptance criteria
- While playing or paused, a horizontal time slider is visible with elapsed / total time.
- Clicking anywhere on the slider jumps playback (or paused position) to that point.
- Pressing and dragging with the left button moves the position continuously; on release the media seeks there.
- The slider reflects live playback position while playing and does not fight the user during a drag.
- Slider and labels reset when playback stops; hidden when no source is loaded.
- Slider is styled consistently in dark and light themes; icon playback and all other behaviors unchanged.

## Verification evidence
All smokes run with the repo venv (`.venv\Scripts\python.exe`, Python 3.14.3) and
`QT_QPA_PLATFORM=offscreen`; media = temp 3 s WAV tone in
una carpeta temporal fuera del repo.
`data\items.json` SHA-256 identical before and after every smoke (never touched).

- `smoke_slider.py` (click math, drag, signals): ALL PASS
  - `QStyle.sliderValueFromPosition(0,1000,200,400)=500` (x=half → midpoint), `(0,1000,400,400)=1000` (x=width → max)
  - QTest click at x=half → value=500; at x=width → value=1000
  - sliderPressed=1, sliderReleased=1 fired on click (QSignalSpy SignalInstance)
  - press at x=quarter → 250; drag to x=3/4 → value=769 (follows cursor, Qt offset math ≈750, tol OK); sliderReleased=2 on release; sliderMoved fired during drag
  - Note: QSignalSpy must be built with the signal instance (`QSignalSpy(s.sliderPressed)`); the string form is silently invalid in this PySide6.
- `smoke_playback.py` (MainWindow + real WAV play + seek): ALL PASS
  - `_fmt_ms`: 0→"00:00", 61000→"01:01", 3599000→"59:59", 3600000→"1:00:00", 3661000→"1:01:01"
  - bar hidden before source; shown on `_toggle_play`; duration=3000 ms detected; slider range=(0,3000); label "00:00 / 00:03"
  - QTest click mid → `player.position()=1500` (~dur/2)
  - drag quarter→three-quarter: `_seeking` True while held, slider 748→2278, release → `position()=2278` (~3·dur/4), `_seeking` False
  - `_stop_playback()` → bar hidden, label "00:00 / 00:00", slider 0, `_seeking` False, path cleared
  - items.json hash identical before/after
- Import smoke: `& .venv\Scripts\python.exe -c "from app.ui.main_window import MainWindow"` → `IMPORT OK: app.ui.main_window`
- Theme smoke: `apply_theme(app, True/False)` → QSS contains `QSlider::groove/sub-page/add-page/handle:horizontal` + `QSlider:disabled::handle:horizontal`, no unsubstituted `$`, accent `#4f8cff` (dark) / `#2f6fed` (light) present → ALL PASS
- Commit: ONE work-unit commit on `feature/time-slider` — message follows repo style (Spanish conventional, no AI attribution). SHA full: `fed000476fd6bf7d38f6195f08c5590ee05f4784` (`fed0004`).
- Parent spot check (orchestrator): re-ejecución de `smoke_playback.py` → ALL PASS (23/23, incluido click mid → position=1500, drag → 2278, bar hidden/reset tras stop, items.json intacto).
- RDD off (default): `gentle-ai review assess` diff main..feature/time-slider → medium (executable_change app/ui/main_window.py), 197 líneas, review_due=under_budget. Sin ceremonia de review.
- Slider nuance: while dragging, the final landed value is Qt's own handle maths (super is called after our setValue), so a drag ending at x=3/4 lands at ~3/4 of the range (±~1-2 px offset); a plain click lands exactly at `sliderValueFromPosition`. This matches the spec (handling stays native).