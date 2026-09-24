"""QSS theming engine: dark and light palettes with a single apply helper.

No external dependencies: plain Qt Style Sheets + QPalette.
"""

from string import Template

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# -- settings ----------------------------------------------------------------
_SETTINGS_ORG = "Transcript"
_SETTINGS_APP = "Transcript"
_DARK_KEY = "ui/dark"


def load_dark_setting() -> bool:
    """Return the persisted dark-mode preference (default: dark)."""
    settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
    value = settings.value(_DARK_KEY, True)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


def save_dark_setting(dark: bool) -> None:
    """Persist the dark-mode preference."""
    settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
    settings.setValue(_DARK_KEY, bool(dark))


# -- palettes ----------------------------------------------------------------
DARK: dict[str, str] = {
    "bg": "#1b1d23",
    "surface": "#24262e",
    "surface_alt": "#2a2c35",
    "border": "#3a3c47",
    "border_hover": "#4e5060",
    "text": "#e8eaef",
    "text_muted": "#9b9eaa",
    "disabled": "#6c6f7b",
    "button_bg": "#33353f",
    "hover": "#3e414d",
    "pressed": "#2a2c34",
    "accent": "#4f8cff",
    "accent_hover": "#6da3ff",
    "accent_pressed": "#3b74db",
    "accent_text": "#ffffff",
    "selection": "rgba(79, 140, 255, 0.35)",
    "input_bg": "#171920",
    "scrollbar": "#4d505c",
    "scrollbar_hover": "#636675",
    "tooltip_bg": "#2e303a",
    "tooltip_text": "#e8eaef",
}

LIGHT: dict[str, str] = {
    "bg": "#f4f5f8",
    "surface": "#ffffff",
    "surface_alt": "#eceef3",
    "border": "#d6d8df",
    "border_hover": "#b6b9c4",
    "text": "#1e2026",
    "text_muted": "#5f636e",
    "disabled": "#a1a4ae",
    "button_bg": "#ffffff",
    "hover": "#f0f1f5",
    "pressed": "#e3e5eb",
    "accent": "#2f6fed",
    "accent_hover": "#4a84f5",
    "accent_pressed": "#2559c8",
    "accent_text": "#ffffff",
    "selection": "rgba(47, 111, 237, 0.22)",
    "input_bg": "#ffffff",
    "scrollbar": "#c6c9d2",
    "scrollbar_hover": "#a9acb7",
    "tooltip_bg": "#ffffff",
    "tooltip_text": "#1e2026",
}

