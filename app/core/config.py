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

# Automatic summarization (Ollama, local) ------------------------------------
# Modelo local usado por "Generar notas de IA"; requiere Ollama instalado con el
# modelo descargado (ollama pull <modelo>). La transcripción nunca sale de la
# máquina: todo el tráfico va a localhost.
SUMMARIZE_MODEL = os.getenv("SUMMARIZE_MODEL", "qwen-optimizado")


def _resolve_ollama_host() -> str:
    """Resolve the local Ollama client target.

    Ollama sirve en OLLAMA_HOST=0.0.0.0 cuando se configura para la red local,
    pero 0.0.0.0 es el destino "escuchar en todas las interfaces": ningún
    cliente puede conectarse a él. Esta app es cliente local, así que ese valor
    (muy común como variable de entorno) se mapea al localhost por defecto.
    """
    raw = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    if raw.strip("/") == "0.0.0.0" or raw.startswith(("0.0.0.0:", "0.0.0.0/")):
        return "http://localhost:11434"
    return raw


OLLAMA_HOST = _resolve_ollama_host()

# Timeout (segundos) por llamada a Ollama. La primera llamada tras un arranque
# en frío también carga el modelo en memoria, así que conviene que sea amplio,
# sobre todo con modelos grandes (p. ej. qwen-optimizado, 27B).
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "900"))

# Media ----------------------------------------------------------------------
SUPPORTED_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v",
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".opus", ".wma",
}

# Extensions that open the dedicated video playback window; everything else
# in SUPPORTED_EXTENSIONS is treated as audio (main-window seek bar only).
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}