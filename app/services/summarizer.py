"""Generation of structured, review-style notes from transcriptions through a
local Ollama model.

The notes are computed entirely on the machine: the service talks to an
Ollama instance on ``localhost`` (configurable via ``OLLAMA_HOST``), so no
transcribed text ever leaves the computer. The model is picked per file with
``SUMMARIZE_MODEL`` (default ``qwen-optimizado``); install Ollama and pull that
model to use the feature.

The output is structured review notes about a work-review audio (a director /
lead commenting on a take): general comments and tone first (approval,
intention, reference/scoring choices), then topic-specific corrections grouped
into sub-topics with concise bullets that keep the option/take numbers and the
exact moment being discussed. The repeated line of the take playing in the
background is identified and excluded. Long transcripts exceed a small model's
context, so the text is split into paragraph chunks that are turned into notes
independently and then consolidated into a single final structure
(map-reduce).
"""

import json
import urllib.error
import urllib.request

from app.core.config import (
    OLLAMA_HOST,
    OLLAMA_TIMEOUT,
    SUMMARIZE_MODEL,
    TRANSLATE_TARGET_LANG,
)

# Approximate character budget per chunk. It is deliberately below typical
# 8K-token contexts so the model has room for instructions and the notes.
CHUNK_CHARS = 12_000
CHUNK_OVERLAP = 1_000
# Above this the text is reduced into chunks instead of notes-written whole.
REDUCE_CHARS = 16_000

# qwen-optimizado (Qwen 27B local) is a reasoning model: it spends a token
# budget on a "thinking" phase before the final answer. Its Modelfile caps the
# context at 4096 tokens, which a 12k-char chunk already exhausts: the model
# then fills the remaining room with thinking and returns an empty response
# (done_reason=length, no final text). Both the context window and the output
# allowance are therefore raised explicitly per request.
NUM_CONTEXT = 32_768
MAX_OUTPUT_TOKENS = 8192

# Markers that separate, inside a note, the AI-generated block from the
# manually written notes. Both survive regeneration of the AI block.
GENERATED_MARKER = "— Notas de IA —"
MANUAL_MARKER = "— Tus notas —"

# Small table for the prompt's language name; unknown codes fall back to the
# code itself, and Ollama usually still understands them.
LANG_NAMES = {
    "es": "español",
    "en": "inglés",
    "fr": "francés",
    "de": "alemán",
    "it": "italiano",
    "pt": "portugués",
    "ca": "catalán",
    "eu": "euskera",
    "gl": "gallego",
}

# Shared format instruction: the style example is deliberately about a DIFFERENT
# topic so the model copies the shape (section titles, "Etiqueta:" bullets,
# sub-topics) and never its content. The animation-take theme is only a
# possibility; the sub-topics must come from the transcript itself.
_FORMAT_INSTRUCTION = (
    "Formato de salida (ejemplo de FORMA sobre otro tema; copia solo la forma, "
    "NUNCA el contenido):\n"
    "Comentarios Generales y Tono\n"
    "Aprobación general: El proyecto en general gusta y la dirección está clara.\n"
    "Intención del proyecto: Debe comunicar cercanía y profesionalismo.\n"
    "Puntuación de referencias: Se usa la propuesta B como base, combinada "
    "con la propuesta A para el cierre.\n"
    "\n"
    "Correcciones Específicas\n"
    "Texto y Mensajes:\n"
    "- Suavizar el tono del primer párrafo; eliminar frases demasiado "
    "contundentes.\n"
    "- Mantener la idea de no usar tecnicismos.\n"
    "Imágenes y Gráficos:\n"
    "- Reducir el número de elementos decorativos; priorizar la claridad "
    "visual.\n"
    "Ajustes por Propuesta:\n"
    "- Propuesta B (mitad del documento): base para la primera y segunda "
    "sección.\n"
    "- Cierre: transición de la propuesta B a la propuesta A.\n"
)

