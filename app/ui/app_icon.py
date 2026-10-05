"""Locate and expose the app logo.

Two shapes of the same artwork: a multi-resolution ``.ico`` for the window and
taskbar, and a 256px PNG for the in-app header and the README.

Every accessor here degrades to "no logo" instead of raising. A missing or
corrupt asset must never stop the app from starting -- branding is not worth a
traceback on boot, and the previous behaviour (Qt's default icon) is a perfectly
working fallback.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QLabel

ASSETS = Path(__file__).resolve().parent.parent / "assets"

# Windows caps an AppUserModelID at 128 characters and allows only letters,
# digits, dots and dashes.
APP_USER_MODEL_ID = "Transcript.Desktop"

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


def apply_app_user_model_id() -> bool:
    """Give this process its own Windows app identity. Returns True on success.

    Without an explicit AppUserModelID, Windows derives one from the executable
    path. Running under ``pythonw.exe`` therefore makes the shell treat the app
    as "python": the taskbar button, the Alt+Tab entry and the task grouping all
    borrow pythonw's embedded logo, and no amount of ``setWindowIcon`` changes
    that, because those are resolved from the app identity rather than from the
    window. Measured: the window hands Windows a valid ``WM_GETICON`` handle and
    the taskbar still showed the Python logo.

    Must run before the first window exists, or the shell has already filed the
    taskbar entry under the old identity.
    """
    try:
        shell32 = ctypes.WinDLL("shell32")
        set_id = shell32.SetCurrentProcessExplicitAppUserModelID
        set_id.argtypes = [ctypes.c_wchar_p]
        set_id.restype = ctypes.c_long
        return set_id(APP_USER_MODEL_ID) == 0
    except (OSError, AttributeError, ValueError):
        # Branding is not worth refusing to start over: on a shell without this
        # API the app simply keeps the old taskbar behaviour.
        return False


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