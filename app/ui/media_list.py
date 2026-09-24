"""The media registry widget: drag & drop, context menu, alias & annotation editing."""

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from app.core.config import SUPPORTED_EXTENSIONS
from app.storage.repository import MediaRepository


class _PopupTextEdit(QTextEdit):
    """Multi-line editor inside the annotation popup.

    Esc cancels the popup (no save); Ctrl+Enter saves and closes. Everything
    else behaves like a plain text editor (wrap + scroll for long notes).
    """

    def __init__(self, popup: "_AnnotationEditorPopup"):
        super().__init__(popup)
        self._popup = popup
        self.setAcceptRichText(False)
        self.setPlaceholderText("Escribe aquí tu anotación…")

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._popup.cancel_and_close()
            event.accept()
            return
        if (
            event.key() == Qt.Key.Key_Return
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._popup.save_and_close()
            event.accept()
            return
        super().keyPressEvent(event)


class _AnnotationEditorPopup(QFrame):
    """Frameless popup text editor anchored above (or below) a media row.

    Auto-saves the new text when it closes (click outside or Ctrl+Enter);
    Escape cancels without saving. ``Qt.Popup`` makes it grab the mouse and
    close itself when the user clicks anywhere else.
    """

    save_requested = Signal(str)
    closed = Signal()

    def __init__(self, parent, text: str):
        super().__init__(
            parent.window(),
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Popup,
        )
        self.setObjectName("annotationPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._cancelled = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        hint = QLabel("Anotación · Ctrl+Enter guardar · Esc cancelar", self)
        hint.setObjectName("annotationPopupHint")
        layout.addWidget(hint)

        self._editor = _PopupTextEdit(self)
        self._editor.setPlainText(text)
        layout.addWidget(self._editor, 1)

    # -- public API ----------------------------------------------------------
    def grab_focus(self) -> None:
        """Give the editor focus and select its whole content."""
        self._editor.setFocus()
        self._editor.selectAll()

    def cancel_and_close(self) -> None:
        self._cancelled = True
        self.close()

    def save_and_close(self) -> None:
        self.close()  # closeEvent emits save_requested

    # -- events --------------------------------------------------------------
    def closeEvent(self, event) -> None:
        if not self._cancelled:
            self.save_requested.emit(self._editor.toPlainText())
        self.closed.emit()
        super().closeEvent(event)


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
    dialog on double click; column 1 ("Anotación") opens a big multi-line popup
    editor on click and the text is persisted per file via
    ``repository.update_annotation``.

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
        self._popup_saving = False  # popup save guard: avoid double persistence
        self._annotation_popup: _AnnotationEditorPopup | None = None
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
                    Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
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
        row = self._row_for_path(path)
        return self.topLevelItem(row) if row >= 0 else None

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
                # Single click on the annotation cell opens the big editor popup.
                index = self.indexAt(pos)
                if index.isValid() and index.column() == 1:
                    self._open_annotation_editor(item)
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

    # -- double click / annotation popup editing -----------------------------
    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if column == 0:
            # Alias → dialog (the _NoEditDelegate blocks inline editing here).
            self._edit_alias(item)
        elif column == 1:
            # Same popup as single click; the open guard prevents duplicates.
            self._open_annotation_editor(item)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """Persist the annotation when its text changes.

        Ignored while ``reload()`` is rebuilding the rows (``_loading`` guard)
        and while the popup is writing the value back (``_popup_saving`` guard)
        so loading/saving never double-writes ``items.json``.
        """
        if column == 1 and not self._loading and not self._popup_saving:
            row = self.indexOfTopLevelItem(item)
            if row >= 0:
                self.repository.update_annotation(row, item.text(1))

    # -- annotation popup editor ---------------------------------------------
    def _open_annotation_editor(self, item: QTreeWidgetItem) -> None:
        if self._annotation_popup is not None:
            return
        # Capture the path, not the item object: the row may be rebuilt while
        # the popup is open, so resolution happens again at save time.
        path = item.data(0, Qt.UserRole)
        popup = _AnnotationEditorPopup(self, item.text(1))
        popup.save_requested.connect(
            lambda text: self._save_annotation_path(path, text)
        )
        popup.closed.connect(self._on_annotation_popup_closed)
        popup.move(self._popup_position(item, popup.sizeHint()))
        popup.show()
        popup.grab_focus()
        self._annotation_popup = popup

    def _on_annotation_popup_closed(self) -> None:
        self._annotation_popup = None

    def _save_annotation_path(self, path: str, text: str) -> None:
        """Persist by file path and refresh the row (robust to item swaps)."""
        row = self._row_for_path(path)
        if row < 0:
            return
        self.repository.update_annotation(row, text)
        item = self.topLevelItem(row)
        self._popup_saving = True
        try:
            item.setText(1, text)
        finally:
            self._popup_saving = False

    def _row_for_path(self, path: str) -> int:
        for row in range(self.topLevelItemCount()):
            if self.topLevelItem(row).data(0, Qt.UserRole) == path:
                return row
        return -1

    def _popup_position(self, item: QTreeWidgetItem, size: QSize) -> QPoint:
        """Anchor the popup above the row; below it when there is no room."""
        screen = self.screen()
        avail = (
            screen.availableGeometry()
            if screen is not None
            else QRect(0, 0, 1920, 1080)
        )
        row_rect = self.visualItemRect(item)
        index = self.indexFromItem(item)
        col_rect = self.visualRect(index.sibling(index.row(), 1))
        if col_rect.isEmpty():
            col_rect = row_rect
        g_col = self.viewport().mapToGlobal(col_rect.center())
        g_row = self.viewport().mapToGlobal(row_rect.topLeft())
        x = g_col.x() - size.width() // 2
        x = max(avail.left() + 8, min(x, avail.right() - size.width() - 8))
        y = g_row.y() - size.height() - 6
        if y < avail.top() + 4:
            y = g_row.y() + row_rect.height() + 6
        y = max(avail.top() + 4, min(y, avail.bottom() - size.height() - 4))
        return QPoint(x, y)

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