_FORMAT_EXPLICIT = (
    "\nCopia SOLO esa forma: secciones con título en su propia línea, puntos "
    "breves con etiqueta \"Etiqueta:\" y sub-temas. NO copies su contenido: "
    "cada sección, etiqueta y sub-tema debe salir de lo que se dice en el "
    "texto que te doy, y si un detalle del ejemplo no está en ese texto no lo "
    "incluyas. Incluye TODAS las correcciones concretas que se mencionen. "
    "Cita solo frases cortas del audio si aportan exactitud.\n"
    "CLARIDAD SOBRE TODO: cada punto debe entenderse SOLO leyendo la nota, "
    "sin el audio ni contexto previo. Escribe primero la explicación en claro "
    "(qué se pide, sobre qué parte o momento de la toma), y usa la cita solo "
    "como apoyo al final, nunca como contenido único. Si el audio es ambiguo "
    "o se refiere a \"esto\", \"ahí\", \"un poco\", \"esa parte\", concreta "
    "a qué se refiere en el propio punto. Traduce e interpreta, no te limites "
    "a copiar: una nota que solo repite una cita en bruto es inaceptable.\n"
    "Devuelve SOLO las notas en {lang}, sin introducción, sin resumen y sin "
    "repetir ningún punto: cada corrección una sola vez."
)


class OllamaUnavailableError(RuntimeError):
    """Ollama could not be reached, reported an error, or returned no text."""


def chunk_text(
    text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP
) -> list[str]:
    """Split ``text`` into paragraph chunks of at most ``size`` chars.

    Consecutive chunks share an overlapping paragraph tail so a sentence
    straddling a seam is still seen in full by the next chunk's notes. The
    joined result preserves every paragraph exactly once, in order.
    """
    paragraphs = [p for p in text.splitlines() if p.strip()]
    if not paragraphs:
        return [""]
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        if current and current_len + len(para) + 1 > size:
            chunks.append("\n".join(current))
            # Keep the tail of the finished chunk (char-bounded) as overlap.
            kept: list[str] = []
            kept_len = 0
            for previous in reversed(current):
                if kept_len + len(previous) + 1 > overlap:
                    break
                kept.append(previous)
                kept_len += len(previous) + 1
            current = list(reversed(kept))
            current_len = kept_len
        current.append(para)
        current_len += len(para) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def pack_partials(partials: list[str]) -> str:
    """Join chunk notes into a labeled body for the consolidation pass."""
    return "\n\n".join(f"Parte {i + 1}:\n{part}" for i, part in enumerate(partials))


def upsert_generated_block(notes: str, generated: str) -> str:
    """Return ``notes`` with ``generated`` in an AI-notes block at the top.

    Manual notes live in their own 'Tus notas' block below it, untouched.
    ``notes`` that already start with the AI-notes marker have that block
    replaced (regenerate in place); anything after the manual marker is
    preserved. With no notes at all only the AI-notes block is produced.
    """
    text = generated.strip()
    lines = notes.splitlines()
    if lines and lines[0].strip() == GENERATED_MARKER:
        idx_manual = next(
            (i for i, line in enumerate(lines) if line.strip() == MANUAL_MARKER),
            None,
        )
        rest = lines[idx_manual:] if idx_manual is not None else []
        block = [GENERATED_MARKER, text]
        if rest:
            return "\n".join(block + [""] + rest)
        return "\n".join(block)
    manual = notes.strip()
    block = [GENERATED_MARKER, text]
    if manual:
        return "\n".join(block) + f"\n\n{MANUAL_MARKER}\n{manual}"
    return "\n".join(block)


