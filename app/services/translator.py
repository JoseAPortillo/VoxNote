"""Argos Translate wrapper: local, free machine translation.

Argos needs a sentence-boundary backend at import time; MiniSBD is enough for
inference, so the runtime never touches the stanza/torch fallback path.
"""

import app.core.config as _config  # noqa: F401  # load .env before argos reads settings

import argostranslate.package
import argostranslate.translate


def _languages() -> dict:
    return {lang.code: lang for lang in argostranslate.translate.get_installed_languages()}


def ensure_language_pair(src_code: str, tgt_code: str) -> None:
    """Install the Argos pair, downloading it on first use."""
    languages = _languages()
    if src_code in languages and tgt_code in languages:
        if languages[src_code].get_translation(languages[tgt_code]) is not None:
            return

    argostranslate.package.update_package_index()
    installed = argostranslate.package.install_package_for_language_pair(src_code, tgt_code)
    if not installed:
        raise ValueError(
            f"El par {src_code} → {tgt_code} no está disponible en Argos. "
            "Consulta los pares soportados con argostranslate.package.get_available_packages()."
        )


def translate_text(text: str, src_code: str, tgt_code: str) -> str:
    """Translate a block of text; installs the pair on first use."""
    if not text.strip():
        return ""
    ensure_language_pair(src_code, tgt_code)
    languages = _languages()
    translation = languages[src_code].get_translation(languages[tgt_code])
    return translation.translate(text)