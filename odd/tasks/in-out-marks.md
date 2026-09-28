# Marcas de inicio y fin (in/out) en los reproductores

## Objetivo
Poder acotar qué trozo de un medio se transcribe o se traduce, marcando inicio y fin sobre el timeslider de los reproductores de audio y de video.

## Problema / por qué
Los archivos que se transcriben son a menudo largos y el usuario solo necesita un fragmento. Sin acotar, se paga la inferencia completa y la traducción de material que no le sirve, y el modelo trabalha de más, alargando los tiempos.

## Alcance
- Colocar y eliminar marcas de inicio y fin en el timeslider de audio y en el de video, con menú contextual.
- Mostrar el tramo marcado sombreado sobre el groove, con una tira de marcas debajo usando `{` y `}`.
- Recortar el medio antes de transcribir, y usar solo el tramo marcado para traducir.
- Soportar varios tramos disjuntos por medio: se ordenan y los solapes se fusionan.
- Persistir las marcas por medio.
- Limpiar las marcas tras una ejecución correcta, porque fueron el input de esa ejecución.
- Sin marcas, el comportamiento actual queda intacto.

## Decisiones de diseño
- Timestamps absolutos, no relativos al tramo, para que el SRT pueda subtitular el medio original.
- Las marcas se limpian tras una ejecución correcta: si persistieran, la siguiente transcripción saldría solo con ese trozo.
- Una marca de inicio sin su correspondiente fin se ignora.
- Los solapes se fusionan y el resultado queda ordenado.

## Restricciones
- Python 3.14.3, PySide6 6.11.2, Windows. Sin dependencias nuevas.
- `faster_whisper.clip_timestamps` NO sirve para esto: solo aplica dentro de una ventana `chunk_length` de 30 s e ignora `vad_filter`. El recorte se hace decodificando el rango y pasando un `np.ndarray` a `WhisperModel.transcribe`.
- El recorte comparte la decodificación PyAV que ya demuestra leer los MKV que rompen a torchcodec. La ruta con `ranges` **no** pasa por `Diarizer._safe_input`: decodifica el rango con el clipper y entrega la onda directamente, así que el lector problemático ni siquiera entra. `_safe_input` sigue igual en la ruta de archivo completo. Ver T1.
- La traducción es por segmento y posterior a la transcripción: no requiere cambios para el recorte.
- **No editar el worktree principal.** Su `git write-tree` es `2ab1817de2f9a190b0df80bfe46c3988ef5727bc`, exactamente el `current_candidate_tree` congelado del lineage `review-ed572d1d7ca76c78` con `projection: workspace`. Editar un solo byte ahí destruye el target `sha256:0acf...` de la revisión de notas editadas, que sigue pendiente. Todo este trabajo vive en el worktree hermano.
- Este worktree usa su propio `.codegraph/`; nunca copiar ni reusar el índice del principal.

