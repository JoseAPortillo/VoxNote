"""The media registry widget: drag & drop, context menu, alias & annotation editing."""

from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, Signal
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


class _NoEditDelegate(QStyledItemDelegate):
    """Delegate that never creates an editor.

    Installed on column 0 so double-clicking the file name does NOT start an
    inline edit: the alias keeps being edited through the "Cambiar alias…"
    dialog handled by :meth:`MediaList._on_item_double_clicked`.
    """

    def createEditor(self, parent, option, index):
        return None


class MediaList(QTreeWidget):
    """Two-column table of media entries backed by a :class:`MediaRepository`.

    Column 0 ("Archivo") shows the play icon + alias and only opens the alias
    dialog on double click; column 1 ("Anotación") is edited inline and the
    text is persisted per file via ``repository.update_annotation``.

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
        self._loading = False  # reload() guard: suppress itemChanged persistence
        self.setColumnCount(2)
        self.setHeaderLabels(["Archivo", "Anotación"])
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
        self.itemChanged.connect(self._on_item_changed)
        # Column 0 must never open an inline editor (alias → dialog only).
        self.setItemDelegateForColumn(0, _NoEditDelegate(self))
        header = self.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.setColumnWidth(0, 300)
        self._icon_play = self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        self._icon_pause = self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause)

    # -- public API ----------------------------------------------------------
    def reload(self) -> None:
        self._loading = True
        try:
            self.clear()
            for entry in self.repository.all():
                item = QTreeWidgetItem()
                item.setFlags(
                    Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsEditable
                )
                item.setData(0, Qt.UserRole, entry.path)
                item.setText(0, entry.alias)
                item.setIcon(0, self._icon_play)
                item.setText(1, entry.annotation or "")
                tooltip = f"{entry.path}\nAñadido: {entry.added_at}"
                item.setToolTip(0, tooltip)
                item.setToolTip(1, tooltip)
                self.addTopLevelItem(item)
        finally:
            self._loading = False

    def selected_path(self) -> str | None:
        item = self.currentItem()
        return item.data(0, Qt.UserRole) if item is not None else None

    def set_playing(self, path: str) -> None:
        """Flip the active row's icon to pause (playing)."""
        item = self._item_for_path(path)
        if item is not None:
            item.setIcon(0, self._icon_pause)

    def set_stopped(self, path: str) -> None:
        """Restore the play icon on the row for ``path``."""
        item = self._item_for_path(path)
        if item is not None:
            item.setIcon(0, self._icon_play)

    def _item_for_path(self, path: str) -> QTreeWidgetItem | None:
        for row in range(self.topLevelItemCount()):
            item = self.topLevelItem(row)
            if item.data(0, Qt.UserRole) == path:
                return item
        return None

    # -- icon click (playback) ------------------------------------------------
    def mouseReleaseEvent(self, event) -> None:
        """Emit :attr:`play_requested` when the left click lands on the row's
        play/pause icon; anything else keeps the default view behavior."""
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

    # -- double click / inline editing ---------------------------------------
    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if column == 0:
            # Alias → dialog (the _NoEditDelegate blocks inline editing here).
            self._edit_alias(item)
        elif column == 1:
            # Reinforce the native inline edit start on the annotation cell.
            self.editItem(item, 1)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """Persist the annotation when its inline edit is committed.

        Ignored while ``reload()`` is rebuilding the rows (``_loading`` guard)
        so loading never rewrites ``items.json``.
        """
        if column == 1 and not self._loading:
            row = self.indexOfTopLevelItem(item)
            if row >= 0:
                self.repository.update_annotation(row, item.text(1))

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
