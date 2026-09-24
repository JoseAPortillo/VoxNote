# Transcript — Local audio/video transcription & translation

A desktop app (Python + PySide6) that transcribes and translates audio/video
files **entirely on your machine** — no cloud, no uploads. It uses
faster-whisper for transcription, Argos Translate for translation, and
optional PyAnnote speaker diarization. Validated on Windows with Python 3.14
and NVIDIA CUDA.

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
- Optional speaker diarization (`[Speaker N]` per line)
- Dual panes: original transcription + translation, persisted per file
- Save results as TXT + SRT with timestamps
- Audio playback with a seek bar (play/pause per row)
- Per-file annotations (click the annotation cell to edit in a popup)
- Dark / light theme toggle

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
| `HF_TOKEN` | *(empty)* | Hugging Face token for speaker diarization (optional) |
| `DIARIZE_SPEAKERS` | `true` | Default state of the "Separar hablantes" checkbox |

## Speaker diarization (optional)

The PyAnnote model is **gated** — it requires a free Hugging Face account:

1. Create an account at <https://huggingface.co>.
2. Accept the model license: <https://huggingface.co/pyannote/speaker-diarization-3.1>.
3. Create a token at <https://huggingface.co/settings/tokens>.
4. Add it to your `.env`: `HF_TOKEN=hf_...`

Without a token the rest of the app works normally; enabling diarization shows a
clear error with these steps.

## First run notes

- Models download automatically on first use (internet required): whisper
  `large-v3-turbo` (~1.6 GB), the Argos translation pair, and the PyAnnote
  diarization model. Subsequent runs are fully offline.
- `data/` (your media list) and `.env` (your token) stay local and are
  git-ignored — nothing is shared with the repository.

## Privacy

Everything runs locally. Media files are never uploaded; the only external
communication is model downloads from Hugging Face (and only when a model is
still missing).

## Troubleshooting

- **CUDA wheels fail to install**: your Python must match the pinned torch
  build; see the instructions inside `requirements.txt` for the CPU fallback.
- **FFmpeg not found (PyAnnote)**: `app/main.py` adds Scoop's
  `ffmpeg-shared` to the path automatically; install it with
  `scoop install ffmpeg-shared` if missing.
- **Diarization errors**: check that `HF_TOKEN` is set and the license was
  accepted (steps above).