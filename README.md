<img src="app/assets/logo-256.png" alt="Transcript logo" width="128" align="right">

# Transcript — Local audio/video transcription & translation

A desktop app (Python + PySide6) that transcribes and translates audio/video
files on the local machine. Transcription, translation, and optional speaker
diarization run locally, and media files are never uploaded. It uses
faster-whisper for transcription, Argos Translate for translation, and
optional PyAnnote for speaker diarization. Validated on Windows with Python
3.14 and NVIDIA CUDA.

## Quick path

1. Clone the repository.
2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate        # Windows
   source .venv/bin/activate     # Linux / macOS
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Copy the configuration template (optional, most defaults work out of the box):
   ```bash
   copy .env.example .env        # Windows
   cp .env.example .env          # Linux / macOS
   ```
5. Run the app:
   ```bash
   python -m app.main
   ```
   On Windows you can also double-click `Transcript.bat` (opens without a console window).

## Features

- Drag & drop media files (mp4, mkv, webm, mov, avi, m4v, mp3, wav, m4a, flac, ogg, aac, opus, wma)
- Transcribe (original language) or translate (default target: Spanish — configurable)
- Set a single slice on the timeslider to transcribe or translate only a fragment of a long file; it persists, and a run never consumes it
- Playback is bound to the slice: play enters it, the `⟳` control loops it or stops at its end, and seeking stays free anywhere on the line
- Optional speaker diarization with `[Hablante N]` labels; it must be explicitly enabled and requires `HF_TOKEN`
- Dual panes: original transcription + translation, persisted per file
- Freely editable multiline notes in both panes, autosaved per media file
- Opt-in proportional `Sincronizar scroll` checkbox, enabled when the selected file has both transcription and translation
- Audio playback in the main window; video playback in a dedicated window with a video surface, play/pause, seek, and time display
- Per-file aliases, annotations, original/translated text, and timestamped segments
- Save results as TXT + SRT with timestamps
- Dark / light theme toggle; the preference persists across launches

## Using the app

### Add and organize files

Drag and drop supported media into the list. Double-click a file name, or use
the context menu, to edit its alias. Click the annotation cell to open a
multi-line annotation popup: `Ctrl+Enter` saves and closes it, `Esc` cancels
without saving, and clicking outside auto-saves. Right-click a file for
`Cambiar alias…`, `Copiar ruta`, and `Eliminar de la lista`.

### Transcribe, translate, and diarize

Use **Transcribe** for the original language or **Translate** to fill both
panes. `Separar hablantes` is unchecked by default (`DIARIZE_SPEAKERS=false`).
Diarization is opt-in: enable it explicitly and set `HF_TOKEN`. When enabled,
grouped output uses the `[Hablante N]` label.

### Compare results and play media

The original transcription and translation remain in separate panes. The
unchecked-by-default `Sincronizar scroll` checkbox synchronizes their vertical
positions proportionally; it is available only when the selected file has both
transcription and translation.

Audio playback uses the main window, with a seek bar and elapsed/total time.
Video files open a dedicated window with a video surface, play/pause, seek, and
elapsed/total time controls; double-clicking the video image toggles play/pause.

### Mark a slice to transcribe or translate

Long recordings often only need a fragment. A media has **one slice**, drawn as a
shaded band over the seek bar of the audio player and of the video window, with a
`■>` start handle and a `<■` end handle. Drag either handle to set its edge. Drag
both back to the extremes and the slice is gone again, which is the same as never
having set one.

A run processes only that slice and timestamps the result against the **original**
file, so the generated SRT lines up with the full media. Translation uses the slice
the same way. With no slice the whole file is processed and behavior is unchanged.

A slice belongs to the media **currently loaded** — the one you hear — and persists
across restarts. **A run does not consume it.** The slice is your standing intent
for that media, not a one-shot ticket for a single execution: transcribe the same
slice twice and both runs cover the same fragment, instead of the second one
quietly widening to the whole file. That also applies to a failed run.

Playback is bound to the same slice. Pressing play enters it from its start, and
at the end playback either loops or returns to the start and pauses, depending on
the `⟳` control in the control row (audio) or in the video window. The loop is a
listening preference for this session and is never stored, so reopening a media
never plays it bounded. Seeking stays free: you can park the cursor anywhere on
the line, anywhere outside the slice.

The run itself follows the **selected** row in the list, while the slice geometry
follows the loaded media's duration. If those two differ — media A is loaded and
has a slice, but you have selected row B — the run falls back to the whole file
and the status bar names both files, instead of quietly processing a different
file than the one you sliced.

