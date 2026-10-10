"""Generation of structured review notes from transcriptions through the Gemini
API.

Unlike transcription and translation, which run on the machine, note
generation is online: the base text (translation when present, else the
transcript) is sent to Google's Gemini REST API. A key from Google AI Studio is
required (``GEMINI_API_KEY``); without it the feature stays disabled. The
default model, the ``gemini-flash-lite-latest`` alias, tracks the cheapest
Flash-Lite release so personal use keeps fitting the free tier even after a
versioned model is retired.

Gemini's 1M-token context swallows a whole transcript, so there is no chunking
and no map-reduce: one request produces the notes. The response is streamed, so
the notes appear as they are written instead of after a long silent wait.

The output is structured review notes about a work-review audio (a director /
lead commenting on a take): general comments and tone first (approval,
intention, reference/scoring choices), then topic-specific corrections grouped
into sub-topics with concise bullets that keep the option/take numbers and the
exact moment being discussed. The repeated line of the take playing in the
background is identified and excluded.
"""

import json
import urllib.error
import urllib.request
from collections.abc import Iterator

from app.core.config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_TIMEOUT,
    TRANSLATE_TARGET_LANG,
)

# Base of the Gemini REST API; the model and method are appended per request.
API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Upper bound for the generated notes. A work-review note is far shorter than
# this; the cap only stops a runaway generation.
MAX_OUTPUT_TOKENS = 8192

# A low temperature keeps the notes close to what was said instead of inventing.
TEMPERATURE = 0.3

# Markers that separate, inside a note, the AI-generated block from the
# manually written notes. Both survive regeneration of the AI block.
GENERATED_MARKER = "— Notas de IA —"
MANUAL_MARKER = "— Tus notas —"

# Small table for the prompt's language name; unknown codes fall back to the
# code itself, and the model usually still understands them.
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

_EXTRACT_INSTRUCTION = (
    "El texto siguiente es la transcripción de un audio de trabajo: una "
    "persona (el director o el lead) revisa un trabajo y da sus notas. "
    "IMPORTANTE: una línea corta se repite una y otra vez (la frase de la "
    "toma que están revisando). Esa línea es la toma sonando de fondo: NO es "
    "una nota ni una instrucción, úsala solo para saber de qué momento hablan "
    "y NO la incluyas en las notas. Extrae las notas con dos bloques: "
    "1) Comentarios generales y tono (aprobación general citando el texto si "
    "lo hay, intención de la escena o el trabajo, elección de referencias y "
    "de opciones numeradas); 2) Correcciones específicas por tema, agrupadas "
    "en sub-temas (por ejemplo, mirada y ojos, cuerpo y cabeza, cortes o "
    "edición por tomas), cada una en su viñeta e indicando la opción o el "
    "momento exacto a que se refiere. Cada corrección debe aparecer UNA sola "
    "vez. Escribe SOLO lo que se dice, sin inventar y sin mezclar el audio de "
    "fondo. Explica siempre en claro qué se pide y sobre qué parte de la "
    "toma; no dejes puntos que solo tengan una frase entre comillas o que den "
    "por hecho el contexto."
)


class NotesError(RuntimeError):
    """Notes could not be generated: no key, a transport error, or no text."""


class NotesGenerator:
    """Writes structured review notes about transcribed text via Gemini."""

    def __init__(
        self,
        api_key: str = GEMINI_API_KEY,
        model: str = GEMINI_MODEL,
        lang: str = TRANSLATE_TARGET_LANG,
    ):
        self.api_key = api_key.strip()
        self.model = model
        self.lang = lang
        self._lang_name = LANG_NAMES.get(lang, lang)
        self._timeout = GEMINI_TIMEOUT

    @property
    def configured(self) -> bool:
        """Whether an API key is present; without it no request is possible."""
        return bool(self.api_key)

    # -- public API ----------------------------------------------------------

    def generate_notes(self, text: str) -> str:
        """Return structured notes about ``text`` (all streaming pieces joined)."""
        return "".join(self.stream_notes(text))

    def stream_notes(self, text: str) -> Iterator[str]:
        """Yield the notes about ``text`` in the target language, piece by piece.

        Empty input yields nothing and sends no request. A missing key, a
        transport failure, or a response with no text raises :class:`NotesError`.
        """
        body = text.strip()
        if not body:
            return
        if not self.api_key:
            raise NotesError(
                "Falta la clave de la API de Gemini (GEMINI_API_KEY). "
                "Consíguela gratis en https://aistudio.google.com/apikey"
            )
        prompt = self._build_prompt(body)
        request = self._build_request(prompt)
        produced = False
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                for raw_line in response:
                    for piece in self._parse_sse_line(raw_line):
                        produced = True
                        yield piece
        except urllib.error.HTTPError as exc:
            raise NotesError(self._http_message(exc)) from exc
        except urllib.error.URLError as exc:
            raise NotesError(
                "No se pudo conectar con la API de Gemini. "
                "Comprueba tu conexión a internet."
            ) from exc
        if not produced:
            raise NotesError("Gemini no devolvió ningún texto.")

    # -- internals -----------------------------------------------------------

    def _build_prompt(self, body: str) -> str:
        return (
            f"{_EXTRACT_INSTRUCTION}\n\n"
            f"{_FORMAT_INSTRUCTION}\n"
            f"{_FORMAT_EXPLICIT.format(lang=self._lang_name)}\n\n"
            f"Texto:\n\n{body}"
        )

    def _build_request(self, prompt: str) -> urllib.request.Request:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": TEMPERATURE,
                "maxOutputTokens": MAX_OUTPUT_TOKENS,
            },
        }
        return urllib.request.Request(
            f"{API_BASE}/models/{self.model}:streamGenerateContent?alt=sse",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
            method="POST",
        )

    @staticmethod
    def _parse_sse_line(raw_line: bytes) -> Iterator[str]:
        """Yield the text pieces carried by one Server-Sent Events line.

        Only ``data:`` lines matter; the JSON may carry several candidates or
        parts. Reasoning parts (``thought: true``) are dropped so only the final
        notes are shown.
        """
        line = raw_line.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            return
        data = line[len("data:"):].strip()
        if not data or data == "[DONE]":
            return
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            return
        for candidate in obj.get("candidates", []) or []:
            content = candidate.get("content") or {}
            for part in content.get("parts", []) or []:
                if part.get("thought"):
                    continue
                piece = part.get("text")
                if piece:
                    yield piece

    @staticmethod
    def _http_message(exc: urllib.error.HTTPError) -> str:
        detail = ""
        try:
            payload = json.loads(exc.read().decode("utf-8", "replace"))
            detail = (payload.get("error") or {}).get("message") or ""
        except Exception:  # pragma: no cover - error body is best-effort
            detail = ""
        finally:
            try:
                exc.close()
            except Exception:  # pragma: no cover - closing is best-effort
                pass
        if exc.code == 401 or exc.code == 403:
            base = "La clave de la API de Gemini no es válida o no tiene permisos."
        elif exc.code == 429:
            base = "Se agotó la cuota de la API de Gemini (espera y reintenta)."
        else:
            base = f"La API de Gemini devolvió un error {exc.code}."
        return f"{base} {detail}".strip()


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
