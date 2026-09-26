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
- [ ] T2: `MediaItem.marks: list | None` (`None` = no hay; lista de pares ordenada y sin solapes), operaciones en `MediaRepository`, carga retrocompatible de `items.json` intacta, y limpieza tras ejecución correcta.
- [ ] T3: `paintEvent` en `app/ui/seek_slider.py` para el sombreado, tira de marcas debajo con `{` y `}`, menú contextual (marcar inicio, marcar fin, quitar marcas, limpiar), y cableado de `seek_slider` y de `video_window.slider` al mismo modelo por medio seleccionado.
- [ ] T4: verificación y documentación. `compileall`, smoke Qt offscreen, `git diff --check`, sección en `README.md` en inglés.

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
- Delivery: `ask-on-risk`; ningún task debe pasar de ~400 líneas.

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
