# Sincronización de scroll de transcripción y traducción

## Objetivo
Añadir un checkbox que sincronice el desplazamiento vertical de los paneles de transcripción y traducción cuando ambos contienen el resultado de un mismo medio.

## Problema / por qué
Los dos paneles se muestran lado a lado, pero actualmente cada scrollbar es independiente. Cuando existen transcripción y traducción, revisar ambos obliga a mantener posiciones visuales alineadas manualmente.

## Alcance
- Añadir un checkbox `Sincronizar scroll` en la barra de controles.
- Sincronizar proporcionalmente el valor vertical de ambos paneles usando el rango de cada scrollbar.
- Actual ambos paneles al desplazar cualquiera de los dos, con protección contra señales recursivas.
- Desactivar la sincronización cuando el checkbox no esté marcado.
- Mantener el checkbox deshabilitado mientras falte transcripción o traducción para el medio seleccionado.
- No modificar la persistencia ni el contenido de los textos.

## Restricciones
- Python 3.14.3, PySide6 6.11.2, Windows.
- No hay suite de tests automatizados; la verificación será un smoke offscreen.
- Los artefactos técnicos y el texto de UI nuevos estarán en español, siguiendo el contexto existente de la aplicación.
- Sin dependencias nuevas.
- Commit único de trabajo con mensaje convencional, sin atribución de IA.

## Tareas
- [x] T1: añadir el checkbox y su estado habilitado solo cuando ambos paneles tengan texto.
- [x] T2: conectar ambos scrollbars y sincronizar por proporción con guarda de realimentación.
- [x] T3: smoke offscreen con textos de longitudes distintas, ambos sentidos, desactivación y estado incompleto; revisión del diff y commit.

## Criterios de aceptación
- Con ambos textos presentes, marcar el checkbox hace que al mover cualquiera de los dos scrollbars el otro se mueva a la misma proporción vertical.
- Desplazar el panel izquierdo actualiza el derecho y viceversa.
- Desmarcar el checkbox detiene la sincronización.
- Sin ambos textos, el checkbox no permite activar una sincronización engañosa.
- El contenido y la persistencia de transcripción/traducción no cambian.

## Verificación
- `python -m compileall app`
- Smoke Qt offscreen con `QApplication`, `MainWindow`, textos de longitudes distintas y aserciones sobre los rangos/valores de ambos scrollbars.
- Revisar `git diff --check`, `git diff --stat` y el estado del worktree antes del commit.

## Progreso
- Rama: `feature/synchronized-scroll`
- Ruta: **delegated direct** para la implementación; el cambio de comportamiento queda en `app/ui/main_window.py`.
- TDD: no disponible/no hay suite; se usarán checks funcionales offscreen.
- Delivery: `ask-on-risk`; el cambio previsto queda muy por debajo de 400 líneas.

## Evidencia
- `python -m compileall app`: PASS con `.venv\\Scripts\\python.exe` (PySide6 6.11.2).
- Smoke real offscreen con `QApplication` y `MainWindow`: PASS; textos con longitudes distintas, sincronización en ambos sentidos, alineación inicial desde el panel activo, desactivación, guarda de realimentación y estado incompleto.
- `git diff --check`: PASS.
- `gentle-ai review assess --cwd <repo> --json`: `high` por archivos untracked sin inventario explícito; requiere el preflight nativo y no representa un fallo de la implementación.
- Commit de trabajo: `373564f` (`feat(ui): synchronize transcript scroll panes`).
- Revisión nativa: el candidato fue aceptado para revisión, pero el lens `review-reliability` no pudo ejecutarse porque el transporte del proveedor rechazó la tarea; se registró `provider_task_unavailable` y la transacción terminó en `unachievable_lens_slot`. No se высок pretending que la revisión pasó.
- Reversión: revertir `373564f`; no afecta persistencia ni contenido.
