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
# Estado por defecto de la casilla "Separar hablantes"; la diarización es opcional.
DIARIZE_SPEAKERS = os.getenv("DIARIZE_SPEAKERS", "false").lower() == "true"

# Automatic summarization (Gemini API) ---------------------------------------
# "Generar notas de IA" calls Google's Gemini through its REST API. A key from
# Google AI Studio (https://aistudio.google.com/apikey) is required; without it
# the feature stays disabled. The base text (translation or transcript) is sent
# to Google, so it leaves the machine: transcription and translation remain
# local, but note generation is online.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Default: the ``-latest`` alias follows the cheapest Flash-Lite release, so a
# versioned model being retired for new accounts does not disable the feature.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")

# Timeout (segundos) por llamada a Gemini. Streaming manda las notas según se
# generan, así que basta con un margen amplio para la respuesta completa.
GEMINI_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT", "120"))

# Media ----------------------------------------------------------------------
SUPPORTED_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v",
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".opus", ".wma",
}

# Extensions that open the dedicated video playback window; everything else
# in SUPPORTED_EXTENSIONS is treated as audio (main-window seek bar only).
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}