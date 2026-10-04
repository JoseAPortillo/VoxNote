# Slice de reproducción y de ejecución por medio

## Objetivo
Un único intervalo `(inicio, fin)` por medio, con dos handles arrastrables `■>` y `<■` sobre el timeslider. Ese intervalo persiste, acota lo que se reproduce y acota lo que se transcribe y traduce. Es la misma fuente de verdad para las dos cosas.

## Problema / por qué
El diseño actual de marcas (`marks`) tiene tresifos que el uso real no justifica:

1. **Se consumen.** `Pipeline._consume_marks` (`app/services/pipeline.py:170`) borra las marcas tras una ejecución correcta, porque fueron el input de esa ejecución. Marcaste un tramo, transcribiste, y tus marcas desaparecieron. Para sacar una segunda variante hay que marcar otra vez desde cero.
2. **Lo que se ve no es lo que se usa.** La geometría de las marcas la fija el medio cargado (`_playing_path`), pero la ejecución se keyea a la fila seleccionada (`app/ui/main_window.py:454-457`). Cuando divergen, `ranges = None` y la transcripción sale **completa sin acotar**; solo lo avisa un texto de estado (`:481`).
3. **N marcas donde solo hace falta una.** `MediaItem.marks` es una lista de pares disjuntos, con normalización, orden y fusión de solapes, para algo que el usuario piensa como *el* trozo que le interesa.

Medido: los 5 medios reales del `data/items.json` tienen todos `marks: null`. La funcionalidad de tramos múltiples **nunca se usó**. Se retira limpia, sin datos que migrar.

## Decisiones de producto (mantenedor, 2026-10-04)
1. **Un slice por medio.** Handles `■>` (inicio) y `<■` (fin) arrastrables sobre el timeslider. Se retira el menú contextual y la tira de llaves `{`/`}`.
2. **Una sola fuente de verdad.** El mismo par acota reproducción **y** ejecución/traducción. Sin slice, se procesa el archivo completo.
3. **Fin del slice:** la reproducción vuelve al inicio y queda en pausa. Toggle `⟳` en los controles de cada reproductor para loop A-B infinito.
4. **Seek libre, play respeta.** El cursor se mueve por toda la línea; pulsar play entra siempre por el inicio del slice y nunca pasa del final.
5. **Persistente.** Las marcas no se consumen. Nunca las borra una ejecución.

## Lo que este diseño elimina por construcción
- **La divergencia como defecto.** Con un par por medio y una clave, la ejecución toma el slice del medio que ejecuta. Ya no hay dos keyedias que puedan desalinearse.
- **La duplicación por warmup.** Defecto medido en T10: dos rangos separados por menos de `RANGE_WARMUP` (15 s) repetían la frase de la frontera — hasta 20 palabras duplicadas con huecos de 0–1 s. Solo existía por haber N rangos disjuntos. Con un slice único no hay frontera entre rangos que atravesar.

## Lo que NO arregla
La voz de fondo a −32 dBFS bajo música ambiental (ratio de 15 dB) es un defecto **del motor**, medido y aceptado el 2026-09-28. `RANGE_WARMUP` sigue aplicando al slice único y sigue empeorando ese caso concreto (punto 16 de `in-out-marks.md`).

## Alcance
- `MediaItem.slice` sustituye a `MediaItem.marks`. Un par, no una lista.
- Handles arrastrables sobre el groove, con hit-test propio y recorte al mover.
- Reproducción acotada al slice en los dos reproductores, con toggle `⟳`.
- La ejecución toma el slice del medio seleccionado; las marcas dejan de consumirse.
- `README.md`: la sección de marcas se sustituye por la de slice.
- Se retira: menú contextual, tira de llaves, `pending_in`, `mark_pixel_rects`, `pending_pixel_rect`, `_hit_index`, `clear_marks`, `_consume_marks`.

## MINA CONFIRMADA — la migración puede vaciar la biblioteca entera
`MediaRepository.load` hace `MediaItem(**entry)` (`app/storage/repository.py:32`). Si un `items.json` antiguo trae la clave `marks` y `MediaItem` ya no la tiene, eso es un `TypeError` — y el `except (json.JSONDecodeError, TypeError, ValueError)` de `:33` **lo traga**: la biblioteca se carga vacía y los bytes originales se apartan a `items.json.corrupt`. No hay crash, hay pérdida silenciosa.

