# Feature: Annotaciones por archivo (columna editable en la lista) (feature/annotations-column)

## Objective
Convertir la lista de medios en una tabla de DOS columnas — **Archivo** | **Anotación** — donde el usuario puede añadir una anotación propia por archivo editando la celda directamente en la lista. La anotación se persiste por archivo en `data/items.json` y se restaura al recargar.

## Problem
La lista solo muestra el nombre/alias del archivo. El usuario quiere notas propias asociadas a cada medio (contexto, recordatorios, resultado esperado…) visibles de un vistazo y persistidas por archivo.

## Why now
Todas las features de salida/playback/seek están cerradas; la lista es ahora el punto de entrada natural y carece de espacio para notas del usuario.

## Scope (decisión del usuario: columna editable en la lista)
- `app/core/models.py`:
  - Añadir `annotation: str = ""` a `MediaItem` (default `""` → los `items.json` viejos cargan sin cambios vía `MediaItem(**entry)`).
- `app/storage/repository.py`:
  - Añadir `update_annotation(index: int, annotation: str) -> None` → setea y persiste (estilo `update_alias`).
- `app/ui/media_list.py` (refactor principal):
  - `MediaList(QListWidget)` → `MediaList(QTreeWidget)` con 2 columnas, cabeceras `["Archivo", "Anotación"]`.
  - Configuración de tabla: `setRootIsDecorated(False)`, `setIndentation(0)`, `setUniformRowHeights(True)`, `setDragDropMode(DropOnly)` (sigue aceptando URLs externas; sin drag interno), `setSelectionMode(SingleSelection)`, `setContextMenuPolicy(CustomContextMenu)` (igual).
  - Items: flags `ItemIsSelectable | ItemIsEnabled` (columna 0 **no** editable inline; el alias se sigue editando con el diálogo actual). Columna 0 = icono play + alias; columna 1 = anotación.
  - `reload()`: por cada `entry` → `QTreeWidgetItem`, `setData(Qt.UserRole, entry.path)` (col 0), `setText(0, entry.alias)`, `setText(1, entry.annotation or "")`, tooltip igual, icono play; `addTopLevelItem`.
  - `selected_path()`: igual con `currentItem()`.
  - `set_playing` / `set_stopped` / `_item_for_path`: iterar `topLevelItemCount()` / `topLevelItem(row)`.
  - `mouseReleaseEvent`: misma lógica de detección del icono de play (usa `visualItemRect(item)` + `SE_ItemViewItemDecoration`; el fallback de rect nulo se mantiene) → emite `play_requested`. El clic normal en cualquier columna conserva el comportamiento por defecto (selección).
  - `itemDoubleClicked(item, column)`: `column == 0` → `_edit_alias(item)` (diálogo actual); `column == 1` → `self.editItem(item, 1)` (edición inline forzada).
  - `itemChanged(item, column)`: si `column == 1` y NO estamos en `reload()`/carga (`self._loading` guard) → `self.repository.update_annotation(self.indexOfTopLevelItem(item), item.text(1))` (persistencia al commit de la edición, Enter o pérdida de foco). Emitir `items_changed` NO es necesario aquí (no cambia selección ni botones).
  - Menú contextual: `self.row(item)` → `self.indexOfTopLevelItem(item)`; resto igual (Cambiar alias… / Copiar ruta / Eliminar de la lista).
  - `add_paths`, drag&drop de URLs: iguales.
  - Anchos: `header().setSectionResizeMode(0, QHeaderView.Interactive)` (inicial ~60%) y `header().setSectionResizeMode(1, QHeaderView.Stretch)`; `setColumnWidth(0, ...)` razonable.
- `app/ui/theme.py`:
  - Añadir QSS para `QTreeWidget` (heredar el aspecto actual de `QListWidget`: surface, borde, radius, padding) y `QTreeWidget::item` (hover/selected igual que `QListWidget::item`).
  - Añadir `QHeaderView::section` con colores del palette (bg `$surface_alt`, borde `$border`, texto `$text_muted`, padding) para ambas columnas.
  - Mantener la regla `QListWidget` existente (no romper otros usos; el widget ya no es QListWidget pero la regla puede quedarse o reemplazarse por la de QTreeWidget).
- `app/ui/main_window.py`:
  - Actualizar el hint: mencionar que el doble clic en el nombre edita el alias y el doble clic en la anotación la edita.
  - Todo lo demás (selección → restauración de paneles, playback, seek, save) se mantiene igual; `itemSelectionChanged` y `selected_path()` funcionan igual en QTreeWidget.
- Compatibilidad: `data/items.json` viejos cargan sin crash; `remove()` borra entrada completa (ya lo hace); nuevos `annotation` solo se escriben al editar.
- Sin nuevas dependencias runtime ni de test.

## Constraints
- Windows 11, Python 3.14.3, venv `.venv`, PySide6.
- `items.json` compatible con entradas viejas (`MediaItem(**entry)` + defaults).
- Persistencia keyed por FILE (path), independiente del alias.
- Conventional commits, sin atribución AI.
- TDD: sin suite → functional checks = smokes offscreen con TEMP WAV y TEMP `items.json` (nunca los reales).
- Forecast < 400 líneas → single work-unit commit en `feature/annotations-column`.

## Checklist
- [ ] T1: models: `MediaItem.annotation = ""` (default); repo: `update_annotation`
- [ ] T2: media_list: MediaList → QTreeWidget 2 columnas (Archivo | Anotación), reload/select/play icon/context/dnd intactos
- [ ] T3: media_list: edición inline columna 1 (doble clic → editItem; itemChanged → persistir con guard _loading)
- [ ] T4: theme: QSS QTreeWidget/QHeaderView; main_window hint actualizado
- [ ] T5: smokes offscreen + work-unit commit en feature branch

## Acceptance criteria
- La lista muestra dos columnas: Archivo | Anotación, con cabeceras visibles.
- Doble clic en la celda de anotación → edición inline; Enter/pérdida de foco guarda.
- La anotación persiste en `items.json` por archivo y se restaura al recargar la lista.
- El icono de play, el alias, el menú contextual, drag & drop y la selección funcionan igual que antes.
- `items.json` viejos cargan sin cambios.

## Verification evidence
- (se rellena tras la implementación)