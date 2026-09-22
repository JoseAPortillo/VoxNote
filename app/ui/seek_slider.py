"""Interactive seek slider: click-to-position and drag-to-seek."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QSlider, QStyle


class SeekSlider(QSlider):
    """Horizontal QSlider where a left-click jumps to the clicked position
    and a left-button drag follows the cursor continuously.

    Default QSlider moves by page-step on click; this overrides that with
    ``QStyle.sliderValueFromPosition``. Standard signals (sliderPressed,
    sliderMoved, sliderReleased, valueChanged) are left untouched.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)

    def _value_at(self, x: int) -> int:
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), x, self.width()
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setValue(self._value_at(event.position().toPoint().x()))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.setValue(self._value_at(event.position().toPoint().x()))
        super().mouseMoveEvent(event)