Los 5 medios reales tienen `marks: null`, así que el archivo actual **no** dispara esto. Pero cualquier `items.json` guardado antes de este cambio sí. **T1 debe traducir la clave explícitamente antes de construir el `MediaItem`, nunca dejar que `**entry` la encuentre.**

## Restricciones
- Python 3.14.3, PySide6 6.11.2, Windows. **Sin dependencias nuevas.**
- PySide6 6.11.2 ya dio cuatro defectos reales en el trabajo anterior (`QColor` no acepta `rgba()`, `mapTo` entre hermanos tumba el proceso, `QStyle.SC_SliderGroove` no es utilizable, `setSource` con la misma URL no emite nada). Verificar antes de asumir.
- El índice `.codegraph/` no se commitea; está en `.git/info/exclude`.
- No hay remoto GitHub: los commits de unidad de trabajo van a una rama de feature y el delivery lo decide el mantenedor.

## Decisiones de diseño derivadas
- **Los handles se pintan siempre; el slice existe solo si se movió uno.** Sin slice, los handles se dibujan atenuados en 0 y en la duración, y el estado persistido sigue siendo `None`. El contrato `None` = archivo completo queda intacto (los motores leen una lista **vacía** como "archivo completo", así que `[]` nunca puede ser el estado "sin slice"). Arrastrar un handle fuera de los extremos crea el slice; devolver **ambos** a los extremos lo vuelve a borrar. Un gesto, sin bandera extra ni menú.
- **`RANGE_WARMUP` se mantiene** (15 s). Es lo que recuperó +45 % de caracteres en T10.
- **El estado del loop `⟳` es de UI, no se persiste.** Es una preferencia de escucha, no un dato del medio.
- **La posición mostrada se recorta al fin del slice** cuando el watchdog salta, para que la etiqueta de tiempo nunca mienta.

## Tareas
- [ ] T1: modelo y persistencia de un slice único. `normalize_slice()` reemplaza a `normalize_marks()`; `MediaItem.slice`; `update_slice()`; **traducción explícita de la clave `marks` legacy en `load()` antes de `MediaItem(**entry)`**; se borran `clear_marks` y `normalize_marks`. Pytest para `normalize_slice`, el round-trip y la carga legacy.
- [ ] T2: handles en el slider. `HANDLE_HIT_PX` + hit-test; el press sobre un handle **no busca** (colisiona con el `mousePressEvent` actual de `seek_slider.py:154`, que hace seek); recorte a `[0, duración]` y a `MIN_SLICE_SECONDS`; pintado de handles activos y atenuados; se borran menú, tira de llaves, `pending_in`, `mark_pixel_rects`, `pending_pixel_rect`, `_hit_index`. Se reutilizan `_x_at_seconds` (`:216`) y `_position_seconds_at` (`:324`).
- [x] T3: reproducción acotada en los dos reproductores. Play entra por el inicio; watchdog que para o hace loop en el fin; toggle `⟳` checkable en la fila de control de audio y de video; recorte de la posición mostrada al fin.
- [x] T4: la ejecución usa el slice y deja de consumirlo. Se borran `_consume_marks` y `clear_marks`; `_run` toma `item.slice` de la **fila seleccionada**; el texto de divergencia pasa a ser una nota corta de "estás viendo X, se ejecutará Y" (ya no es un fallo de correctitud); el denominador de progreso usa la duración del slice.
- [ ] T5: verificación y documentación. Pytest de la lógica pura **y** de los widgets offscreen (`QT_QPA_PLATFORM=offscreen`), de modo que la evidencia vive en el repo y no en `%TEMP%`; `compileall`; `git diff --check`; sección de `README.md` en inglés.

## Criterios de aceptación
- Con un slice activo, pulsar play entra por el inicio y no pasa del final; con loop `⟳` activo, repite; sin loop, vuelve al inicio en pausa.
- El cursor se puede mover freely por toda la línea con slice activo, y pulsar play sigue entrando por el inicio.
- El slice persiste al reiniciar la app, y sobrevive a cambiar de medio y volver.
- Sin handles movidos, el estado es `None` y la ejecución procesa el archivo completo, idéntico a hoy.
- Devolver ambos handles a los extremos borra el slice.
- La ejecución sobre el medio seleccionado usa el slice de ese medio.
- Un `items.json` con `marks` antiguo carga sin perder entradas y traduce la clave.
- Los timestamps resultantes son absolutos sobre el medio original y el SRT es válido sobre el medio original.
- Sin slice, el resultado es idéntico al comportamiento actual.

