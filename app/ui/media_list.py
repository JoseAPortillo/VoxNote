"""The media registry widget: drag & drop, context menu, alias editing."""

from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QStyle,
    QStyleOptionViewItem,
)

from app.core.config import SUPPORTED_EXTENSIONS
from app.storage.repository import MediaRepository


class MediaList(QListWidget):
    """List of media entries backed by a :class:`MediaRepository`.

    Signals:
        items_changed: emitted after any repository mutation.
        alias_changed: emitted with (row, new_alias) after alias editing.
        play_requested: emitted with the path when the row's play icon is clicked.
    """

    items_changed = Signal()
    alias_changed = Signal(int, str)
    play_requested = Signal(str)

    def __init__(self, repository: MediaRepository, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.setAcceptDrops(True)
        self.setDragEnabled(False)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.itemDoubleClicked.connect(self._edit_alias)
        self._icon_play = self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        self._icon_pause = self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause)

    # -- public API ----------------------------------------------------------
    def reload(self) -> None:
        self.clear()
        for entry in self.repository.all():
            item = QListWidgetItem(entry.alias)
            item.setData(Qt.UserRole, entry.path)
            item.setToolTip(f"{entry.path}\nAñadido: {entry.added_at}")
            item.setIcon(self._icon_play)
            self.addItem(item)

    def selected_path(self) -> str | None:
        item = self.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def set_playing(self, path: str) -> None:
        """Flip the active row's icon to pause (playing)."""
        item = self._item_for_path(path)
        if item is not None:
            item.setIcon(self._icon_pause)

    def set_stopped(self, path: str) -> None:
        """Restore the play icon on the row for ``path``."""
        item = self._item_for_path(path)
        if item is not None:
            item.setIcon(self._icon_play)

    def _item_for_path(self, path: str) -> QListWidgetItem | None:
        for row in range(self.count()):
            item = self.item(row)
            if item.data(Qt.UserRole) == path:
                return item
        return None

    # -- icon click (playback) ------------------------------------------------
    def mouseReleaseEvent(self, event) -> None:
        """Emit :attr:`play_requested` when the left click lands on the row's
        play/pause icon; anything else keeps the default list behavior."""
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            item = self.itemAt(pos)
            if item is not None:
                opt = QStyleOptionViewItem()
                opt.initFrom(self)
                opt.rect = self.visualItemRect(item)
                opt.decorationSize = QSize(16, 16)
                opt.features = QStyleOptionViewItem.ViewItemFeature.HasDecoration
                icon_rect = self.style().subElementRect(
                    QStyle.SubElement.SE_ItemViewItemDecoration, opt, self
                )
                if icon_rect.isEmpty():
                    # Some styles (e.g. offscreen/common) return a null rect;
                    # fall back to the leading cell where the icon is drawn.
                    icon_rect = QRect(
                        opt.rect.left(), opt.rect.top(),
                        opt.decorationSize.width(), opt.rect.height(),
                    )
                if icon_rect.contains(pos):
                    self.play_requested.emit(item.data(Qt.UserRole))
                    event.accept()
                    return
        super().mouseReleaseEvent(event)

    def add_paths(self, paths: list[str]) -> None:
        """Register any number of dropped paths and refresh the view."""
        for path in paths:
            if Path(path).suffix.lower() in SUPPORTED_EXTENSIONS:
                self.repository.add(path)
        self.reload()
        self.items_changed.emit()

    # -- drag & drop ---------------------------------------------------------
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.toLocalFile()]
        self.add_paths(paths)

    # -- context menu --------------------------------------------------------
    def _on_context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        action_alias = menu.addAction("Cambiar alias…")
        action_copy = menu.addAction("Copiar ruta")
        action_remove = menu.addAction("Eliminar de la lista")
        chosen = menu.exec(self.viewport().mapToGlobal(pos))
        if chosen is action_alias:
            self._edit_alias(item)
        elif chosen is action_copy:
            QApplication.clipboard().setText(item.data(Qt.UserRole))
        elif chosen is action_remove:
            row = self.row(item)
            self.repository.remove(row)
            self.reload()
            self.items_changed.emit()

    def _edit_alias(self, item) -> None:
        row = self.row(item)
        alias, ok = QInputDialog.getText(
            self, "Alias", "Alias para el archivo:", text=item.text(),
        )
        alias = alias.strip() if ok else ""
        if not alias or alias == item.text():
            return
        self.repository.update_alias(row, alias)
        item.setText(alias)
        self.alias_changed.emit(row, alias)
        self.items_changed.emit()