class NotesGenerator:
    """Writes structured review notes about transcribed text via Ollama."""

    def __init__(
        self,
        host: str = OLLAMA_HOST,
        model: str = SUMMARIZE_MODEL,
        lang: str = TRANSLATE_TARGET_LANG,
    ):
        host = host.rstrip("/")
        if not host.startswith(("http://", "https://")):
            host = "http://" + host
        self.host = host
        self.model = model
        self.lang = lang
        self._lang_name = LANG_NAMES.get(lang, lang)
        self._timeout = OLLAMA_TIMEOUT

    # -- public API ----------------------------------------------------------

    def generate_notes(self, text: str) -> str:
        """Return structured notes about ``text`` in the target language.

        Long texts are processed chunk-wise first and consolidated into one
        final structure. An empty input returns an empty output without any
        request.
        """
        text = text.strip()
        if not text:
            return ""
        chunks = chunk_text(text)
        if len(chunks) <= 1:
            return self._call(chunks[0])
        partials = [self._call(chunk) for chunk in chunks]
        return self._call(pack_partials(partials), final_pass=True)

    # -- internals -----------------------------------------------------------

    def _call(self, body: str, final_pass: bool = False) -> str:
        if final_pass:
            instruction = (
                "Las partes siguientes son notas ya extraídas de un audio "
                "largo de revisión. Consolídalas en UNA sola estructura de "
                "notas con el mismo formato y estilo del ejemplo: comentarios "
                "generales y tono arriba, correcciones específicas agrupadas "
                "por tema con sub-temas, eliminando duplicados y conservando "
                "los números de opción o toma y los momentos exactos."
            )
            label = "Partes:"
        else:
            instruction = (
                "El texto siguiente es la transcripción de un audio de "
                "trabajo: una persona (el director o el lead) revisa un "
                "trabajo y da sus notas. IMPORTANTE: una línea corta se "
                "repite una y otra vez (la frase de la toma que están "
                "revisando). Esa línea es la toma sonando de fondo: NO es una "
                "nota ni una instrucción, úsala solo para saber de qué "
                "momento hablan y NO la incluyas en las notas. Extrae las "
                "notas con dos bloques: 1) Comentarios generales y tono "
                "(aprobación general citando el texto si lo hay, intención de "
                "la escena o el trabajo, elección de referencias y de "
                "opciones numeradas); 2) Correcciones específicas por tema, "
                "agrupadas en sub-temas (por ejemplo, mirada y ojos, cuerpo y "
                "cabeza, cortes o edición por tomas), cada una en su viñeta e "
                "indicando la opción o el momento exacto a que se refiere. "
                "Cada corrección debe aparecer UNA sola vez. Escribe SOLO lo "
                "que se dice, sin inventar y sin mezclar el audio de fondo. "
                "Explica siempre en claro qué se pide y sobre qué parte de la "
                "toma; no dejes puntos que solo tengan una frase entre "
                "comillas o que den por hecho el contexto."
            )
            label = "Texto:"
        prompt = (
            f"{instruction}\n\n"
            f"{_FORMAT_INSTRUCTION}\n"
            f"{_FORMAT_EXPLICIT.format(lang=self._lang_name)}\n\n"
            f"{label}\n\n{body}"
        )
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": MAX_OUTPUT_TOKENS,
                "num_ctx": NUM_CONTEXT,
            },
        }
        request = urllib.request.Request(
            f"{self.host}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise OllamaUnavailableError(self._connection_message()) from exc
        except (json.JSONDecodeError, OSError) as exc:
            raise OllamaUnavailableError(
                "Respuesta inesperada de Ollama."
            ) from exc
        if data.get("error"):
            raise OllamaUnavailableError(data["error"])
        result = (data.get("response") or "").strip()
        if not result:
            raise OllamaUnavailableError(
                "Ollama no devolvió ningún texto. "
                f"(done={data.get('done')}, done_reason={data.get('done_reason')}, "
                f"eval_count={data.get('eval_count')}, "
                f"thinking_len={len(data.get('thinking') or '')})"
            )
        return result

    def _connection_message(self) -> str:
        return (
            f"No se pudo conectar con Ollama en {self.host}. "
            "Comprueba que Ollama esté en marcha."
        )