## Verificación
- **Comando autoritativo:** `.venv\Scripts\python.exe -m unittest discover -s tests`. El intérprete del proyecto (`.venv`) tiene **PySide6 6.11.2 pero no pytest**; el intérprete base (`E:\Python314`) tiene **pytest 9.0.3 pero no PySide6**. Los dos son disjuntos, así que pytest por sí solo no puede correr los checks de Qt, y `.venv` es el intérprete que ejecuta la app. `unittest` es stdlib y corre en `.venv`. El estilo de tests del repo ya es `unittest.TestCase`, no fixtures de pytest.
- `python -m pytest -q` (intérprete base) — misma suite, opcional.
- Widgets y reproductores con `QT_QPA_PLATFORM=offscreen`.
- `.venv\Scripts\python.exe -m compileall app`
- `git diff --check`

## Progreso
- Ruta: **delegated direct**. El cambio toca modelo, persistencia, slider, los dos reproductores y el pipeline: 2+ archivos no triviales, así que la implementación va delegada a un escritor acotado. La lectura que prepara la escritura va con el escritor, no aparte.
- TDD: **resuelto por decisión del orquestador (2026-10-04)**, no por configuración del proyecto — no hay registro `sdd-init/transcript` ni config de test en el repo. Runner autoritativo: `.venv\Scripts\python.exe -m unittest discover -s tests`. Se aplica TDD a la lógica pura (`normalize_slice`, geometría, matemática de posición acotada) y checks offscreen al comportamiento de Qt, que no es unit-testeable. Motivo: el fallo de T8 fue una expectativa escrita sobre el orden de entrada cuando `set_marks` normaliza en la asignación (`seek_slider.py:192`) — clase de defecto que un test de la normalización habría pillado antes.
- **Corrección de T1 (2026-10-04):** este documento afirmaba que el venv del proyecto tenía pytest 9.0.3. Es falso, y T1 lo refutó ejecutándolo. La versión era correcta; la ubicación no. Ver "Verificación".
- Precedente del feature anterior: la evidencia vivía en `%TEMP%` y se perdió. Aquí los checks offscreen se quedan en el repo.
- Delivery: por decidir. Forecast ≈ 1000–1200 líneas authored, **por encima del presupuesto de ~400**. Sin remoto GitHub, los PRs encadenados no son ejecutables; el precedente (`in-out-marks`) fue `exception-ok` con `size:exception` aprobada por el mantenedor.
- Estimación por tarea: T1 ≈ 180, T2 ≈ 350, T3 ≈ 250, T4 ≈ 120, T5 ≈ 250.

## Registro por tarea

### T1 — modelo y persistencia (cerrada, `d224a0a`)
661 líneas authored en 5 rutas (forecast: 180 — el test pesa 276 líneas y el doble de lo previsto).

**La mina se reprodujo antes de arreglarla.** Con `load()` sin traducir la clave legacy, sobre un `items.json` de 3 entradas todas con `marks`: **0 entradas cargadas**, `items.json.corrupt` escrito, y `items.json` dejado conteniendo `[]`. Pérdida total y silenciosa, exactamente como predice "MINA CONFIRMADA".

**Decisiones del escritor que el documento no pedía:** rechazo de booleanos (`float(False) == 0.0` haría de `[false, true]` un slice de 0–1 s que nadie puso); guarda explícita de `NaN`/`inf` (un `end` NaN sobrevive a toda comparación de orden y llegaría a los motores como un rango que no pueden decodificar); `load()` construye entrada por entrada con su propia guarda, así una fila mala cuesta esa fila y no la biblioteca; y una fila descartada **igual guarda sus bytes crudos**, porque `__init__` llama `save()` justo después de `load()` y esa copia es la única que queda.

**Verificación observada:** RED `ImportError: cannot import name 'normalize_slice'`; GREEN `79 passed, 26 subtests` — 41 preexistentes de `tests/asr/` intactos, 38 nuevos. `.venv\Scripts\python.exe -m unittest discover -s tests`: `Ran 79 tests ... OK`. `compileall app`: exit 0. `git diff --check`: limpio. Re-ejecutado por el orquestador, los cuatro con el mismo resultado.