### Edit and export notes

Both result panes are freely editable multiline documents. Changes autosave per
selected media file, including an intentional empty pane, and are restored when
you switch media or reopen the app. The original inference text and timestamped
segments remain unchanged underneath the edited documents. If a new inference
would replace edited text, the app asks for confirmation; a failed run keeps the
notes.

Use **Guardar texto editado…** to export the current documents as UTF-8 TXT with
stable `TRANSCRIPCIÓN` and `TRADUCCIÓN` sections. **Guardar TXT + SRT…** remains
the separate export for the original timestamped base output.

### Persist and export

For each file, `data/items.json` stores its alias, annotation, original and
translated text, language metadata, timestamped segments with start/end times,
text, and optional speaker labels. It also stores optional edited transcript
and translation documents separately: a missing field means no edit, while an
empty string records a deliberate clear. Selecting a file restores the edited
panes when present, otherwise the original text. **Guardar TXT + SRT…** writes
timestamped TXT and SRT files; **Guardar texto editado…** writes only the current
edited documents. The dark/light theme choice is persisted through Qt settings
and restored on the next launch.

## Hardware notes

| Setup | What to do |
| --- | --- |
| **Windows + NVIDIA GPU (recommended)** | Install as-is. `requirements.txt` pins CUDA 13 wheels (`torch==2.14.0+cu130`); diarization runs on the GPU. |
| **CPU only / other platforms** | Edit `requirements.txt`: replace `+cu130` with `+cpu` on the three `torch*` lines and remove the `--extra-index-url` line. Transcriptions still work, just slower. |

`WHISPER_DEVICE=auto` uses CUDA when available and falls back to CPU otherwise.

## Configuration (`.env`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `WHISPER_MODEL` | `large-v3-turbo` | Model size (`tiny`, `base`, `small`, `medium`, `large-v3`, `large-v3-turbo`) or a HF repo id |
| `WHISPER_DEVICE` | `auto` | `auto` \| `cpu` \| `cuda` |
| `WHISPER_COMPUTE_TYPE` | `auto` | `auto` \| `int8` \| `float16` \| `float32` |
| `WHISPER_BEAM_SIZE` | `5` | Beam search width |
| `TRANSLATE_TARGET_LANG` | `es` | Argos output language code |
| `HF_TOKEN` | *(empty)* | Hugging Face token required when speaker diarization is enabled |
| `DIARIZE_SPEAKERS` | `false` | Default state of the "Separar hablantes" checkbox; diarization is opt-in and requires `HF_TOKEN` when enabled |

An existing value in `.env` takes precedence over the code default.

## Speaker diarization (opt-in)

Speaker diarization is disabled by default: `DIARIZE_SPEAKERS` is `false`, and
the **Separar hablantes** checkbox must be enabled explicitly. Enabling it
requires `HF_TOKEN`.

The PyAnnote model is **gated** and requires a free Hugging Face account:

1. Create an account at <https://huggingface.co>.
2. Accept the model license: <https://huggingface.co/pyannote/speaker-diarization-3.1>.
3. Create a token at <https://huggingface.co/settings/tokens>.
4. Add it to your `.env`: `HF_TOKEN=hf_...`

When enabled, grouped output uses the `[Hablante N]` label. Without a token the
rest of the app works normally; enabling diarization shows a clear error with
these steps.

## First run notes

The first use may require internet access to download resources that are not
already cached:

- faster-whisper may download the configured Whisper model (`large-v3-turbo`,
  ~1.6 GB by default).
- Argos may update its package index and download the requested source-to-target
  language pair.
- PyAnnote may download its gated diarization model when diarization is enabled.

After the required resources are cached, transcription, translation, and
diarization run locally. `data/` (your media list) and `.env` (your token) stay
local and are git-ignored — nothing is shared with the repository.

## Privacy

Processing and media files stay local. The app does not upload media or send it
to a cloud service. Network access is limited to first-use resource retrieval:
the faster-whisper model, Argos's package index and language pair, and the
PyAnnote model when diarization is enabled. Cached resources are used locally.

## Troubleshooting

- **CUDA wheels fail to install**: your Python must match the pinned torch
  build; see the instructions inside `requirements.txt` for the CPU fallback.
- **FFmpeg not found (PyAnnote)**: `app/main.py` adds Scoop's
  `ffmpeg-shared` to the path automatically; install it with
  `scoop install ffmpeg-shared` if missing.
- **Diarization errors**: check that `HF_TOKEN` is set and the license was
  accepted (steps above).