# Editable transcript and translation notes

## Objetivo
Permitir editar libremente los textos de transcripción y traducción como documentos multilínea, guardarlos por medio y exportarlos sin reiniciar la inferencia.

## Problema / por qué
Whisper y la traducción producen una buena base, pero el usuario necesita corregir errores, añadir contexto o conservar solo una parte del contenido. Los paneles son actualmente de solo lectura, por lo que cualquier ajuste obliga a modificar el proceso y repetirlo.

## Decisiones confirmadas
- La edición será libre y multilínea; no se limitará a los segmentos temporizados.
- Cada medio conservará su texto editado independientemente.
- `TimedSegment` y sus timestamps permanecerán como metadatos originales de la inferencia.
- `Guardar TXT + SRT` conservará la exportación base con timestamps.
- Se añadirá una exportación separada del texto editado.
- Una nueva inferencia solicitará confirmación antes de sustituir ediciones existentes; solo se limpiarán las ediciones cuando la inferencia termine correctamente.

## Alcance
- Convertir ambos paneles en editores persistentes.
- Añadir autoguardado con debounce y volcado al cambiar de medio/cerrar la ventana.
- Proteger las actualizaciones programáticas (`setPlainText`, streaming y restauración) para que no sobrescriban notas.
- Guardar el texto editado en `MediaItem` y `data/items.json` sin perder el texto base ni los segmentos.
- Añadir `Guardar texto editado` con una salida TXT clara para ambos paneles.
- Mantener intacta la sincronización de scroll existente.
- Actualizar `README.md` con el flujo de edición y guardado.

## Restricciones
- Python 3.14.3, PySide6 6.11.2, Windows.
- No hay suite de tests automatizados; se añadirá verificación funcional offscreen.
- Sin dependencias nuevas.
- UI y documentación de la aplicación en español/inglés respectivamente, siguiendo los artefactos existentes.
- No tocar ni incluir `.codegraph/`.
- No hacer push ni PR; la entrega remota queda aplazada.

## Tareas
- [x] T1: extender modelos/repositorio con textos editados y operaciones de persistencia que distingan texto vacío de ausencia de edición.
- [x] T2: hacer editables ambos paneles, añadir autoguardado con guarda contra escrituras programáticas y confirmar antes de reemplazar notas.
- [x] T3: añadir exportación TXT del documento editado sin alterar TXT/SRT base.
- [x] T4: actualizar documentación y ejecutar compileall, smoke Qt offscreen, comprobaciones de persistencia y revisión del diff.

## Criterios de aceptación
- El usuario puede escribir, corregir, añadir y eliminar texto multilínea en ambos paneles.
- Los cambios se guardan automáticamente por `MediaItem` y sobreviven a seleccionar otro medio y reabrir la aplicación.
- Una restauración, inferencia o traducción no borra silenciosamente ediciones existentes; una nueva inferencia pide confirmación y solo reemplaza el estado editado si termina bien.
- Los segmentos y timestamps originales permanecen intactos.
- `Guardar TXT + SRT` sigue exportando el resultado base; `Guardar texto editado` exporta las notas actuales.
- La sincronización de scroll sigue funcionando y no introduce bucles de señales.
- La aplicación compila y el smoke offscreen valida edición, autoguardado, restauración, inferencia y exportación.

## Verificación
- `.venv\Scripts\python.exe -m compileall app`
- Smoke Qt offscreen con `QApplication`, selección de medios, edición, cambio de selección, reapertura de la ventana y exportación.
- Comprobación directa de persistencia JSON y de que los segmentos no cambian.
- `git diff --check`
- `gentle-ai review assess --cwd <repo> --json` si el estado del repositorio permite la evaluación.

## Progreso
- Rama: `feat/editable-transcript-notes`.
- Ruta: **delegated direct**; un escritor único implementó T1–T3 y el orquestador verificó T4.
- TDD: no configurado/no hay suite; se usaron checks funcionales offscreen.
- Delivery: `ask-on-risk`; el diff real alcanza 512 líneas cambiadas y requiere decisión de entrega antes del commit.
- Revisión nativa: `gentle-ai review assess` devuelve riesgo `medium`, `review_due=true` por `slice_budget_reached`; la preflight/revisión se ejecutará sobre el candidato final elegido.

## Evidencia
- `.venv\Scripts\python.exe -m compileall app`: PASS.
- Smoke Qt offscreen `transcript_editable_smoke.py`: PASS; cubrió edición multilínea, autosave de texto vacío, restauración al cambiar de medio, reapertura,legacy JSON, escrituras programáticas, cancelación/exito/fallo de inferencia, limpieza por lado, exportación editada, exportación base y scroll.
- `git diff --check`: PASS.
- `gentle-ai review assess --cwd <repo> --agent opencode --untracked-scope=select --intended-untracked=odd/tasks/editable-transcript-notes.md --expected-untracked-inventory=sha256:d4ab427371c3bdc0fc8d826319efd711837fb5e6f7a1fc53aaef1ea18ca2fcb5 --json`: riesgo `medium`, 6 rutas, 512 líneas, `review_due=true` (`slice_budget_reached`).
- `.codegraph/` queda excluido explícitamente y no se versionará.
- Smoke repetido por el orquestador: `SMOKE OK`.
- Commit/merge: pendientes de decisión de entrega.