**Riesgo nativo:** `medium` por `executable_change` en `app/core.models.py`, `review_due: true` por `slice_budget_reached`. El mantenedor **declinó la revisión de este candidato** (`consent: declined_this_candidate`, sin autoridad ni recibo creado). El rechazo es por candidato, no un apagado global.

**Rama medio rota a propósito:** `app/ui/seek_slider.py:45` importaba `normalize_marks` (ya no existe) y `app/services/pipeline.py:192` llamaba a `clear_marks` (borrado en T1). T2 retiró el primero y T4 el segundo. **La rama está entera desde T4**: reproducción acotada y ejecución con slice, las dos funcionando.

### T2 — handles arrastrables en el slider (cerrada)

**Dos defectos REALES de producto, encontrados por el escritor y confirmados por el orquestador con una sonda independiente:**

1. **El handle de fin era inalcanzable.** `_x_at_seconds` devuelve un **borde izquierdo**, que es lo correcto para una banda y lo incorrecto para un punto: en el final de la línea respondía columna `678` en un widget de 678 columnas cuya última columna dibujable es la `677`. Es decir, un píxel fuera del widget. Arrastrar el handle de fin al último columna mapeaba `59.91 s` sobre un archivo de 60 s, `slice()` nunca devolvía `None`, y **el gesto de "devolver ambos handles a casa para borrar el slice" no se podía completar**. Arreglo: `handle_x` fija la columna dentro del groove y el drag hace snap al borde DESPUÉS del clamp. Era un off-by-one heredado de la era de las marcas, donde `MIN_MARK_WIDTH_PX` lo compensaba y ya no queda nadie que compense.

2. **El handle se tragaba su propia banda en tema claro: 1.97:1.** La causa no era el alpha: `mark_fill` `#b35c3700` y `mark_edge` `#5c3700` son **el mismo tono**, separados solo por alfa. Compuestos, la banda caía en `#806642` y la barra opaca en `#5c3700`. Esos valores se midieron en la era de las marcas para una marca **sola** sobre el groove; el slice apila una barra opaca sobre esa misma banda translúcida, una composición que no existía. Corregido a `#a65c3700` / `#2e1b00`.

**Lo que el escritor NO hizo, y también importa:** no bajó el piso de contraste de la banda para disimular el defecto. El handle es un límite interactivo → 3.0 (WCAG 1.4.11). La banda es un indicador translúcido cuyo 1.86 en tema oscuro es preexistente y deliberado → piso 1.8, que sigue rechazando el `#4dde8f18` invisible (1.21). El defecto real (handle contra banda) queda clavado en 3.0.

**Y una lección sobre el propio test:** los tests de pintado del escritor construían el widget contra la paleta Fusion **sin estilos**, así que `apply_theme` nunca se leía y las propiedades de color no se ejercitaban — el defecto de contraste era invisible para la suite. Añadió `TestPaintInBothThemes`, que aplica ambos temas y mide los tres ratios sobre píxeles renderizados.

**De las 9 pruebas rojas iniciales, 8 eran comprobaciones mal escritas del propio escritor** (mayúsculas/lowercase, `QRect.right()` inclusivo, fixtures cuyo "no cruzar" limitaba antes que la línea de tiempo, casos de empate cuyo precondición no se cumplía, y asserts de milisegundos exactos que en realidad medían el *page step* de QSlider) y 1 era el defecto de producto. Se investigó cada una con sonda propia en vez de tocar producción para que un test pasara.

**Constantes:** `HANDLE_HIT_PX = 6` (radio generoso: los handles son de ~4 px, así que 6 da margen sin ambigüedad con el playhead) y `MIN_SLICE_SECONDS = 0.1`.

**Decisiones de diseño que el documento fijó y el código respeta literalmente:**
- `slice()` **deriva** `None` de que los handles estén en los extremos; no es un estado almacenado. Por eso "sin slice" y "borrado" son la misma cosa.
- `_clamp_to_timeline` **no** aplica `MIN_SLICE_SECONDS`: un slice guardado a mano tiene que volver exactamente como se guardó. El mínimo es invariante del **gesto**, y lo impone el drag. Esa distinción es lo que impide que el slider "corrija" datos que el usuario no tocó.
- El pintado atenúa por alfa sobre **un** color, así los dos estados no pueden separarse en tono, y atenúa por "existe slice", no por posición: el handle que sigue en el extremo se lee activo cuando el otro ya se movió.