# -- stylesheet ---------------------------------------------------------------
_QSS = Template(
    """
/* ===== Global ===== */
QMainWindow { background-color: $bg; }
QWidget#central { background-color: $bg; }
QWidget { color: $text; }
QLabel { background: transparent; }
QDialog, QMessageBox { background-color: $bg; }

/* ===== Labels ===== */
QLabel#hintLabel {
    color: $text_muted;
    font-size: 12px;
    padding: 2px;
}
QLabel#paneLabel {
    color: $text_muted;
    font-size: 12px;
    font-weight: 600;
    padding: 2px;
}

/* ===== Buttons ===== */
QPushButton {
    background-color: $button_bg;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 6px 16px;
    min-height: 20px;
}
QPushButton:hover { background-color: $hover; border-color: $border_hover; }
QPushButton:pressed { background-color: $pressed; }
QPushButton:disabled {
    background-color: $surface_alt;
    color: $disabled;
    border-color: $surface_alt;
}
QPushButton#primaryButton {
    background-color: $accent;
    border-color: $accent;
    color: $accent_text;
    font-weight: 600;
}
QPushButton#primaryButton:hover {
    background-color: $accent_hover;
    border-color: $accent_hover;
}
QPushButton#primaryButton:pressed {
    background-color: $accent_pressed;
    border-color: $accent_pressed;
}
QPushButton#primaryButton:disabled {
    background-color: $surface_alt;
    border-color: $surface_alt;
    color: $disabled;
    font-weight: 400;
}
QPushButton#themeButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 15px;
}
QPushButton#themeButton:hover { background: $hover; border-color: $border; }
QPushButton#themeButton:pressed { background: $pressed; }

/* ===== Checkbox ===== */
QCheckBox { spacing: 8px; }
QCheckBox:disabled { color: $disabled; }
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid $border;
    border-radius: 4px;
    background: $input_bg;
}
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; }
QCheckBox::indicator:checked:hover {
    background: $accent_hover;
    border-color: $accent_hover;
}
QCheckBox::indicator:disabled {
    background: $surface_alt;
    border-color: $border;
}

/* ===== List ===== */
QListWidget {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 4px;
    outline: none;
}
QListWidget::item {
    padding: 7px 10px;
    border-radius: 6px;
}
QListWidget::item:hover { background-color: $hover; }
QListWidget::item:selected { background-color: $selection; color: $text; }
QListWidget::item:selected:!active {
    background-color: $selection;
    color: $text;
}
QListWidget::item:disabled { color: $disabled; }

/* ===== Tree (media list) ===== */
QTreeWidget {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 4px;
    outline: none;
}
QTreeWidget::item {
    padding: 7px 10px;
    border-radius: 6px;
}
QTreeWidget::item:hover { background-color: $hover; }
QTreeWidget::item:selected { background-color: $selection; color: $text; }
QTreeWidget::item:selected:!active {
    background-color: $selection;
    color: $text;
}
QTreeWidget::item:disabled { color: $disabled; }

/* ===== Header (tree columns) ===== */
QHeaderView::section {
    background-color: $surface_alt;
    border: 1px solid $border;
    color: $text_muted;
    padding: 6px 8px;
    font-weight: 600;
}

/* ===== Progress bar ===== */
QProgressBar {
    background-color: $surface_alt;
    border: 1px solid $border;
    border-radius: 6px;
    text-align: center;
    color: $text;
    height: 20px;
    font-size: 12px;
}
QProgressBar::chunk {
    background-color: $accent;
    border-radius: 5px;
}

/* ===== Seek slider ===== */
QSlider::groove:horizontal {
    height: 5px;
    background: $surface_alt;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: $accent;
    border-radius: 2px;
}
QSlider::add-page:horizontal {
    background: $border;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: $accent;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::handle:horizontal:hover { background: $accent_hover; }
QSlider:disabled::handle:horizontal { background: $disabled; }

/* ===== Output ===== */
QPlainTextEdit, QTextEdit {
    background-color: $input_bg;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 8px;
    selection-background-color: $selection;
    selection-color: $text;
}
QAbstractScrollArea::corner { background: transparent; }

/* ===== Annotation popup editor ===== */
QFrame#annotationPopup {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: 8px;
}
QLabel#annotationPopupHint {
    color: $text_muted;
    font-size: 11px;
    padding: 0;
}

/* ===== Line edit (dialogs) ===== */
QLineEdit {
    background: $input_bg;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 6px 8px;
    selection-background-color: $selection;
    selection-color: $text;
}
QLineEdit:focus { border-color: $accent; }
QLineEdit:disabled { background: $surface_alt; color: $disabled; }

/* ===== Menus ===== */
QMenuBar {
    background-color: $surface;
    border-bottom: 1px solid $border;
    padding: 2px 6px;
}
QMenuBar::item {
    padding: 6px 10px;
    background: transparent;
    border-radius: 4px;
}
QMenuBar::item:selected { background: $hover; }
QMenuBar::item:pressed { background: $pressed; }
QMenu {
    background-color: $surface;
    border: 1px solid $border;
    padding: 4px;
}
QMenu::item {
    padding: 6px 24px 6px 20px;
    border-radius: 4px;
    margin: 1px 2px;
}
QMenu::item:selected { background-color: $accent; color: $accent_text; }
QMenu::item:disabled { color: $disabled; }
QMenu::separator {
    height: 1px;
    background: $border;
    margin: 4px 8px;
}

/* ===== Tooltips ===== */
QToolTip {
    background-color: $tooltip_bg;
    color: $tooltip_text;
    border: 1px solid $border;
    padding: 6px 8px;
    border-radius: 4px;
    font-size: 12px;
}

/* ===== Scrollbars ===== */
QScrollBar:vertical {
    background: $surface_alt;
    width: 12px;
    margin: 0;
    border-left: 1px solid $border;
}
QScrollBar::handle:vertical {
    background: $scrollbar;
    min-height: 32px;
    border-radius: 5px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover { background: $scrollbar_hover; }
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0;
    width: 0;
    background: transparent;
}
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal {
    background: $surface_alt;
    height: 12px;
    margin: 0;
    border-top: 1px solid $border;
}
QScrollBar::handle:horizontal {
    background: $scrollbar;
    min-width: 32px;
    border-radius: 5px;
    margin: 2px;
}
QScrollBar::handle:horizontal:hover { background: $scrollbar_hover; }
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {
    height: 0;
    width: 0;
    background: transparent;
}
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal { background: transparent; }
QScrollBar::corner { background: transparent; }
"""
)


# -- helpers ------------------------------------------------------------------
def _build_palette(colors: dict[str, str]) -> QPalette:
    """Map a palette dict onto a QPalette for native (non-QSS) rendering."""
    pal = QPalette()
    text = QColor(colors["text"])
    disabled = QColor(colors["disabled"])
    accent = QColor(colors["accent"])

    pal.setColor(QPalette.ColorRole.Window, QColor(colors["bg"]))
    pal.setColor(QPalette.ColorRole.WindowText, text)
    pal.setColor(QPalette.ColorRole.Base, QColor(colors["surface"]))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["surface_alt"]))
    pal.setColor(QPalette.ColorRole.Text, text)
    pal.setColor(QPalette.ColorRole.Button, QColor(colors["button_bg"]))
    pal.setColor(QPalette.ColorRole.ButtonText, text)
    pal.setColor(QPalette.ColorRole.BrightText, accent)
    pal.setColor(QPalette.ColorRole.Link, accent)
    pal.setColor(QPalette.ColorRole.LinkVisited, accent)
    pal.setColor(QPalette.ColorRole.Highlight, accent)
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(colors["accent_text"]))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors["tooltip_bg"]))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(colors["tooltip_text"]))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors["text_muted"]))

    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        pal.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    pal.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Button,
        QColor(colors["surface_alt"]),
    )
    pal.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Highlight,
        QColor(colors["surface_alt"]),
    )
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.HighlightedText, disabled)
    return pal


def apply_theme(app: QApplication, dark: bool = True) -> None:
    """Apply the dark or light theme (QPalette + QSS) to the whole app."""
    if not app.property("_theme_fusion"):
        # Fusion renders QSS consistently across platforms/styles.
        app.setStyle("Fusion")
        app.setProperty("_theme_fusion", True)
    colors = DARK if dark else LIGHT
    app.setPalette(_build_palette(colors))
    app.setStyleSheet(_QSS.substitute(colors))
