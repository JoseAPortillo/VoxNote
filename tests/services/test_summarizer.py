"""Tests for the notes generator helpers and its Gemini transport.

Run with::

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

The block upsert is pinned without any network. The transport is exercised
against a throwaway local ``http.server`` that speaks Server-Sent Events, so the
real feature works without a Gemini key or internet.
"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.services.summarizer import (
    GENERATED_MARKER,
    MANUAL_MARKER,
    NotesError,
    NotesGenerator,
    upsert_generated_block,
)


class UpsertGeneratedBlockTests(unittest.TestCase):
    def test_empty_notes_get_only_the_generated_block(self):
        self.assertEqual(
            upsert_generated_block("", "Texto con notas"),
            f"{GENERATED_MARKER}\nTexto con notas",
        )

    def test_manual_notes_moved_under_manual_marker(self):
        result = upsert_generated_block("mis notas", "notas IA")
        self.assertTrue(result.startswith(f"{GENERATED_MARKER}\nnotas IA\n\n{MANUAL_MARKER}\n"))
        self.assertIn("mis notas", result)

    def test_existing_generated_block_is_replaced(self):
        existing = f"{GENERATED_MARKER}\nnotas viejas\n\n{MANUAL_MARKER}\nnota manual"
        result = upsert_generated_block(existing, "notas nuevas")
        self.assertIn("notas nuevas", result)
        self.assertNotIn("notas viejas", result)
        self.assertIn("nota manual", result)

    def test_regeneration_preserves_manual_notes_after_the_marker(self):
        result = upsert_generated_block(
            f"{GENERATED_MARKER}\nalgunas notas\n\nnota suelta\n\n{MANUAL_MARKER}\nnota real", "nuevas"
        )
        self.assertIn("nota real", result)
        # Content left of the manual marker is considered part of the generated
        # block only when a marker actually exists; after regeneration only the
        # manual block survives as user content.
        self.assertNotIn("nota suelta", result)

    def test_regeneration_without_manual_marker_keeps_no_leftovers(self):
        existing = f"{GENERATED_MARKER}\nviejo\nmas texto suelto"
        result = upsert_generated_block(existing, "nuevo")
        self.assertEqual(result, f"{GENERATED_MARKER}\nnuevo")

    def test_generated_text_is_stripped_before_insertion(self):
        result = upsert_generated_block("", "  con espacios  ")
        self.assertEqual(result, f"{GENERATED_MARKER}\ncon espacios")


class NotesGeneratorKeyTests(unittest.TestCase):
    def test_empty_key_is_not_configured(self):
        self.assertFalse(NotesGenerator(api_key="").configured)

    def test_present_key_is_configured(self):
        self.assertTrue(NotesGenerator(api_key="abc").configured)

    def test_missing_key_raises_without_any_request(self):
        generator = NotesGenerator(api_key="")
        with self.assertRaises(NotesError):
            generator.generate_notes("texto a analizar")

    def test_empty_input_needs_no_request_and_no_key(self):
        generator = NotesGenerator(api_key="")
        self.assertEqual(generator.generate_notes(""), "")
        self.assertEqual(generator.generate_notes("   \n\n  "), "")


class _FakeGeminiHandler(BaseHTTPRequestHandler):
    status = 200
    body = b""
    last_path: str | None = None
    last_key: str | None = None
    last_payload: dict | None = None

    def do_POST(self):
        type(self).last_path = self.path
        type(self).last_key = self.headers.get("x-goog-api-key")
        length = int(self.headers.get("Content-Length", 0))
        type(self).last_payload = json.loads(self.rfile.read(length) or b"{}")
        self.send_response(type(self).status)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(type(self).body)

    def log_message(self, *args):
        pass


def _sse(*objects: dict) -> bytes:
    return "".join(f"data: {json.dumps(obj)}\n\n" for obj in objects).encode("utf-8")


def _text_event(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class NotesGeneratorHttpTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeGeminiHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        _FakeGeminiHandler.status = 200
        _FakeGeminiHandler.body = _sse(_text_event("las notas IA"))
        _FakeGeminiHandler.last_path = None
        _FakeGeminiHandler.last_key = None
        _FakeGeminiHandler.last_payload = None
        self.generator = NotesGenerator(
            api_key="clave-de-prueba",
            model="gemini-2.5-flash-lite",
        )

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _run(self, text: str) -> str:
        # Point the generator at the fake server instead of Google.
        original_base = "https://generativelanguage.googleapis.com/v1beta"
        import app.services.summarizer as S

        S.API_BASE = f"http://127.0.0.1:{self.port}"
        try:
            return self.generator.generate_notes(text)
        finally:
            S.API_BASE = original_base

    def test_generate_notes_returns_the_streamed_text(self):
        self.assertEqual(self._run("texto a analizar"), "las notas IA")

    def test_request_targets_the_model_with_sse(self):
        self._run("texto a analizar")
        self.assertIn("gemini-2.5-flash-lite", _FakeGeminiHandler.last_path)
        self.assertIn("streamGenerateContent", _FakeGeminiHandler.last_path)
        self.assertIn("alt=sse", _FakeGeminiHandler.last_path)

    def test_request_carries_the_api_key_header(self):
        self._run("texto a analizar")
        self.assertEqual(_FakeGeminiHandler.last_key, "clave-de-prueba")

    def test_prompt_contains_the_text_and_the_background_line_hint(self):
        self._run("texto a analizar")
        prompt = _FakeGeminiHandler.last_payload["contents"][0]["parts"][0]["text"]
        self.assertIn("texto a analizar", prompt)
        self.assertIn("toma", prompt)

    def test_streaming_yields_pieces_in_order(self):
        _FakeGeminiHandler.body = _sse(
            _text_event("Hola "), _text_event("mundo")
        )
        import app.services.summarizer as S

        S.API_BASE = f"http://127.0.0.1:{self.port}"
        try:
            pieces = list(self.generator.stream_notes("texto"))
        finally:
            S.API_BASE = "https://generativelanguage.googleapis.com/v1beta"
        self.assertEqual(pieces, ["Hola ", "mundo"])

    def test_thought_parts_are_dropped(self):
        _FakeGeminiHandler.body = _sse(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "razonando...", "thought": True},
                                {"text": "notas visibles"},
                            ]
                        }
                    }
                ]
            }
        )
        self.assertEqual(self._run("texto"), "notas visibles")

    def test_non_data_lines_are_ignored(self):
        _FakeGeminiHandler.body = b": keep-alive\n\ndata: not json\n\n" + _sse(_text_event("ok"))
        self.assertEqual(self._run("texto"), "ok")

    def test_http_error_raises_notes_error(self):
        _FakeGeminiHandler.status = 500
        _FakeGeminiHandler.body = b'{"error": {"message": "boom"}}'
        with self.assertRaises(NotesError):
            self._run("texto")

    def test_invalid_key_error_mentions_permissions(self):
        _FakeGeminiHandler.status = 403
        _FakeGeminiHandler.body = b'{"error": {"message": "denied"}}'
        with self.assertRaises(NotesError) as ctx:
            self._run("texto")
        self.assertIn("no es válida", str(ctx.exception))

    def test_rate_limit_error_is_reported(self):
        _FakeGeminiHandler.status = 429
        _FakeGeminiHandler.body = b'{"error": {"message": "quota"}}'
        with self.assertRaises(NotesError) as ctx:
            self._run("texto")
        self.assertIn("cuota", str(ctx.exception))

    def test_empty_stream_raises_notes_error(self):
        _FakeGeminiHandler.body = _sse(
            {"candidates": [{"content": {"parts": []}}]}
        )
        with self.assertRaises(NotesError):
            self._run("texto")


if __name__ == "__main__":
    unittest.main()