**Verificación observada:** RED `ImportError: cannot import name 'normalize_marks'`; GREEN `138 tests` = 79 preexistentes (41 `tests/asr/` + 38 `tests/core/`) + 59 nuevos. `.venv\Scripts\python.exe -m unittest discover -s tests`: `Ran 138 tests ... OK`. `python -c "import app.ui.seek_slider, app.ui.main_window, app.ui.video_player_window"`: **OK — la app vuelve a importar su UI, que era el objetivo**. `compileall -q app`: exit 0. `git diff --check`: limpio. Los cuatro re-ejecutados por el orquestador con el mismo resultado.

**Defecto de formato encontrado y corregido por el orquestador:** la primera línea del bloque de comentario de `main_window.py:468` había quedado en la columna 0. Legal en Python (los comentarios no participan del indentado, por eso los imports pasaban) pero descuidado.

**Fuera del alcance estricto, justificado:** `theme.py` cambió de color. Renombrar las claves de propiedad quedó diferido, pero corregir un color para arreglar un defecto de contraste que esta tarea introdujo está dentro del alcance —y `markFillColor`/`markEdgeColor` los consume solo el slice.

**Pendiente a propósito:** `app/services/` intacto. `pipeline.py:192` sigue llamando a `clear_marks` (borrado en T1) **hasta T4**, que lo retira. Resuelto en T4.

### T3 - reproducción acotada al slice en ambos reproductores (cerrada)

**La decisión va primero, el cableado después.** El riesgo de acotar no es la señal: es la DECISIÓN en la frontera. Si un reproductor reporta 4.999 s en un slice que acaba en 5 s, ¿sigue, salta o se detiene? Eso es función pura de tres números, así que se escribió como una (`resolve_slice_position`) y se probó exacta, sin player ni ventana.

**El watchdog que pedía el diseño, y por qué no es opcional.** La primera versión aplicará la frontera solo desde `positionChanged`, y eso NO cumple lo que este documento pide. `QMediaPlayer` emite sus cambios de posición en un intervalo que **no documenta**, así que acotar únicamente con ellas deja el final del slice holgado por un hueco desconocido y audible en cada slice. Por eso hay `QTimer` de 100 ms (`SLICE_WATCHDOG_INTERVAL_MS`) que consulta `player.position()` directamente: el rebasamiento queda acotado por ESE intervalo, al precio de diez comparaciones de enteros por segundo. Es exactamente el "watchdog" que pedía el diseño desde el principio.

Dos llamadores, un solo cuerpo (`_enforce_slice_boundary`): la señal del player y el polling del timer. Comparten el cuerpo porque si no acabarían discrepando sobre qué significa el final, y esa es la clase de defecto que este proyecto viene corrigiendo desde T1.

**El watchdog se desarma en todos los finales, no solo en el suyo:** pausa manual (los dos caminos de pausa), `_stop_playback`, `EndOfMedia` (que ya pasaba por `_stop_playback`) y el propio fin de slice sin loop. Con loop encendido queda armado a propósito, porque es lo que sostiene el rebobinado. Quedaría armado solo en un caso: `playbackState()` sigue asincrónico tras `play()`, y ese estado ambiguo no es seguro para auto-desarmarse.

**Doshelpers, no uno, porque son dos preguntas distintas.** `resolve_slice_position` recibe la posición que el player YA tiene mientras corre. `slice_entry_position` responde dónde EMPEZAR ante un play deliberado, cuando el player puede estar aparcado legítimamente en cualquier sitio. El seek es libre —el usuario puede dejar el player pasado el final a propósito—, así que solo el play es una petición de escuchar el slice. Confundirlas habría hecho que play arrancara desde el sitio equivocado.

**Dos decisiones de frontera que merecen nombre, porque aunque parezcan arbitrarias no lo son:**
- El final es `end`, no `end - 1`. El milisegundo es la resolución del propio player; restarle uno recortaría la cola de todos los slices.
- "En o más allá del final" es UN caso, no dos. El player nunca para exactamente en la frontera: reporta a su propia cadencia, así que el primer informe posterior al final ya está pasado. Manejar solo `== end` dejaría el medio sonar de más.

