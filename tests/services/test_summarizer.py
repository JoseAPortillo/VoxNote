"""Tests for the notes generator helpers (pure, network-free) and the transport.

Run with::

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

The helpers were kept module-level pure functions so the block insertion and
the chunking logic can be pinned without an Ollama instance. The transport is
exercised against a throwaway local ``http.server`` so the real feature works
without Ollama being installed.
"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.services.summarizer import (
    GENERATED_MARKER,
    MANUAL_MARKER,
    NotesGenerator,
    OllamaUnavailableError,
    chunk_text,
    pack_partials,
    upsert_generated_block,
)


class ChunkingTests(unittest.TestCase):
    def test_short_text_is_a_single_chunk(self):
        text = "Una sola idea."
        self.assertEqual(chunk_text(text), [text])

    def test_long_text_is_split_into_bounded_chunks(self):
        paragraph = ("Párrafo con contenido útil. " * 40).strip()
        lines = "\n".join(f"{i}: {paragraph}" for i in range(40))
        chunks = chunk_text(lines)
        self.assertGreater(len(chunks), 1)
        # A closed chunk may carry the paragraph that tipped it over, so the
        # bound is the budget plus one paragraph's worth of slack.
        slack = len(paragraph) + 40
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 12_000 + slack)

    def test_every_paragraph_survives_the_split_once(self):
        paragraphs = [f"párrafo {i}" * 30 for i in range(80)]
        text = "\n".join(paragraphs)
        chunks = chunk_text(text)
        self.assertGreater(len(chunks), 1)
        joined = "\n".join(chunks)
        for paragraph in paragraphs:
            self.assertIn(paragraph, joined)

    def test_overlap_reuses_the_previous_tail(self):
        paragraph = "Palabra repetida en la cola. " * 4
        text = "\n".join(paragraph for _ in range(40))
        chunks = chunk_text(text, size=800, overlap=300)
        self.assertGreater(len(chunks), 1)
        # The first paragraph of a later chunk is the kept tail of the previous
        # chunk, so it must already appear there in full.
        for previous, current in zip(chunks[:-1], chunks[1:]):
            first_para = current.splitlines()[0]
            self.assertIn(first_para, previous)

    def test_empty_text_yields_one_empty_chunk(self):
        self.assertEqual(chunk_text("   \n\n  "), [""])


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


class NotesGeneratorEmptyInputTests(unittest.TestCase):
    def test_empty_input_needs_no_request(self):
        generator = NotesGenerator(host="http://127.0.0.1:1")
        self.assertEqual(generator.generate_notes(""), "")

    def test_blank_input_needs_no_request(self):
        generator = NotesGenerator(host="http://127.0.0.1:1")
        self.assertEqual(generator.generate_notes("   \n\n  "), "")


class PackPartialsTests(unittest.TestCase):
    def test_partials_are_labeled_and_joined(self):
        packed = pack_partials(["uno", "dos"])
        self.assertIn("Parte 1:", packed)
        self.assertIn("Parte 2:", packed)
        self.assertIn("dos", packed)


class _FakeOllamaHandler(BaseHTTPRequestHandler):
    response = (200, b'{"response": "las notas IA", "done": true}')
    last_path: str | None = None
    last_payload: dict | None = None

    def do_POST(self):
        type(self).last_path = self.path
        length = int(self.headers.get("Content-Length", 0))
        type(self).last_payload = json.loads(self.rfile.read(length) or b"{}")
        status, body = type(self).response
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class NotesGeneratorHttpTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllamaHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        _FakeOllamaHandler.response = (200, b'{"response": "las notas IA", "done": true}')
        _FakeOllamaHandler.last_path = None
        _FakeOllamaHandler.last_payload = None

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_generate_notes_returns_the_response_field(self):
        config = NotesGenerator(host=f"http://127.0.0.1:{self.port}")
        self.assertEqual(config.generate_notes("texto a analizar"), "las notas IA")
        self.assertEqual(_FakeOllamaHandler.last_path, "/api/generate")
        self.assertEqual(_FakeOllamaHandler.last_payload["model"], config.model)
        self.assertIn("texto a analizar", _FakeOllamaHandler.last_payload["prompt"])
        self.assertIn("toma", _FakeOllamaHandler.last_payload["prompt"])

    def test_scheme_less_host_is_normalized(self):
        config = NotesGenerator(host=f"127.0.0.1:{self.port}")
        self.assertEqual(config.generate_notes("texto"), "las notas IA")

    def test_http_error_raises_unavailable(self):
        _FakeOllamaHandler.response = (500, b"internal error")
        config = NotesGenerator(host=f"http://127.0.0.1:{self.port}")
        with self.assertRaises(OllamaUnavailableError):
            config.generate_notes("texto")

    def test_non_json_body_raises_unavailable(self):
        _FakeOllamaHandler.response = (200, b"not json")
        config = NotesGenerator(host=f"http://127.0.0.1:{self.port}")
        with self.assertRaises(OllamaUnavailableError):
            config.generate_notes("texto")

    def test_error_field_raises_unavailable(self):
        _FakeOllamaHandler.response = (200, b'{"error": "model not found"}')
        config = NotesGenerator(host=f"http://127.0.0.1:{self.port}")
        with self.assertRaises(OllamaUnavailableError):
            config.generate_notes("texto")

    def test_empty_response_field_raises_unavailable(self):
        _FakeOllamaHandler.response = (200, b'{"done": true}')
        config = NotesGenerator(host=f"http://127.0.0.1:{self.port}")
        with self.assertRaises(OllamaUnavailableError):
            config.generate_notes("texto")


if __name__ == "__main__":
    unittest.main()