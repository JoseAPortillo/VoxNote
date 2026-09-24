# Video Playback Window

## Objetivo
Al pulsar play en una fila cuyo path apunta a un vídeo, abrir una ventana
dedicada que reproduzca el vídeo con botón play/pausa y timeslider con
click-para-saltar y drag-para-buscar. Los audios siguen reproduciéndose
como hasta ahora (barra en la ventana principal, sin ventana extra).

## Problema / por qué
Hoy todo se reproduce como audio-only en la ventana principal. Un vídeo
se escucha pero no se ve; no hay forma de ver la imagen ni transportes
dentro de una superficie de vídeo.

## Alcance
- Nueva ventana `VideoPlayerWindow` (widget top-level) con QVideoWidget,
  botón play/pausa, SeekSlider reutilizado y label de tiempo.
- Integración en `MainWindow`: detectar vídeo por extensión, enrutar el
  `QMediaPlayer` existente al `QVideoWidget` de la ventana, orquestar
  play/pausa/seek y cerrar la ventana al parar/cambiar/cerrar la app.
- QSS para el botón play/pausa (estado activo visible: fondo accent
  mientras reproduce, como el "sombreado pulsado").
- Sin cambios en la reproducción de audio (comportamiento actual intacto).

## Tareas
- [x] T1: `app/ui/video_player_window.py` — ventana con QVideoWidget +
      botón checkable play/pausa + SeekSlider + label "mm:ss / mm:ss";
      señales `play_toggled`, `seek_pressed`, `seek_released`; slots
      `set_duration`, `update_position`, `set_playing_state`,
      `reset`; `closeEvent` → señal `closed`.
      Evidencia: ventana top-level con QVideoWidget+, botón checkable
      (objectName `playButton`, icono QStyle SP_MediaPlay/Pause; en estado
      playing el glifo se recoloriza en blanco mediante mask para leer sobre
      el fondo accent), SeekSlider reutilizado tal cual, label de tiempo
      `_fmt_ms` (mm:ss, h:mm:ss ≥1h). Señales: play_toggled (clic de
      usuario), seek_pressed (press), seek_released (release, con valor ms),
      closed (closeEvent; sin WA_DeleteOnClose → reutilizable por hide).
      Slots: set_source_title, set_duration, update_position (no-op con
      slider pulsado), set_playing_state (bloquea señales para no realimentar
      a MainWindow), reset. La ventana nunca toca QMediaPlayer.
- [x] T2: Integración en `app/ui/main_window.py` — `VIDEO_EXTENSIONS` en
      config; `_is_video(path)`; `_ensure_video_window()`; ruteo en
      `_toggle_play` / `_stop_playback` / `_on_media_status` /
      `_on_position_changed` / `_on_duration_changed` / `closeEvent`;
      cerrar ventana al cambiar de media o parar.
      Evidencia: `VIDEO_EXTENSIONS` añadido a `app/core/config.py`;
      `_ensure_video_window` crea UNA vez y reutiliza por hide (verificado:
      misma instancia entre reproducciones). `_toggle_play`: mismo path →
      pause/resume + sync de ventana; path distinto → `_start_video` rutea el
      player compartido al QVideoWidget, play, `set_playing` de la fila,
      show/raise/activate. `_stop_playback` y EndOfMedia → ventana
      set_playing_state(False) + hide + fila restaurada. positionChanged y
      durationChanged también alimentan la ventana; la barra principal
      permanece oculta durante vídeo (el transporte vive en la ventana).
      Audio sin cambios. Hallazgo clave: este backend pasa a PlayingState de
      forma ASÍNCRONA tras play() → la UI se sincroniza por la ACCIÓN tomada,
      no releyendo playbackState. Cerrar la ventana con X → closed →
      `_stop_playback` (hide() no dispara closeEvent → sin recursión).
- [x] T3: `app/ui/theme.py` — QSS `QPushButton#playButton` (hover,
      pressed, `:checked` = accent) + label de tiempo.
      Evidencia: bloques `QPushButton#playButton` (default/:hover/:pressed/
      :checked/:checked:hover/:checked:pressed), `QLabel#videoTimeLabel`
      (muted, monospace), `QVideoWidget` negro, superficies de la ventana;
      verificado en oscuro y claro (sin `$` sin sustituir, accents
      `#4f8cff`/`#2f6fed` presentes).
- [x] T4: Smokes offscreen (señales, estados, drag slider) + smoke real
      con MP4 generado (ffmpeg) y work-unit commit en rama feature.
      Evidencia: Probe A (ventana standalone) y Probe B (MainWindow + MP4
      real) ALL PASS — detalle en "Verification evidence". Commit único de
      trabajo (código + doc) — ver abajo.

## Criterios de aceptación
- Play en fila de vídeo → ventana visible reproduciendo (player en
  `PlayingState`, vídeo conectado al QVideoWidget, fila con icono pausa).
- Botón play/pausa de la ventana: pausa congela el frame, reanuda; el
  botón muestra estado activo (`:checked`, sombreado accent) mientras
  reproduce.