## Tareas
- [x] T0: spike sin código de producción. Los timestamps son 0-based respecto al array recibido, así que sumar el offset es correcto. Ver [Evidencia](#evidencia). Quedan tres casos sin cubrir, que se cierran en T1 o con un medio real: códecs no-AAC, presencia de pista de vídeo, y rangos de más de 30 s.
- [x] T1: `app/services/clipper.py` con decode por rango (seek hacia atrás y decodificación hacia adelante descartando hasta el punto exacto, **con guarda de 0.25 s antes del objetivo**). Selección del stream por el **objeto** `c.streams.audio[0]`, validando antes que haya al menos una pista de audio, con mensaje propio. `Pipeline` y `Transcriber` aceptan `ranges: list[tuple[float, float]] | None`, donde `None` significa archivo completo. Timestamps llevados a absolutos. Diarización acotada a los rangos, aunque **no** con `Inference.crop()`: ese método no existe en el pipeline. Ver [Evidencia](#evidencia).
- [x] T2: `MediaItem.marks: list | None` (`None` = no hay; lista de pares ordenada y sin solapes), operaciones en `MediaRepository`, carga retrocompatible de `items.json` intacta, y limpieza tras ejecución correcta. Ver [Evidencia](#evidencia).
- [x] T3: `paintEvent` en `app/ui/seek_slider.py` para el sombreado, tira de marcas debajo con `{` y `}`, menú contextual (marcar inicio, marcar fin, quitar marcas, limpiar), y cableado de `seek_slider` y de `video_window.slider` al mismo modelo por medio seleccionado. Ver [Evidencia](#evidencia).
- [ ] T4: documentación. Los checks de `compileall`, smoke Qt offscreen y `git diff --check` ya se ejecutaron como evidencia de T3; queda la sección en `README.md` en inglés.

### Regresiones de la prueba manual (2026-09-27)
Cinco hallazgos del usuario en la prueba manual con el MKV real. T5 y T6 son defectos reales de cableado; T7 y T8 son usabilidad; T9 es una función nueva. El humo de T3 no los cubrió porque ejercía los widgets de forma aislada y nunca el ciclo real play→marcar→volver a entrar.

- [x] T5 (**correctitud, prioridad máxima**): la ejecución y las marcas se keyedian de forma distinta. `_on_marks_edited` escribe con `self._playing_path`, pero `_run` lee con `path = self.media_list.selected_path()` y saca `ranges` de esa fila. Con el medio A sonando y marcado y la fila B seleccionada, `ranges` salía de B y el resultado no correspondía a lo marcado. **Decisión del mantenedor (2026-09-27): la ejecución es la de la fila seleccionada**, porque los paneles de transcripción y traducción son por selección. La geometría de las marcas, en cambio, la fija la duración del medio **cargado**, así que la barra sigue siendo del medio cargado. La consecuencia inevitable es que ambas cosas pueden diferir, y el arreglo no puede ser fingir que no: una ejecución divergida queda **sin acotar y lo declara**, en la línea de estado al cambiar de selección y otra vez al arrancar. Ver [Evidencia](#evidencia).
- [x] T6 (**funcional**): el timeslider de la ventana de video dejaba de avanzar al volver a entrar en el video. La hipótesis se **verificó antes de arreglar**, y era correcta: `setSource` con la MISMA URL no emite nada. Ver [Evidencia](#evidencia).
- [x] T7 (UX, bajo riesgo): la marca `in` ya se ve desde el primer clic, como tick discontinuo sobre el groove y como `{` suelta en la tira, en color atenuado y sin `}`.
- [x] T8 (UX): el clic derecho sobre un tramo nombra el tramo que va a quitar, con sus extremos. Conserva la redacción genérica cuando el clic no cae sobre ninguno y la limpieza de todas.
- [x] T9 (función, independiente del feature): doble clic sobre la imagen del video reproduce o pausa.

## Criterios de aceptación
- Con dos rangos disjuntos marcados, la transcripción cubre ambos y los timestamps resultantes son absolutos respecto al medio original.
- El SRT generado con marcas es válido sobre el medio original, no sobre el recorte.
- La diarización respeta los mismos rangos que la transcripción.
- Sin marcas, el resultado es idéntico al comportamiento actual.
- Las marcas sobreviven a reiniciar la aplicación, y un `items.json` previo sin el campo carga sin error.
- Tras una ejecución correcta, las marcas de ese medio quedan limpias.
- El sombreado y la tira de marcas aparecen en el reproductor de audio y en el de video.

## Verificación
- `python -m compileall app`
- Smoke Qt offscreen: dos rangos disjuntos, aserción de timestamp absoluto, crop de diarización, alineación del SRT, round-trip de persistencia, y que con marcas presentes la ejecución sustituya el resultado anterior.
- `git diff --check` y revisión del estado del worktree antes de cada commit.

## Progreso
- Rama: `feat/in-out-marks`, worktree `CODE_WORKS\Transcript-worktrees\marks`, base `main` en `42dc326`.
- Ruta: **delegated direct** para la implementación; los cambios de comportamiento abarcan servicios, persistencia y los dos reproductores.
- TDD: no hay suite de tests automatizados en el proyecto; se usarán checks funcionales offscreen.
- Delivery: `exception-ok` con `size:exception` **aprobada por el mantenedor** (2026-09-26). Motivo: al cruzar T1 las 400 líneas authored y no existir todavía repositorio GitHub, los PRs encadenados no eran ejecutables. Se acepta superar el presupuesto y se sigue en commits de unidad de trabajo; cuando exista el repositorio se decidirá si el historial se divide.
- Riesgo nativo del slice T1: `gentle-ai review assess` con `--base-ref 42dc326 --committed-only` da `medium` por `executable_change` en `app/services/clipper.py`, 5 rutas y 497 líneas, con `review_due: true` y motivo `slice_budget_reached`. La revisión nativa no se ejecuta todavía: el mantenedor la pospuso para después de la fase de pruebas, y el host no puede lanzar subagentes de revisión (política de tier del proveedor). El límite revisado sigue en `42dc326` para que la feature completa se revise como un único slice.
- T3 commiteada: `4ced31d` (`feat(ui): wire in/out marks into audio and video players`, 4 archivos, +709/−16) y `ba449d2` (`fix(ui): derive the mark groove from the stylesheet, raise light mark contrast`, 2 archivos, +96/−85). HEAD de la rama: `ba449d2`.
- Riesgo nativo del slice T1+T2+T3: `medium` por `executable_change` en `app/core/models.py`, 11 rutas y 1427 líneas, `review_due: true` por `slice_budget_reached`. El mantenedor **rechazó la revisión de ese candidato** el 2026-09-27 (`--consent declined` → `consent: declined_this_candidate`, sin autoridad ni recibo creado). El rechazo es por candidato, no un apagado global: el límite revisado sigue en `42dc326` y un candidato futuro volverá a preguntar.
- T5–T9 commiteadas: `7207a16` (T5+T6), `2602993` (T7+T8), `ce0d49c` (T9). 406 líneas authored. HEAD de la rama: `ce0d49c`.
- Las cinco regresiones de la prueba manual del 2026-09-27 están cerradas y verificadas; el slice sigue sin revisar, con el límite revisado en `42dc326` (1761 líneas authored acumuladas desde la base de la rama, 11 rutas).
- T4 sigue abierta: falta la sección de `README.md` en inglés, y falta una **segunda** prueba manual (la primera ya se hizo y es la que produjo T5–T9).

## Evidencia

### T0 — spike (cerrada)
La pregunta decisiva era si los timestamps de faster-whisper son 0-based respecto al array recibido. **Lo son**, así que T1 puede sumar el offset sin invierte nada.

- **Origen de los timestamps**: en `faster_whisper/transcribe.py:875-878` el `ndarray` pasa intacto, sin normalizar ni envolver. El origen se fija en `transcribe.py:1168` como `time_offset = seek * self.feature_extractor.time_per_frame`, un índice dentro de `features` derivados del array, y la conversión token→tiempo en `transcribe.py:1056-1059` solo añade ese offset. Lo único que reescribe timestamps es `transcribe.py:1009-1010`, y solo con `vad_filter=True`. No hay ninguna constante que ancle el origen a un reloj absoluto.
- **Prueba en ejecución**: la misma ráfaga sintética transcrita como array de 2 s devuelve `[0.0, 2.0]`; el mismo audio dentro del array completo de 100 s devuelve `[90.0, 100.0]`. Mismo audio, distinto origen de array, distinto timestamp.
- **Precisión del recorte**: error de posición real ≤ **0.45 ms** medido con impulsos deterministas, constante y con dispersión de 0.001 samples. El sesgo es siempre negativo, o sea el recorte incluye una miga de audio anterior al inicio. Contraste: el cuanto de whisper (`time_precision`) es de 20 ms, así que el sesgo es 44 veces más pequeño y no puede desincronizar un SRT.
- **Aterrizaje del seek en MKV/AAC**: entre −4136 ms y +13 ms. Siempre cae *antes*; es granularidad de índice de clusters, no error de posición, y el recorte por `frame.pts` lo corrige. Costo: hasta 4.1 s de audio desechable. `any_frame=True` aterrizaba +1 a +35 ms, o sea se pasa del objetivo, así que no sirve para recortar.
- **Voz real ya verificada**: `pyannote/audio/sample/sample.wav` (30 s, mono 16 kHz, dos hablantes), 4 rangos, 57 palabras alineadas contra la transcripción completa: desviación media de inicio −0.09 s y de fin −0.08 s, la mayoría dentro de ±0.02 s. Repetido con `large-v3-turbo`, el modelo que usa la app: 35 palabras, medias +0.06 s / +0.02 s. Los outliers de ±1 s son emparejamientos erróneos de `difflib` sobre tokens repetidos, no error de tiempo.

### Consecuencias para T1
1. **Dividir por 32768.** `decode_audio` devuelve `s16 / 32768.0`. Sin ese `/32768.0` el recorte entrega picos de 16629 y whisper no dice nada, con la duración correcta: basura con aspecto de audio válido.
2. **El `pts` de la salida del resampler no sirve como índice de muestra.** Emite 341/342 muestras por frame pero su `pts` avanza 336/335/337, hasta 336 samples de discrepancia. Hay que contar muestras. El primer frame sale corto (325 en vez de 341) por el retardo de grupo del filtro, y de ahí el sesgo constante.
3. **No asumir ≥1 segmento por rango.** Whisper devuelve 0 segmentos sobre audio sin voz, correctamente: `no_speech_prob` fue 0.89–0.95 sobre tonos. Desactivado el umbral emite `' Thank you.'` sobre silencio. T1 no debe bajar `no_speech_threshold`.
4. **El texto de un rango no coincide con el del fichero completo.** En ventana de 6 s whisper fusiona frases y reinicia el prompt: `'Neither did I'` frente a `'I needed to die'` sobre el mismo audio. Los timestamps cuadran, el texto no. El SRT es válido pero con segmentación distinta respecto a transcribir el archivo entero.
5. **Selección del stream: el peligro real no es decodificar vídeo, es la ambigüedad de pista.** En `container.decode(...)` el entero es un **ordinal dentro de la tupla por tipo**, no un índice global: `decode(audio=0)` sobre un archivo con audio en el stream 1 devuelve el audio correcto, y `decode(video=1)` lanza `IndexError` aunque el índice global 1 exista. El riesgo es que con varias pistas de audio `audio=0` **nunca falla** y elige en silencio la pista 0, y que con cero pistas el error es un `IndexError: tuple index out of range` inútil para un usuario. Pasar el objeto `c.streams.audio[0]` y validar `len(c.streams.audio) > 0` con mensaje propio.
6. **Guarda de 0.25 s al hacer seek, obligatoria.** `backward=True` **no garantiza** aterrizar antes del objetivo: 56 de 711 puntos de malla aterrizan por delante, +1 a +8 ms. Sin la guarda, los 56 dan longitud incorrecta (pedir 11.0 s devolvía 47872 muestras en vez de 48000). Con la guarda: 0 de 56, error máximo de 1 muestra.

### Validación sobre medio real (`C:\Users\josea\Videos\2026-09-25 16-19-06.mkv`)
292 MB, 356 s. Stream 0 vídeo h264 60 fps, stream 1 audio aac 48 kHz estéreo.
- **La pista de vídeo NO empeora la granularidad del seek.** Peor rebobinado −61 ms, frente a los −4136 ms del spike sintético: unos 68 veces mejor. Los clusters de este archivo miden ~0.15 s. El −4 s era propiedad de la densidad de clusters del archivo sintético, no de la presencia de vídeo. `any_frame=True` dio resultado idéntico aquí; `backward=False` sí se pasa (+24/+27 ms). Se mantiene `backward=True, any_frame=False`.
- **Alineación del recorte**: −0.31 a +0.31 ms contra `decode_audio` del archivo completo, 0.016× el `time_precision` de 20 ms.
- **Offset sobre voz real: PASA.** VAD de pyannote localizó 86 regiones con habla. Sobre un silencio→habla real con onset en 201.707 s, con el rango arrancando 2.5 s dentro del silencio y `vad_filter=True` (el ajuste de la app): **+40 ms**. Por palabra, mediana **+6.9 ms**, 18/18 dentro de ±500 ms y 15/18 dentro de ±20 ms. Criterio de ±0.5 s cumplido con holgura.
- **Rangos de más de 30 s: PASA.** Un rango de 60 s dio 15 segmentos, 0 huecos mayores de 1 ms, 0 resets, ningún timestamp por encima de 60.00 s, span absoluto exacto y **mediana 0.0 ms**. Los offsets de las ventanas de 30 s de `nb_max_frames` **sí** son array-relativos.
- **Rangos disjuntos: PASA.** A[35,55] + B[275,300] dieron 7 segmentos monótonos, 0 solapes, 0 en el hueco de 220 s, medianas 0.0 ms en ambos.

### T1 — implementación (cerrada)
`ranges=None` conserva literalmente la ruta anterior: es el mismo `path` al motor, sin recorte y sin offsets. Todo lo de abajo solo aplica cuando hay rangos.

**Archivos**: `app/services/clipper.py` (nuevo), `transcriber.py`, `diarizer.py`, `pipeline.py`. Sin tocar `core/models.py` ni `storage/repository.py`, sin dependencias nuevas.

**1. `decode_range` recorta exacto, y la guarda es imprescindible.**
El recorte se ancla en el `pts` del **primer frame resampleado**, no en el objetivo pedido: contar desde el objetivo daba un error de hasta 992 muestras (62 ms). Con `SEEK_GUARD = 0.25`, 341 posiciones repartidas entre rejillas, keyframes, clips sintéticos y medios con vídeo dieron **0 desviaciones de longitud** y un error de alineación máximo de **0.250 ms** contra `decode_audio` del archivo completo. El control con `SEEK_GUARD = 0` falla: 6/74 en la rejilla real, 19/223 en keyframes y 12/12 en el medio AV.

**2. La diarización NO se puede acotar con `Inference.crop()`.** `Inference.crop` existe, pero el objeto que `from_pretrained` devuelve (`SpeakerDiarization`) **no lo expone**: `AttributeError: 'SpeakerDiarization' object has no attribute 'crop'`. Sus internos (`_segmentation`, `_embedding`) son `Inference`, sí, pero usarlos devolvería features sin el clustering que produce los hablantes. La vía soportada es entregar la onda: `pipeline({"waveform": tensor (channel, time), "sample_rate": 16000})`, que `Audio.validate_file` acepta. Por eso la ruta con rangos decodifica con el clipper y se salta `_safe_input`: el tensor ya está en memoria, así que torchcodec no interviene y el WAV temporal sería trabajo perdido.

**3. El clustering necesita contexto: sin margen, los hablantes son incorrectos.** Un rango de 12 s recortado en crudo devolvió **2 turnos con `SPEAKER_00`** donde el archivo completo da **5 turnos con `SPEAKER_02`/`SPEAKER_01`**. Con 15 s de margen a cada lado (`RANGE_WARMUP`), recortando luego al rango pedido, los 10 turnos coinciden con la ejecución completa: **misma etiqueta y ≤ 10 ms de deriva**, y `assign_speaker` por rango devuelve la misma etiqueta que la referencia. El hueco de 167.5 s nunca se procesa. El pipeline completo son 151 turnos en 7.8 s; los dos rangos, 1.4 s.

**4. Verificación funcional** (Whisper tiny, `beam_size=1`, offline; pyannote 3.1 con `HF_TOKEN`):
| Check | Resultado |
| --- | --- |
| `python -m compileall app` | PASS |
| Sin marcas, A/B contra los módulos de `42dc326` con la misma instancia | PASS: texto, idioma, 9 segmentos y los 9 valores de progreso idénticos |
| Dos rangos `(22.5,34.5)` y `(202,214)` | PASS: 192000/192000 muestras, 3 segmentos absolutos, 0 en el hueco, idioma fijado en `en` |
| Longitud y alineación, 341 posiciones | PASS: 0 muestras de desviación, 0.250 ms |
| Diarización con rangos vs. archivo completo | PASS: 10 turnos, etiquetas y límites dentro de 10 ms |
| `Pipeline` reenvía rangos a ambos motores (motores falsos, sin modelo) | PASS: transcribe, transcribe_and_translate, diarize y sin diarizar |
| `git diff --check` | limpio |

**5. El resto queda igual**: `no_speech_threshold` intacto (un rango de silencio puede devolver 0 segmentos), la traducción sigue siendo por segmento y posterior, y `assign_speaker` no se toca. Pyannote puede devolver **turnos solapados** (interrupciones): el par `[32.860,34.500] SPEAKER_00` + `[32.995,33.417] SPEAKER_02` aparece igual en la ejecución completa, así que la regla de solape máximo sigue siendo la correcta.

### T2 — persistencia y limpieza (cerrada)
**Archivos**: `app/core/models.py`, `app/storage/repository.py`, `app/services/pipeline.py`. Sin tocar la UI, sin dependencias nuevas.

**1. `normalize_marks()` es el único sitio donde se normaliza, y `None` nunca se convierte en `[]`.**
La función de módulo recibe lo que el llamante juntó (posiciones del slider, una lista guardada, un `items.json` editado a mano) y devuelve pares `[inicio, fin]` **ordenados y sin solapes**, con los pares inválidos descartados. Devuelve `None` para `None`, para entrada vacía y para entrada enteramente inválida. Eso no es cosmético: los motores leen una lista de rangos **vacía** como "archivo completo", así que guardar `[]` convertiría "sin marcas" en "transcribe todo". Al no existir `[]` como estado almacenable, el contrato de T1 queda protegido por construcción. `MediaItem.__post_init__` la aplica en cada construcción, así que un `items.json` con marcas desordenadas o solapadas se autorrepara al cargar.

**2. Un par tiene que ser una lista o una tupla de dos elementos, y el chequeo va antes de leer los valores.** Un string suelto es iterable, así que indexarlo convertía `"12"` en el rango `[1.0, 2.0]` en silencio: audio de la posición equivocada sin ningún error. `isinstance(pair, (list, tuple))` más `len(pair) == 2` corta eso antes de que llegue a `float()`.

**3. Los pares se guardan como listas de `float`, no como tuplas.** `json.dumps` escribe una tupla como array y `json.loads` la devuelve como lista, así que guardar tuplas haría fallar la igualdad exacta del round-trip por un detalle de tipo. Los valores se castean a `float` para que el JSON sea homogéneo.

**4. `MediaRepository.__init__` ya llamaba a `save()`** para dejar el archivo creado, así que construir el repositorio **sí** escribe. No es una migración destructiva: la comprobación 2b verificó que las 3 entradas del `items.json` real conservan todos sus campos y valores, y que lo único que se añade es `"marks": null`. `load()` en sí no toca el archivo (comprobación 2a, hash idéntico antes y después), que es lo que exige el criterio de aceptación.

**5. La limpieza va en el `Pipeline`, con el repositorio inyectado por constructor.** `Pipeline(transcriber, target_lang, diarizer, repository=None)`. Al terminar bien, `_consume_marks()` llama a `clear_marks(index_of(path))`. Tres propiedades deliberadas:
- **Solo limpia una ejecución que consumió marcas.** Con `ranges=None` (archivo completo) no se consumió ninguna, así que unas marcas existentes siguen describiendo lo que el usuario quiere acotar la próxima vez y se dejan. Por eso el `None` de `normalize_marks` importa también aquí.
- **Nunca en la ruta de fallo.** La excepción de transcripción, decodificación o traducción se propaga antes de construir el resultado, así que las marcas sobreviven a una ejecución fallida y se pueden reintentar (comprobaciones 5h–5j: `FileNotFoundError` propagada y marcas intactas).
- **`repository=None` no cambia nada.** Sin repositorio inyectado el pipeline se comporta exactamente como antes (comprobación 5n), así que T2 no altera el comportamiento de la app hasta que T3 cablee la inyección.

**Pendiente para T3 (una línea, en la UI)**: `main_window.py:80` sigue construyendo `Pipeline(Transcriber(), target_lang=TRANSLATE_TARGET_LANG)`. Para que la limpieza ocurra en la app hace falta pasar el repositorio — `Pipeline(Transcriber(), target_lang=TRANSLATE_TARGET_LANG, repository=self.repository)` — además de leer `item.marks` y pasarlo como `ranges`. Ambas cosas son cableado de UI, que es T3; la lógica ya está escrita y verificada en el servicio.

**6. El hint de `ranges` dice `tuple` pero el storage produce `list`.** T1 anotó `list[tuple[float, float]] | None`; `normalize_marks` devuelve `list[list[float]]` porque es lo que sobrevive al round-trip por JSON. Los servicios solo hacen `for start, end in ranges`, así que ambos funcionan igual y no hay ningún fallo de comportamiento: es una imprecisión de tipos que conviene arreglar en T4 o en una pasada de limpieza, sin mezclarla aquí.

**7. Verificación funcional** (Whisper tiny, `beam_size=1`, `HF_HUB_OFFLINE=1`; sin diarización real, con dobles de prueba):
| Check | Resultado |
| --- | --- |
| `python -m compileall app` | PASS |
| Retrocompatibilidad, `items.json` real de 3 entradas sin `marks` | PASS: no revienta, `marks is None` en las 3, `load()` deja el hash idéntico, y la construcción solo añade `"marks": null` sin perder ningún campo |
| Round-trip con solapes, desorden, contacto e inválidos | PASS: `[[30,40],[10,20],[20,25],[35,45],[22,23],[99,99],["x",5],[5],"12",{"start":1,"end":2},[3,4]]` → `[[3.0,4.0],[10.0,25.0],[30.0,45.0]]`, idéntico en memoria, en disco y tras recargar |
| Aislamiento entre medios | PASS: 3 medios, solo el destino marcado; sobrevive al round-trip; `clear_marks` de uno no toca los otros |
| `clear_marks` sin marcas no escribe | PASS: hash del archivo sin cambios |
| Limpieza tras ejecución correcta (real, 6 s del MKV real) | PASS: 2 segmentos absolutos `[22.50→25.30]`, `[25.30→28.30]`, idioma `en`; marcas `None` en memoria y en disco tras releer, y el segundo medio conserva las suyas |
| La ejecución fallida no consume marcas | PASS: `FileNotFoundError` propagada, marcas intactas |
| Ramas con y sin diarizar | PASS: la rama diarizada también limpia, y reenvía los mismos rangos a los dos motores |
| Regresión sin marcas | PASS: el pipeline recibe `None` (no `[]`), el motor recibe la ruta sin recortar y los tiempos no llevan offset; una ejecución a archivo completo no borra marcas existentes |
| `git diff --check` | limpio |

### T3 — UI y cableado (cerrada)
**Commits**: `4ced31d` (initial) y `ba449d2` (corrección de groove y contraste). **Archivos**: `app/ui/seek_slider.py`, `app/ui/video_player_window.py`, `app/ui/main_window.py`, `app/ui/theme.py`. Sin tocar los servicios de T1 ni el repositorio de T2, sin dependencias nuevas.

**1. Las marcas pertenecen al medio que suena, no a la fila seleccionada.** `MainWindow` refleja `item.marks` del **`_playing_path`** y persiste contra ese mismo índice. Elegir otra fila de la lista mientras suena el medio A no toca las marcas del medio B: el estado de la UI tiene que describir lo que se oye, no lo que está resaltado en la tabla. Cuando no hay nada en reproducción, el destino es la selección.

**2. `pending_in` es memoria de la UI, nunca persistencia.** Una marca de inicio sin su fin solo existe mientras se elige el fin. Guardarla en `items.json` en ese estado produciría un par incompleto que `normalize_marks` descartaría en la carga, y el usuario perdería el gesto sin ninguna señal. El modelo de `marks` guarda solo pares cerrados.

**3. `MainWindow` inyecta el repositorio y pasa los rangos.** `Pipeline(Transcriber(), target_lang=TRANSLATE_TARGET_LANG, repository=self.repository)`, que es exactamente el pendiente que T2 dejó anotado en una línea. Los `ranges` salen de `normalize_marks(item.marks)`, de modo que el contrato `None` = archivo completo de T1 queda protegido por construcción también desde la UI. La UI **no** borra las marcas por su cuenta: relee del repositorio después de la ejecución, porque la limpieza es responsabilidad del `Pipeline` (T2) y duplicarla aquí abriría la puerta a estados divergentes.

**4. El bloqueo durante la inferencia es de estado, no de visibilidad.** Editar marcas con una ejecución en vuelo dejaría la UI describiendo un `ranges` que el motor ya no va a leer. Se deshabilita la edición y se bloquea el arranque de otra ejecución, y tras el resultado —éxito o fallo— se relee el estado real del repositorio. Un fallo conserva las marcas porque el `Pipeline` no las consumió.

**5. PySide6 6.11.2: cuatro defectos reales, no estilo.** Cada uno costó un fallo de humo antes de encontrarlo:
- **`QColor` no acepta `rgba(...)`.** El formato de Qt es `#AARRGGBB` o `#RRGGBB`, y `QColor("rgba(...)")` deja el color inválido en silencio: la marca se pintaba transparente sin ningún error. Todos los colores del tema pasan a `#AARRGGBB`.
- **`mapTo` entre hermanos puede tumbar el proceso.** Mapear al slider hermano desde la tira de marcas da un segmentation fault en vez de una excepción. La ruta correcta es `mapToGlobal` y de ahí al padre.
- **El enum de grooves de `QStyle` no es utilizable en 6.11.2.** La constante pedida no está donde PySide la expone, así que la lectura de la geometría real del groove por ese enum falla. Se eliminó esa lectura y el redibujado manual del handle que dependía de ella: es código muerto y una oportunidad más de desincronizar el pintado del QSS.
- **La geometría del groove sale del QSS, no del widget.** El groove no tiene altura garantizada por la API, pero el QSS sí la declara, así que la fórmula vive junto a la constante que la define: `QRect(0, (height - SEEK_GROOVE_HEIGHT_PX) // 2, width, SEEK_GROOVE_HEIGHT_PX)`, con `SEEK_GROOVE_HEIGHT_PX = 5` y filas medidas entre 5 y 9. Constante y geometría en el mismo módulo evita que el QSS y el pintado se separen.

**6. El error de unidades que casi deja las marcas invisible.** Las posiciones del slider vienen en **segundos** y el ancho viene en **píxeles**. Tratar los segundos como píxeles dibujaba la marca pegada al inicio con cualquier duración, y con un medio largo el error era de un factor de miles. La conversión a píxeles ocurre en un único punto, y el humo lo comprueba con dos rangos de posiciones distintas.

**7. Contraste comprobado, no supuesto.** La sonda de render mide el contraste real de lo pintado contra el fondo del groove en ambos temas. En oscuro: groove `#3a3c47`, relleno renderizado `#716251` con borde `#d8aa64`; contraste del relleno **1.86** y del borde **5.14**. Los valores claros se ajustaron para subir el contraste de la marca, que era el punto débil del tema claro.

**8. Verificación funcional** (intérprete autoritativo del proyecto: Python 3.14.3, PySide6 6.11.2, Qt offscreen):
| Check | Resultado |
| --- | --- |
| `python -m compileall app` | PASS |
| Import de los módulos de UI | PASS |
| `smoke_widget.py` (71 comprobaciones) | PASS: `ALL CHECKS PASSED` |
| `smoke_main.py` | PASS: `ALL CHECKS PASSED` |
| Sonda de render claro/oscuro y regresión | PASS: `ALL CHECKS PASSED` |
| `git diff --check` | limpio |
| Worktree principal | `git write-tree` sigue en `2ab1817de2f9a190b0df80bfe46c3988ef5727bc`, sin tocar |

**9. Lo que falta y no está resuelto**: T4 sigue abierto por la sección de `README.md` en inglés. La prueba manual con el medio real de 292 MB (`C:\Users\josea\Videos\2026-09-25 16-19-06.mkv`) **se hizo** el 2026-09-27 y fue la que produjo T5–T9; lo que sigue pendiente es una **segunda** pasada manual, no la primera.

### T5–T9 — regresiones de la prueba manual (cerrada)

**Commits**: `7207a16` (T5+T6, `main_window.py`), `2602993` (T7+T8, `seek_slider.py`), `ce0d49c` (T9, `video_player_window.py`). 406 líneas authored (+150/−31, +204/−29, +52/−1). Sin dependencias nuevas, sin tocar los servicios de T1 ni el repositorio de T2.

**Entorno de verificación**: el intérprete que el proyecto fija (Python 3.14.3, PySide6 6.11.2) tomado del venv del proyecto, con `QT_QPA_PLATFORM=offscreen` y el worktree en `sys.path`. El humo usa el `MediaRepository` **real** contra un `items.json` temporal, el `QMediaPlayer` **real** contra medios reales, un worker de inferencia síncrono falso (no se carga ningún modelo) y un `QMenu` con `exec()` neutralizado pero dirigible, de modo que una **elección** del menú se ejerce de extremo a extremo. Cada expectativa de geometría se recalcula desde las constantes del stylesheet en vez de devolverle el widget al test, que es lo que permite que una discrepancia real falle.

| Check | Resultado |
| --- | --- |
| `python -m compileall app` (3.14.3) | PASS |
| `git diff --check` | limpio |
| Humo offscreen T5–T9, intérprete fijado | **PASS: 135 checks, 0 fallos** |
| Cobertura | T5, T5a–d, T6, T6a–d, T7, T7b–c, T8, T8b–f, T9 |
| Worktree principal | `git write-tree` sigue en `2ab1817de2f9a190b0df80bfe46c3988ef5727bc`, sin tocar |

**1. T6: la hipótesis se verificó ANTES de arreglar, y era correcta.** Medido en 6.11.2 con el backend ffmpeg, tanto con un wav de 30 s como con el MKV de 292 s: poner una fuente que **ya es** la fuente del reproductor no emite `LoadingMedia`, ni `sourceChanged`, ni `durationChanged`, ni `LoadedMedia`; solo `BufferingMedia`/`BufferedMedia` y la transición a `PlayingState`. El backend conserva el medio cargado, así que `player.duration()` sigue respondiendo bien en la línea siguiente, pero nada lo anuncia. La otra mitad, **por qué preguntar es seguro**: con una fuente genuinamente nueva `player.duration()` responde `0` en la línea siguiente, nunca el valor del medio anterior, y `durationChanged` llega con el real poco después. Medido también que forzar un cambio real de fuente (`setSource(QUrl())` antes de la URL) sí devuelve `durationChanged`, pero vuelve a sondear el fichero en cada entrada y tira la línea temporal por un evento, así que la app **pregunta** en vez de obligar.

**2. T5: la divergencia se declara, nunca se disimula.** Con el medio A cargado y marcado y la fila B seleccionada, la ejecución queda sin acotar y el estado nombra los dos ficheros; cuando la selección coincide con el cargado, las marcas sí acotan. Con nada cargado no hay divergencia posible y se aplica lo propio de la fila. Un medio sin marcas, divergido, también se explica: el mensaje es el mismo, sin marcar en falso lo que no acotó.

**3. T7: el tick pendiente se ve y no se confunde con un tramo.** Contraste real medido contra el groove: **4.89** en oscuro y **4.41** en claro, y **29** escalones de diferencia frente a un tramo ya confirmado, así que la señal no es solo "algo pintado" sino "otra cosa". El tick comparte el mapeo `_x_at_seconds` con los tramos, así que la banda empieza en la columna exacta del tick (medido: mismo valor) y confirmar el fin solo lo engorda.

**4. T8: el índice se resuelve contra lo pintado, con una guarda que importa.** `mark_pixel_rects()` **descarta** el tramo que no se puede dibujar, así que emparejar supervivientes por índice nombraría —y quitaría— el tramo equivocado. Por eso el rect pintado solo se usa cuando rects y marcas almacenadas son del mismo tamaño, y el rango se busca en las marcas almacenadas, nunca en las pintadas. Descubrimiento que costó un check rojo: **`set_marks()` normaliza** (ordena y fusiona), así que `[[100,200],[5,10]]` se almacena como `[[5,10],[100,200]]` y el tramo dibujado es el índice 0. Una expectativa de índice escrita sobre el orden de entrada mide el test, no el widget.

**5. T8, el ensanchado a `MIN_MARK_WIDTH_PX` depende del ancho.** Un tramo de 0.05 s **no** da la premisa del test: medido, con los 1678 px reales de la barra principal las tres columnas ampliadas caen todavía **dentro** del par, así que el mapeo por segundos lo habría encontrado igual. Un tramo subpíxel (`[5.0, 5.001]`, que cae en la misma columna que `5.0` a cualquier ancho) sí sale del par por construcción, y ahí el camino del rect es el único que puede resolverlo: verificado que el clic lo quita y que `_remove_under_cursor` con esos segundos se niega.

**6. T9: `QTest.mouseDClick` no puede modelar un doble clic real sobre un botón checkable.** Envía press + DblClick + release **sin release en medio**, y un `QAbstractButton` solo emite en un release, así que reporta 0 toggles donde un usuario produce 1. Medido con la secuencia real (press, release, DblClick, release) sobre un `QPushButton` checkable: **dos** toggles, `[True, False]`, dejando el botón donde estaba. Es semántica de Qt y es **preexistente**: el forwarder se instala solo en el `QVideoWidget` y el botón conserva exactamente ese comportamiento, lo que el humo comprueba contando los toggles. Sobre la imagen de vídeo, un doble clic real alterna **exactamente una vez**, un segundo pausa por el camino compartido, y el doble clic derecho y el clic simple no alternan.

**7. Los 4 checks que fallaron en la primera pasada no eran defectos del producto.** Se-investigó cada uno con sondas independientes antes de tocar nada, y los cuatro medían el script: uno esperaba un índice previo a la normalización, dos dependían del ancho, y uno usaba una señal de `QTest` que no reproduce un doble clic real. Corregido el script para que mida el invariante en vez de la premisa caducada, el humo pasa entero. **No se cambió código de producción para hacer pasar un test.**

**8. Riesgo nativo del slice**: `gentle-ai review assess` con `--base-ref 42dc326 --committed-only` devuelve `unassessable` porque el worktree tiene `.codegraph/` sin trackear, que exige una declaración explícita de untracked; el índice local de CodeGraph no es parte de la feature. `review_due: true` por `high_risk`. El límite revisado sigue en `42dc326`.
