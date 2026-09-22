# Feature: UI Professional Polish (feature/ui-polish)

## Objective
Give the app a professional, modern Qt look: QSS theming with a dark theme by default plus a light theme toggle, a polished layout, and consistent styling across main window, media list, controls, progress, and output.

## Problem
The UI is functional but plain (native widgets, no styling). The user wants a professional look: modern QSS, dark/light themes, polished layout.

## Why now
Core functionality (transcription, translation, CUDA, diarization, launcher) is done and merged. UI polish is the remaining visible improvement.

## Scope
- New `app/ui/theme.py`: full QSS stylesheet engine with `DARK` and `LIGHT` palettes (backgrounds, surfaces, text, accent, buttons, list, progress bar, output, scrollbars, menus, tooltips, dialogs).
- `app/main.py`: apply dark theme by default at startup.
- `app/ui/main_window.py`: add a dark/light toggle button (sun/moon) in a small header row; persist choice via `QSettings`; keep existing behavior and signals intact.
- `app/ui/media_list.py`: only if needed for consistent drop hint / selection styling (QSS on QListWidget is global, mostly no code change).
- Verify: offscreen smoke test (app launches, toggle switches palette, no errors) and, when possible, a quick visual check on the real desktop window.

## Constraints
- Windows 11, Python 3.14.3, venv `.venv`, PySide6.
- No new runtime dependencies; QSS only.
- Keep existing behavior: drag & drop, alias edit, context menu, transcribe/translate buttons, diarize checkbox, progress, status label, worker threading.
- Conventional commits, no AI attribution.
- TDD: no test suite in repo; functional checks = GUI offscreen smoke + manual visual check.
- forecast <= 400 authored lines → single work-unit commit; no chained PR needed.

## Checklist
- [X] T1: Create `app/ui/theme.py` with DARK/LIGHT QSS palettes and a `apply_theme(app, dark: bool)` helper
- [X] T2: Apply dark theme by default in `app/main.py`
- [X] T3: Add theme toggle button in `main_window.py`, persist via QSettings, default dark
- [X] T4: Offscreen smoke test: launch, toggle theme, exercise transcribe/translate disabled state, no exceptions
- [X] T5: Work-unit commit on feature branch (conventional commit) + record commit identity — `8ed451528cbba8e764f6287c5ed401b42fe10ea6` (`feat: UI profesional con temas oscuro/claro (QSS)`, 3 files, 408 insertions)

## Acceptance criteria
- App launches with dark theme by default.
- Toggle switches to light and back instantly; choice persists across launches.
- All widgets look intentional: consistent padding, accent color, hover states, styled list/progress/output/scrollbars.
- No regression in existing interactions (drag&drop, alias, context menu, transcribe/translate flow).
- Offscreen smoke passes with zero exceptions.

## Verification evidence
- Writer (general, perfil estándar): smoke offscreen OK oscuro (cero excepciones); toggle real dark→light→dark OK (QSS `#1b1d23` ↔ `#f4f5f8`, persistencia QSettings, botones disabled sin selección); `import app.main` OK.
- Parent spot check: `QT_QPA_PLATFORM=offscreen` launch + `btn_theme` presente → `SPOT CHECK OK oscuro`.
- RDD off (default): `gentle-ai review assess` sobre el diff → medium (executable_change app/main.py), 409 líneas, review_due=slice_budget_reached. No se inicia ceremonia de review (RDD off); entrega sigue política ordinaria del repo.
- Pendiente: vistazo visual en desktop real (paleta LIGHT, checkbox checked sin checkmark, glifos ☀/☾ según tipografía).