# Feature: Editor popup para anotaciones multilínea (feature/annotations-popup-editor)

## Objective
Al hacer clic en la celda de anotación (columna 1 de la lista de medios), abrir un cuadro emergente GRANDE (textbox multilínea) anclado ENCIMA de la fila, para que el usuario escriba anotaciones largas cómodamente. El texto se persiste igual que hoy (`items.json` por archivo).

## Problem
El editor inline actual (doble clic → `editItem`) es una línea pequeña: incómodo para anotaciones de varias líneas. El usuario pide un textbox emergente más grande, posicionado encima de la línea.

## Why now
`annotations-column` (main @ b3d6449) ya persiste anotaciones; solo falta mejorar la experiencia de edición multi-línea.

## Scope
- `app/ui/media_list.py` (refactor principal):
  - Nueva clase `_AnnotationEditorPopup(QFrame)` con flags `FramelessWindowHint | Popup`:
    - Con `QTextEdit` (multilínea, `setAcceptRichText(False)`), foco + `selectAll` al abrir.
    - Label de ayuda pequeño: "Ctrl+Enter guardar · Esc cancelar".
    - `closeEvent`: si NO fue cancelado → emite `save_requested(text)` (auto-guardado al cerrar por clic fuera o Ctrl+Enter).
    - Escape → `_cancelled = True` + `close()` (NO guarda).
    - Ctrl+Enter → `close()` (guarda vía closeEvent).
  - `MediaList._open_annotation_editor(item)`:
    - Guard si ya hay popup abierto (`self._annotation_popup is not None`).
    - Posiciona el popup ENCIMA de la fila (`visualItemRect(item)`): anclado al centro horizontal de la columna 1 (`visualRect(index.siblingAtColumn(1))`), y verticalmente `globalTopLeft.y() - size.height() - 6`; si no cabe (top de pantalla), lo coloca DEBAJO de la fila (`bottom + 6`). Clamp X/Y a `self.screen().availableGeometry()`.
    - `save_requested` → `_save_annotation(item, text)`: `repository.update_annotation(row, text)` + `item.setText(1, text)` con guard `_popup_saving` (evita doble persistencia vía `itemChanged`).
    - `closed` → `self._annotation_popup = None`.
  - `mouseReleaseEvent`: tras la detección del icono de play, si `indexAt(pos).column() == 1` y hay item → abrir el popup (clic simple en la celda de anotación).
  - `_on_item_double_clicked`: col 0 → alias dialog (igual); col 1 → `_open_annotation_editor(item)` (mismo popup; el guard evita duplicados).
  - Quitar `ItemIsEditable` de los flags (ya no hay editor inline); mantener `_NoEditDelegate` en col 0.
  - `_on_item_changed`: añadir `and not self._popup_saving` al guard existente.
- `app/ui/theme.py`:
  - QSS `QFrame#annotationPopup` (surface, borde, radius 8px) + `QLabel#annotationPopupHint` (muted, 11px).
  - El `QTextEdit` interno ya hereda la regla `QPlainTextEdit, QTextEdit` existente.
- `app/ui/main_window.py`:
  - Hint: "clic en la anotación: editarla" (en vez de "doble clic en la anotación: editarla").
- Sin cambios en models/repository/items.json schema.
- Sin nuevas dependencias runtime ni de test.

## Constraints
- Windows 11, Python 3.14.3, venv `.venv`, PySide6.
- Persistencia keyed por FILE (path) — igual que hoy.
- Conventional commits, sin atribución AI.
- TDD: sin suite → functional checks = smokes offscreen con TEMP `items.json` (nunca `data/` real).
- Forecast < 400 líneas → single work-unit commit en `feature/annotations-popup-editor`.

## Checklist
- [x] T1: popup editor (QFrame Popup + QTextEdit multilínea, posicionado encima de la fila, auto-guardado/Esc/Ctrl+Enter)
- [x] T2: clic simple col 1 abre popup; doble clic col 0 sigue con alias dialog; inline editing eliminado
- [x] T3: guard `_popup_saving` (sin doble persistencia) + repaint del item al guardar
- [x] T4: QSS popup + hint main_window actualizado
- [x] T5: smokes offscreen + work-unit commit

## Acceptance criteria
- Clic en la celda de anotación → aparece un textbox grande encima de la fila con el texto actual. ✅ (S5-S8; S9b: popup anclado ENCIMA en filas medias; S9a: fallback debajo cuando no cabe)
- Se pueden escribir varias líneas cómodamente (QTextEdit con wrap + scroll si excede). ✅ (S10: 3 líneas persistidas completas)
- Cerrar (clic fuera / Ctrl+Enter) guarda; Escape cancela sin guardar. ✅ (S10-S12, S17, S18)
- La anotación persiste en `items.json` y se restaura al recargar (sin dobles escrituras). ✅ (S10, S12: exactamente 1 write por guardado; S14 restore)
- Play icon, alias, menú contextual, drag & drop y selección siguen funcionando igual. ✅ (código del icono intacto; S2-S3; guardado por path robusto a reload)

## Verification evidence
- Work-unit commit: `feat: editor popup multilinea para anotaciones` — SHA `5fc68b4` (rama `feature/annotations-popup-editor`, 3 archivos, +183/−18; sin push, sin PR).
- Smokes offscreen (`QT_QPA_PLATFORM=offscreen`) con TEMP `items.json` + WAV fake en `%LOCALAPPDATA%\Temp\opencode\annotations-popup-smoke` (nunca `data/` real; verificado `git status data/` limpio).
- Smoke resumen — 20 checks ALL PASS:
  - S1-S4: item cargado, sin flag `ItemIsEditable`, 2 columnas, rect de columna válido.
  - S5-S8: clic en celda anotación → popup QFrame con flag Popup, visible, prellenado con el texto actual.
  - S9a: fila 1ª (sin espacio arriba) → popup DEBAJO de la fila. S9b: fila media → popup ENCIMA (y=7, fila top=133). S9c: popup dentro de `availableGeometry` de la pantalla.
  - S10-S12: guardado de 3 líneas persiste en repo, celda actualizada, EXACTAMENTE 1 write (sin doble persistencia vía itemChanged, guard `_popup_saving`).
  - S13: `_annotation_popup` liberado al cerrar. S14: reload restaura anotación multilínea.
  - S15-S16: reapertura con texto actual; S17: Escape cancela sin guardar; S18: Ctrl+Enter (save_and_close) guarda.
  - S19-S20: QSS `QFrame#annotationPopup` + `QLabel#annotationPopupHint` presentes en tema oscuro y claro.
- Robustez extra detectada por el propio smoke: el guardado captura el PATH del archivo (no el objeto item), de modo que un reload de la lista mientras el popup está abierto no crashea (S10 tras reload en S9). Bug real corregido antes del commit.
- Limite conocido (no cubierto offscreen): interacción real con foco/teclado en ventana Popup nativa (clic fuera para cerrar y guardar); comportamiento Qt estándar, riesgo residual mínimo.