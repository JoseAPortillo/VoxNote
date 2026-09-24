"""Application configuration.

Settings come from environment variables with sane defaults. Copy
``.env.example`` to ``.env`` (in the project root) to override them.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Paths ----------------------------------------------------------------------
DATA_DIR = PROJECT_ROOT / "data"
ITEMS_FILE = DATA_DIR / "items.json"

# Whisper --------------------------------------------------------------------
# Model sizes: tiny, base, small, medium, large-v3, large-v3-turbo,
# or a Hugging Face repo id of a distil-whisper / fine-tuned model.
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3-turbo")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")          # auto | cpu | cuda
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "auto")  # auto|int8|float16|float32
WHISPER_BEAM_SIZE = int(os.getenv("WHISPER_BEAM_SIZE", "5"))

# Translation ----------------------------------------------------------------
TRANSLATE_TARGET_LANG = os.getenv("TRANSLATE_TARGET_LANG", "es")  # Argos code

# Speaker diarization --------------------------------------------------------
# HF_TOKEN: token gratuito de Hugging Face (requerido para descargar el modelo
# gated de PyAnnote; la licencia debe aceptarse en huggingface.co/pyannote/...).
HF_TOKEN = os.getenv("HF_TOKEN", "")
# Marca por defecto del checkbox "Separar hablantes" en la interfaz.
DIARIZE_SPEAKERS = os.getenv("DIARIZE_SPEAKERS", "true").lower() == "true"

# Media ----------------------------------------------------------------------
SUPPORTED_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v",
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".opus", ".wma",
}

# Extensions that open the dedicated video playback window; everything else
# in SUPPORTED_EXTENSIONS is treated as audio (main-window seek bar only).
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}