**Orden que importa en la corrección:** rebobinar PRIMERO, pausar después. Pausar primero dejaría al player emitir un informe más allá del final, y el loop rebotaría contra el final en vez de asentarse en el inicio.

**Defecto encontrado por el orquestador, no por los tests:** el botón `⟳` era un `QPushButton` checkable SIN ninguna regla `:checked` para `#loopButton` en `theme.py`. Todos los tests de estado pasan igual sobre un botón que alterna con **cero cambio visible**: `isChecked()` nunca consulta la hoja de estilos. El usuario hacía clic, el modelo decía "en bucle" y la pantalla se veía idéntica. Se añadió la regla accent (misma superficie que `#playButton:checked`, para que "encendido" se lea igual en ambas ventanas) y el test que lo ata muestrea **píxeles renderizados** en los dos temas.

**Y ese test se verificó rompiendo la regla a propósito:** con `#loopButton:checked` renombrado a un selector inexistente, el guard falla en ambos temas (`#33353f == #33353f` en oscuro, `#ffffff == #ffffff` en claro). Un guard que nunca falla no ata nada. Muestreados con la regla puesta: oscuro `#33353f` → `#4f8cff`, claro `#ffffff` → `#2f6fed`, el accent real en cada uno.

**Contraste del glifo, medido y NO arreglado a propósito:** texto `$accent_text` (`#ffffff`) sobre `$accent` mide **3.22:1 en oscuro** y 4.55:1 en claro. El oscuro queda bajo el piso de 4.5:1 para texto normal, pero es exactamente el par de tokens que ya publican `#primaryButton` y `#playButton:checked`; subirlo exige cambiar el accent de toda la paleta y mover controles fuera de alcance. Se registra como valor de casa preexistente y compartido, no se disimula bajando el umbral del test. El glifo se recolorea por QSS (a diferencia del icono del play, que la ventana reconstruye) porque QSS alcanza el texto de un botón plano.

**Decisiones que el documento fijó y el código respeta:**
- El loop se lee de `seek_slider`, NO de la fila seleccionada: la reproducción se acota a lo que el slider muestra, que es el slice del medio CARGADO. Leer la fila sería acotar un slice mientras se muestra otro.
- El bucle es estado de UI, **nunca persistido**: es una preferencia de ESTA sesión de escucha, no una propiedad del medio, y un loop almacenado acotaría en silencio el medio de una transcripción al reabrirlo.
- `set_loop_state(checked, available)` son las dos mitades de UN control en una llamada: separadas, las dos ventanas podrían discrepar sobre qué hace el botón.
- Señales bloqueadas en todo mirror. El owner refleja el botón de la OTRA ventana en el suyo, así que una escritura sin bloquear rebotaría el estado y apagaría el loop en el instante en que se encendió.
- Disponibilidad sigue al slice: un clic que pareciera no hacer nada (loop sin slice) ya no puede envenenar el siguiente slice que se dibuje.

**Verificación observada:** RED `ImportError: cannot import name 'resolve_slice_position'` (los 138 preexistentes en verde); GREEN `Ran 161 tests ... OK` = 138 preexistentes + 23 nuevos (15 de frontera pura, 8 de botón/mirroring). `compileall -q app\ui`: exit 0. `git diff --check`: limpio. Los tres re-ejecutados por el orquestador con el mismo resultado.

**Límite explícito de la cobertura:** `MainWindow` NO se construye en los tests. Su `__init__` crea un `MediaRepository` real (lee y escribe `items.json`) y un `Pipeline` real, así que un test que lo construyera probaría el sistema de archivos tanto como el widget. El hueco que queda —que `_on_position_changed` esté cableado a estos helpers— es real y se registra como tal para la evidencia offscreen de T5, en lugar de fingir cobertura.

**Observación para T5, no tocada aquí:** al terminar el slice la fila conserva el icono de pausa, igual que tras una pausa manual, y el status dice "Fin del slice.". Es coherente con el comportamiento existente y pulsar play reinicia el slice desde el inicio (por `_enter_slice_on_play`). Cambiar la semántica de pausa excede T3.