- Slider: click izquierdo en cualquier punto salta; arrastre con botón
  izquierdo pulsado mueve la posición en vivo y al soltar busca; refleja
  la posición durante la reproducción.
- Cambiar a otro media / stop / EndOfMedia → ventana se cierra, fila
  restaura el icono play.
- Cerrar la ventana con la X → playback se detiene y la fila se limpia.
- Audio-only: sin ventana, barra principal como hoy.
- Temas oscuro y claro coherentes.

## Verification evidence
Smokes con el venv del repo (`.venv\Scripts\python.exe`, Python 3.14.3) y
`QT_QPA_PLATFORM=offscreen`; media generado con ffmpeg en
`%TEMP%\opencode\video-playback-smoke\` (fuera del repo). `data\items.json`
byte-idéntico antes/después (SHA-256 igual).

- Media:
  `ffmpeg -y -f lavfi -i testsrc=duration=3:size=320x240:rate=15 -f lavfi -i sine=frequency=440:duration=3 -c:v libx264 -pix_fmt yuv420p -c:a aac test.mp4`
  `ffmpeg -y -f lavfi -i testsrc=duration=30:size=320x240:rate=15 -f lavfi -i sine=frequency=440:duration=30 -c:v libx264 -pix_fmt yuv420p -c:a aac test_long.mp4`
  `ffmpeg -y -f lavfi -i sine=frequency=440:duration=3 test.wav`
- `python -m compileall app`: OK (config, main_window, theme, video_player_window compilan).
- Probe A — VideoPlayerWindow standalone: ALL PASS (35 checks)
  - estado inicial label "00:00 / 00:00", slider 0..0
  - set_duration(60000) → rango 0..60000, label "00:00 / 01:00";
    update_position(30000) → value 30000, label "00:30 / 01:00"
  - `_fmt_ms`: 0→"00:00", 61000→"01:01", 3599000→"59:59",
    3600000→"1:00:00", 3661000→"1:01:01"
  - set_playing_state(True) → checked, icono PAUSA blanco puro (fila 8:
    opacos x=4..11, todos (255,255,255)); set_playing_state(False) →
    deschecked, icono PLAY estándar (fila 8: opacos x=3..13, no blanco)
  - clic en el botón → play_toggled(True) y otro clic → play_toggled(False);
    set_playing_state NO emite (signals bloqueados)
  - clic en slider al 75% → value 44922 (≈45000) + seek_pressed +
    seek_released(44922); drag 25%→90% con botón izquierdo: update_position
    NO-OP mientras está pulsado, value sigue al cursor (14922→55484, la
    matemática final del handle es de Qt, igual que en time-slider), release
    → seek_released(≈54000)
  - reset() limpia label/rango/checked; close() → closed() emitido; show()
    tras close() → reutilizable (sin borrado)
- Probe B — MainWindow + MP4 real: ALL PASS (36 checks)
  - `_toggle_play(test.mp4)` → ventana visible con título "test.mp4",
    botón checked, player PlayingState, fila activa, barra principal OCULTA
    para vídeo, duración 3000 ms en el slider de la ventana
  - `_stop_playback()` → ventana oculta, fila restaurada
  - `_toggle_play(test_long.mp4)` con 30 s → MISMA instancia de ventana
    (reutilizada), PlayingState, checked, duración 30000
  - clic en botón de la ventana → PausedState + deschecked; seek por slider
    pulsado en pausa (press 15% + release) → position=4430 (target ~4500);
    clic → PlayingState + checked
  - `_toggle_play(mismo path)` desde la fila → PausedState + botón
    deschecked (sync por acción)
  - `_toggle_play(test.wav)` → ventana oculta SIN crash, audio PlayingState,
    barra principal visible (comportamiento previo intacto), fila wav activa
  - `_stop_playback()` → fila restaurada, "Listo.", StoppedState, botón
    reset; items.json byte-idéntico
- MainWindow.close() con vídeo en marcha → sin crash, ventana de vídeo oculta.
- Tema: QSS oscuro y claro contienen playButton (default/hover/pressed/
  checked/checked:hover/checked:pressed), videoTimeLabel, QVideoWidget y las
  superficies de la ventana; sin `$` sin sustituir.
- Hallazgos de smoke (no afectan al producto): `QTest.qWait` puede bloquear
  bajo entrega de frames de vídeo offscreen → los probes bombean eventos
  manualmente (processEvents + sleep); `QSignalSpy` en PySide6 6.11 no es
  subscriptable (usar `.at(i)`); el recolor del icono por
  `CompositionMode_DestinationIn` no produce el glifo blanco (rellena el
  rect completo) → se usa la máscara del glifo QStyle
  (`createMaskFromColor(transparent, MaskInColor)` + clip) + fill blanco.
- Commit: UN work-unit commit en `feature/video-playback` — mensaje
  convencional en español, sin atribución de IA. SHA: ver `git log` de la
  rama al cierre de la tarea.