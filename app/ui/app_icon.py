"""Locate and expose the app logo.

Two shapes of the same artwork: a multi-resolution ``.ico`` for the window and
taskbar, and a 256px PNG for the in-app header and the README.

Every accessor here degrades to "no logo" instead of raising. A missing or
corrupt asset must never stop the app from starting -- branding is not worth a
traceback on boot, and the previous behaviour (Qt's default icon) is a perfectly
working fallback.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QLabel

ASSETS = Path(__file__).resolve().parent.parent / "assets"

# Resolved from __file__, never from the CWD: Transcript.bat happens to cd into
# the project root, but `python -m app.main` has to work from anywhere.
WINDOWS_ICON = ASSETS / "logo.ico"
PNG_ICON = ASSETS / "logo-256.png"

HEADER_LOGO_PX = 34


def _pick() -> Path | None:
    """The best available raster for the current platform.

    Windows gets the ``.ico`` because Qt reads its multiple sizes natively, which
    is what keeps the taskbar pin sharp at every DPI. Elsewhere the PNG is
    loaded once and scaled by Qt, which is fine for a window icon.
    """
    order = (
        [WINDOWS_ICON, PNG_ICON] if sys.platform == "win32" else [PNG_ICON, WINDOWS_ICON]
    )
    return next((p for p in order if p.is_file()), None)


def app_icon() -> QIcon:
    """The app icon, or a null ``QIcon`` when no asset is readable."""
    path = _pick()
    if path is None:
        return QIcon()
    icon = QIcon(str(path))
    # A QIcon over an unreadable file reports null too, so this catches the case
    # where the file exists but is corrupt rather than merely absent.
    return icon if not icon.isNull() else QIcon()


def apply_app_icon(app) -> None:
    """Give the application its icon, so every window inherits it."""
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)


def header_logo_label() -> QLabel | None:
    """The mark shown at the left of the main-window header.

    Returns ``None`` when there is no usable asset, so the caller can skip the
    widget entirely instead of laying out an empty gap.
    """
    icon = app_icon()
    if icon.isNull():
        return None
    label = QLabel()
    label.setObjectName("headerLogo")
    label.setPixmap(icon.pixmap(HEADER_LOGO_PX, HEADER_LOGO_PX))
    # The mark is decorative: the window title already names the app, so an
    # empty accessible name keeps it out of the screen reader's way instead of
    # announcing a duplicate "Transcript".
    label.setAccessibleName("")
    return label