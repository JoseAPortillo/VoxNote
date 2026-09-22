"""Application entry point.

Run from the project root with:  .venv\\Scripts\\python -m app.main
"""

import sys

from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Transcript")
    app.setOrganizationName("Transcript")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())