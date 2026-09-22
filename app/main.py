"""Application entry point.

Run from the project root with:  .venv\\Scripts\\python -m app.main
"""

import os
import sys

# torchcodec (usado por PyAnnote) necesita las DLL compartidas de FFmpeg en
# el PATH. Scoop las instala bajo apps/ffmpeg-shared/current/bin; si el proceso
# no las heredó (terminal abierta antes de instalarlas), las añadimos aquí.
_SCOOP_FFMPEG = os.path.expanduser(
    r"~\scoop\apps\ffmpeg-shared\current\bin"
)
if (
    os.path.isdir(_SCOOP_FFMPEG)
    and "ffmpeg-shared" not in os.environ.get("PATH", "")
):
    os.environ["PATH"] = _SCOOP_FFMPEG + os.pathsep + os.environ.get("PATH", "")

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