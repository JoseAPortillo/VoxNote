"""The media registry widget: drag & drop, context menu and alias editing."""

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QInputDialog,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
)

from app.core.config import SUPPORTED_EXTENSIONS
from app.storage.repository import MediaRepository


class _PlayButtonDelegate(QStyledItemDelegate):
    """Owns the leading cell of a media row: play/pause icon + active shade.

    It is the single source of truth for where the play button is painted, so
    the click handler hits exactly what the user sees (no QSS sub-element
    drift). While the row is playing/paused the whole cell gets a translucent
    accent tint (pressed-state feedback) and the icon flips to pause.

    Inline editing of the column is disabled here so double-clicking the alias
    keeps going through the dialog (:meth:`MediaList._on_item_double_clicked`).
    """

    # Icon placed like the QSS item padding did (10px left, 7px top).
    ICON_OFFSET = QPoint(10, 7)
    ICON_SIZE = QSize(16, 16)
    # Horizontal room reserved for the icon so the alias text starts after it.
    TEXT_INDENT = 30

    def __init__(self, view: "MediaList", parent=None):
        super().__init__(view)
        self._view = view

    def icon_rect(self, option: QStyleOptionViewItem) -> QRect:
        """The exact rect where the play/pause icon is painted."""
        return QRect(
            option.rect.left() + self.ICON_OFFSET.x(),
            option.rect.top() + self.ICON_OFFSET.y(),
            self.ICON_SIZE.width(),
            self.ICON_SIZE.height(),
        )

    def createEditor(self, parent, option, index):
        return None

    def paint(self, painter, option, index) -> None:
        view = self._view
        playing = view.is_playing_path(index.data(Qt.UserRole))
        # 1) QSS background (hover/selection) + alias text, indented past the icon.
        text_opt = QStyleOptionViewItem(option)
        text_opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasDecoration
        text_opt.decorationSize = self.ICON_SIZE
        text_opt.rect = text_opt.rect.adjusted(self.TEXT_INDENT, 0, 0, 0)
        super().paint(painter, text_opt, index)
        # 2) Active (pressed) shade over the whole cell, translucent accent.
        if playing:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            tint = QColor(view.palette().color(QPalette.ColorRole.Highlight))
            tint.setAlpha(70)
            painter.setBrush(tint)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(option.rect.adjusted(2, 2, -2, -2), 6, 6)
            painter.restore()
        # 3) Icon on top: pause while active, play otherwise.
        icon = view._icon_pause if playing else view._icon_play
        icon.paint(painter, self.icon_rect(option))


class MediaList(QTreeWidget):
    """One-column table of media entries backed by a :class:`MediaRepository`.

    The column renders a play/pause button (delegate-drawn icon + pressed
    shade while active) and opens the alias dialog on double click. The notes
    themselves are edited in the dedicated notes pane to the right, not here.

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
        self.setColumnCount(1)
        self.setHeaderLabels(["Archivo"])
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        self.setUniformRowHeights(True)
        self.setAcceptDrops(True)
        self.setDragEnabled(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.itemDoubleClicked.connect(self._on_item_double_clicked)
        # Column 0 renders the play button through a delegate (icon + active
        # shade) and never opens an inline editor (alias → dialog only).
        self._play_delegate = _PlayButtonDelegate(self)
        self.setItemDelegateForColumn(0, self._play_delegate)
        self._playing_path: str | None = None
        header = self.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._icon_play = self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        self._icon_pause = self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause)

    # -- public API ----------------------------------------------------------
    def reload(self) -> None:
        self.clear()
        for entry in self.repository.all():
            item = QTreeWidgetItem()
            item.setFlags(
                Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
            )
            item.setData(0, Qt.UserRole, entry.path)
            item.setText(0, entry.alias)
            tooltip = f"{entry.path}\nAñadido: {entry.added_at}"
            item.setToolTip(0, tooltip)
            self.addTopLevelItem(item)

    def selected_path(self) -> str | None:
        item = self.currentItem()
        return item.data(0, Qt.UserRole) if item is not None else None

    def set_playing(self, path: str) -> None:
        """Mark ``path`` as active: pause icon + pressed shade until stopped."""
        self._playing_path = path
        self.viewport().update()

    def set_stopped(self, path: str) -> None:
        """Restore the normal row rendering for ``path``."""
        if self._playing_path == path:
            self._playing_path = None
            self.viewport().update()

    def is_playing_path(self, path: str) -> bool:
        return self._playing_path is not None and self._playing_path == path

    # -- icon click (playback) ------------------------------------------------
    def mouseReleaseEvent(self, event) -> None:
        """Emit :attr:`play_requested` when the left click lands on the row's
        play/pause button; anything else keeps the default view behavior."""
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            item = self.itemAt(pos)
            if item is not None:
                opt = QStyleOptionViewItem()
                opt.initFrom(self)
                opt.rect = self.visualItemRect(item)
                opt.decorationSize = _PlayButtonDelegate.ICON_SIZE
                opt.features = QStyleOptionViewItem.ViewItemFeature.HasDecoration
                # Hit zone = exactly the painted icon, with a little air around
                # it so real clicks never miss.
                hit_rect = self._play_delegate.icon_rect(opt).adjusted(-4, -4, 4, 4)
                if hit_rect.contains(pos):
                    self.play_requested.emit(item.data(0, Qt.UserRole))
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

    # -- double click ---------------------------------------------------------
    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if column == 0:
            # Alias → dialog (the delegate disables inline editing here).
            self._edit_alias(item)

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
            QApplication.clipboard().setText(item.data(0, Qt.UserRole))
        elif chosen is action_remove:
            row = self.indexOfTopLevelItem(item)
            self.repository.remove(row)
            self.reload()
            self.items_changed.emit()

    def _edit_alias(self, item: QTreeWidgetItem) -> None:
        row = self.indexOfTopLevelItem(item)
        alias, ok = QInputDialog.getText(
            self, "Alias", "Alias para el archivo:", text=item.text(0),
        )
        alias = alias.strip() if ok else ""
        if not alias or alias == item.text(0):
            return
        self.repository.update_alias(row, alias)
        item.setText(0, alias)
        self.alias_changed.emit(row, alias)
        self.items_changed.emit()