**Riesgo nativo:** RDD quedó **apagado a nivel de clone** por decisión del mantenedor antes de esta tarea (`receipt-driven development: off (decided by clone_local)`). No se intentó revisión nativa de T3 ni se fabricó JSON: el lineage de T2 sigue sin recibo y `review abandon` está prohibido por el propio STATUS. La verificación de esta tarea es la funcional de arriba, sin ceremony nativo.

### T4 — la ejecución usa el slice y deja de consumirlo (cerrada)

**Archivos**: `app/services/pipeline.py`, `app/services/transcriber.py`, `app/ui/main_window.py`, `tests/services/test_run_scoping.py` (nuevo). Sin dependencias nuevas.

**El RED doorway fue más fuerte de lo previsto, y eso cambió el diagnóstico.** El primer fixture construía `Pipeline(transcriber=...)` sin repositorio, y el consumo solo era alcanzable **a través** de él: la prueba de persistencia pasaba **en vacío** contra el código viejo. Corregido el fixture para cablear el pipeline como lo cablea la app, el RED dejó de ser unaGpérdida de slice hipotética y fue el producto roto de verdad:

```
AttributeError: 'MediaRepository' object has no attribute 'clear_marks'
```

Es decir: **una ejecución con slice no completaba**. T1 borró `clear_marks` y dejó la llamada colgando; T4 la retira, y la rama vuelve a estar entera.

**Se desató el acoplamiento entero, no solo la llamada.** `_consume_marks` era el ÚNICO uso de `self.repository` en el pipeline, así que el parámetro, el atributo, el import bajo `TYPE_CHECKING` y 25 líneas de docstring que justificaban el ciclo se fueron con él. Dos guards lo fijan: `repository` no está en la firma, y el pipeline **no tiene ningún atributo** cuyo nombre contenga `repo` — quitar el parámetro solo habría cerrado el acoplamiento a medias, dejando el almacenamiento a un `self.repository` de distancia de reintroducir.

**El denominador de progreso era un defecto de producto, no una incomodidad.** `progress_total` con `ranges` llamaba `duration(path)`: una apertura completa del contenedor, en cada ejecución, para calcular un número que el llamador ya traía. Con tiempos absolutos, el total correcto es **el final del último range**, no `info.duration` (que es el *chunk decodificado*, no el medio) ni la *longitud* del slice (que dejaría la barra cerca de 500% en un slice que empieza tarde). El slice 10–20 s de un medio de 600 s marcaba 3.3%; ahora marca 100%.

**Un test casi fijó el contrato equivocado y lo cambié antes de escribir producción.** `(5.0, 5.0)` es un slice degenerado pero su final son 5 s absolutas, no 0: devolver la longitud (0) dejaría el último segmento en 5 s sobre un total de 0 s. El test afirmaba `0.0`; el contrato afirma `5.0`.

**La nota de divergencia se extrajo para poder probarla.** `_slice_scope_note` era un método que solo se alcanzaba construyendo un `MainWindow`, con todo lo que eso arrastra; ahora la regla es `slice_scope_note(loaded, selected)`, pura, y el método solo le pasa los dos caminos. Bajó de 96 a 74 caracteres y dejó de sonar a queja: nombra los dos medios y listo.

**Vocabulario legacy en las status lines:** «tramo marcado» desapareció. Un status que habla de «tramo marcado» manda al usuario a buscar un segundo handle que ya no existe.

**Verificación observada:** RED con la causa correcta (`AttributeError: clear_marks` ×3, `ImportError: slice_scope_note` ×5, `av.FileNotFoundError` en los 4 de progreso por la apertura del contenedor inexistente, 6 asserts de status/firma). GREEN `Ran 182 tests ... OK` = 161 de T3 + 21 nuevos. `compileall -q app tests`: exit 0. `git diff --check`: limpio. Sin llamadas a `clear_marks`/`_consume_marks` en todo el repo; los dos únicos `Pipeline(` de producción pasan ya sin repositorio.

**Pendiente a propósito:** la segunda prueba manual real (slice que sobrevive a una ejecución de verdad). Queda para T5 junto con el resto de evidencia offscreen.

## Próximo paso
T5: evidencia offscreen en el repo, sección de `README.md` en inglés, y la segunda prueba manual real sobre el slice persistente.
