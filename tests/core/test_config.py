"""Tests for resolution of the Ollama client host.

``OLLAMA_HOST=0.0.0.0`` is a common stray environment export: it is the
"listen on every interface" target that Ollama prints for LAN serving, but a
client cannot connect to it. The app is a local client, so that value (with or
without port) must fall back to the localhost default.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.core import config


class OllamaHostResolutionTests(unittest.TestCase):
    @patch.dict("os.environ", {"OLLAMA_HOST": "0.0.0.0"}, clear=False)
    def test_bare_any_interface_maps_to_localhost(self):
        self.assertEqual(config._resolve_ollama_host(), "http://localhost:11434")

    @patch.dict("os.environ", {"OLLAMA_HOST": "0.0.0.0:11434"}, clear=False)
    def test_any_interface_with_port_maps_to_localhost(self):
        self.assertEqual(config._resolve_ollama_host(), "http://localhost:11434")

    @patch.dict("os.environ", {"OLLAMA_HOST": "0.0.0.0/"}, clear=False)
    def test_any_interface_with_slash_maps_to_localhost(self):
        self.assertEqual(config._resolve_ollama_host(), "http://localhost:11434")

    @patch.dict("os.environ", {"OLLAMA_HOST": "127.0.0.1:11434"}, clear=False)
    def test_a_real_address_is_kept(self):
        self.assertEqual(config._resolve_ollama_host(), "127.0.0.1:11434")

    @patch.dict("os.environ", {"OLLAMA_HOST": "http://localhost:11434"}, clear=False)
    def test_the_default_form_is_kept(self):
        self.assertEqual(config._resolve_ollama_host(), "http://localhost:11434")


if __name__ == "__main__":
    unittest.main()