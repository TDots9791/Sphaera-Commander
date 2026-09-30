"""Панель файлового менеджера: путь, таблица, статус; асинхронное чтение каталога."""

from __future__ import annotations

import os
import threading
import time
import tarfile
import zipfile

from PySide6.QtCore import QFileSystemWatcher, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QCompleter,
    QHeaderView,
    QLabel,
    QMessageBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .archives import ArchiveBrowser, archive_format, norm_member
from .fsmodel import (
    FileEntry,
    FileTableModel,
    human_size,
    scan_directory,
    sort_entries,
)


class FileView(QTableView):
    """Таблица с TC-поведением клавиш: Tab, Enter, Insert, +, -, *."""

    switch_requested = Signal()
    entry_activated = Signal(object)  # FileEntry | None
    mask_requested = Signal(bool)     # True — отметить по маске, False — снять
    context_requested = Signal(object)  # QPoint

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._emit_context)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(22)
        header = self.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setHighlightSections(False)
        header.sectionClicked.connect(self._on_header_clicked)

    def _emit_context(self, pos) -> None:
        self.context_requested.emit(pos)

    def _on_header_clicked(self, section: int):
        self.model().apply_sort(section)
        self.scrollToTop()

    def set_current_row(self, row: int):
        row = max(0, min(row, self.model().rowCount() - 1))
        idx = self.model().index(row, 0)
        self.setCurrentIndex(idx)
        self.scrollTo(idx, QAbstractItemView.PositionAtCenter)

    def keyPressEvent(self, event):
        model = self.model()
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.entry_activated.emit(model.entry_at(self.currentIndex().row()))
            event.accept()
            return
        if key in (Qt.Key_Tab, Qt.Key_Backtab):
            self.switch_requested.emit()
            event.accept()
            return
        if key == Qt.Key_Insert:
            row = self.currentIndex().row()
            model.toggle_mark(row)
            self.set_current_row(row + 1)
            event.accept()
            return
        if key == Qt.Key_Plus:
            self.mask_requested.emit(True)
            event.accept()
            return
        if key == Qt.Key_Minus:
            self.mask_requested.emit(False)
            event.accept()
            return
        if key == Qt.Key_Asterisk:
            model.invert_marks()
            event.accept()
            return
        super().keyPressEvent(event)


class FilePanel(QWidget):
    """Одна панель; чтение каталога — в фоновом потоке, применяется по поколению."""

    path_changed = Signal(str)
    entry_activated = Signal(object)
    loaded = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = FileTableModel(self)
        self._gen = 0
        self._pending = ""
        self._loading = False
        self._reveal_name: str | None = None
        self._vfs: ArchiveBrowser | None = None
        self._vfs_dir = ""

        self.path_combo = QComboBox()
        self.path_combo.setInsertPolicy(QComboBox.NoInsert)
        self.path_combo.setEditable(True)
        self.path_combo.lineEdit().returnPressed.connect(self._on_path_edited)
        self.path_combo.activated.connect(lambda i: self.cd(self.path_combo.itemText(i)))

        self._completer = QCompleter(self)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.path_combo.setCompleter(self._completer)

        self.view = FileView()
        self.view.setModel(self.model)
        self.view.entry_activated.connect(self._on_entry_activated)
        self.view.doubleClicked.connect(
            lambda idx: self._on_entry_activated(self.model.entry_at(idx.row())))

        self.status_label = QLabel()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(1)
        layout.addWidget(self.path_combo)
        layout.addWidget(self.view)
        layout.addWidget(self.status_label)

        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_dir_changed)
        self._watcher.fileChanged.connect(self._on_dir_changed)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(150)
        self._refresh_timer.timeout.connect(self._watched_refresh)

        self.model.dataChanged.connect(lambda *_: self.update_status())
        self.model.modelReset.connect(self._on_model_reset)
        self.loaded.connect(self._on_loaded)
        self.cd(os.path.expanduser("~"), quiet=True)

    # -- навигация ---------------------------------------------------------

    def current_path(self) -> str:
        return self.model.path

    @property
    def is_vfs(self) -> bool:
        return self._vfs is not None

    @property
    def vfs(self) -> ArchiveBrowser | None:
        return self._vfs

    def cd(self, path: str, quiet: bool = False) -> bool:
        if "::" in path:
            archive, _, inner = path.partition("::")
            return self._cd_vfs(archive, inner, quiet)
        target = os.path.abspath(os.path.expanduser(path))
        if os.path.isfile(target) and archive_format(target):
            return self._cd_vfs(target, "", quiet)
        self._vfs = None
        self._vfs_dir = ""
        if not os.path.isdir(target):
            if not quiet:
                QMessageBox.warning(self, "Переход", f"Каталог не найден:\n{target}")
            return False
        self._rewatch(target)
        self._pending = target
        self._gen += 1
        self.path_changed.emit(target)
        self._start_scan(target, self._gen)
        return True

    def _cd_vfs(self, archive: str, inner: str, quiet: bool) -> bool:
        archive = os.path.abspath(os.path.expanduser(archive))
        try:
            if self._vfs is None or self._vfs.archive_path != archive:
                self._vfs = ArchiveBrowser(archive)
            self._vfs.list_dir(inner)  # проверка существования каталога
        except (ValueError, OSError, KeyError,
                zipfile.BadZipFile, tarfile.TarError) as exc:
            self._vfs = None
            self._vfs_dir = ""
            if not quiet:
                QMessageBox.warning(self, "Архив", f"Не удалось открыть:\n{exc}")
            return False
        self._vfs_dir = norm_member(inner)
        self._rewatch(archive)
        self._pending = f"{archive}::{self._vfs_dir}"
        self._gen += 1
        self.path_changed.emit(self._pending)
        self._start_scan(self._pending, self._gen)
        return True

    def _rewatch(self, path: str) -> None:
        if self._watcher.directories():
            self._watcher.removePaths(self._watcher.directories())
        if self._watcher.files():
            self._watcher.removePaths(self._watcher.files())
        self._watcher.addPath(path)

    def up(self) -> None:
        if self._vfs is not None:
            if self._vfs_dir:
                parent = self._vfs_dir.rsplit("/", 1)[0] if "/" in self._vfs_dir else ""
                self._cd_vfs(self._vfs.archive_path, parent, quiet=True)
            else:
                self.cd(os.path.dirname(self._vfs.archive_path))
            return
        parent = os.path.dirname(self.current_path())
        if parent != self.current_path():
            self.cd(parent)

    def refresh(self) -> None:
        self._start_scan(self._pending, self._gen)

    def reveal(self, name: str) -> None:
        """Перечитать каталог и поставить курсор на объект с данным именем."""
        self._reveal_name = name
        self._start_scan(self._pending, self._gen)

    def wait_loaded(self, timeout_s: float = 5.0) -> None:
        """Дождаться применения отложенной загрузки (для тестов/скриптов)."""
        deadline = time.monotonic() + timeout_s
        while self._loading and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.005)

    # -- фоновое чтение ------------------------------------------------------

    def _start_scan(self, path: str, gen: int) -> None:
        show_hidden = self.model.show_hidden
        col, desc = self.model.sort_col, self.model.sort_desc
        self._loading = True
        self.update_status()

        if "::" in path:
            archive, _, inner = path.partition("::")
            browser = self._vfs
            if browser is None or browser.archive_path != archive:
                try:
                    browser = ArchiveBrowser(archive)
                except (ValueError, OSError, zipfile.BadZipFile,
                        tarfile.TarError) as exc:
                    self._loading = False
                    self.loaded.emit({"gen": gen, "path": path, "entries": [],
                                      "error": str(exc)})
                    return
                self._vfs = browser

            def worker():
                try:
                    entries = browser.list_dir(inner, show_hidden)
                    sort_entries(entries, col, desc)
                    error = ""
                except (KeyError, OSError, ValueError,
                        zipfile.BadZipFile, tarfile.TarError) as exc:
                    entries, error = [], str(exc)
                self.loaded.emit({"gen": gen, "path": path,
                                  "entries": entries, "error": error})
        else:
            def worker():
                try:
                    entries = scan_directory(path, show_hidden)
                    sort_entries(entries, col, desc)
                    error = ""
                except OSError as exc:
                    entries, error = [], str(exc)
                self.loaded.emit({"gen": gen, "path": path,
                                  "entries": entries, "error": error})

        threading.Thread(target=worker, daemon=True, name="panel-scan").start()

    def _on_loaded(self, payload: dict) -> None:
        if payload["gen"] != self._gen or payload["path"] != self._pending:
            return  # устаревший результат более раннего запроса
        self._loading = False
        if payload["error"]:
            self.status_label.setText(f"не удалось прочитать каталог: {payload['error']}")
            return
        keep = self._reveal_name or self._cursor_name()
        self._reveal_name = None
        self.model.set_entries(payload["path"], payload["entries"])
        self._restore_cursor(keep)
        self._update_combo()
        self._update_completer()
        self.update_status()

    def _watched_refresh(self) -> None:
        self.refresh()

    def _on_dir_changed(self, _path: str) -> None:
        self._refresh_timer.start()

    def _on_path_edited(self) -> None:
        self.cd(self.path_combo.lineEdit().text())

    def _on_entry_activated(self, entry: FileEntry | None) -> None:
        if entry is None:
            if self.view.currentIndex().row() == 0:
                self.up()
            return
        if entry.name == "..":
            self.up()
            return
        if entry.is_dir:
            self.cd(entry.path)
        elif not self.is_vfs and archive_format(entry.path):
            self.cd(entry.path)  # вход в архив с диска (VFS)
        else:
            self.entry_activated.emit(entry)

    # -- курсор и отметки ----------------------------------------------------

    def _cursor_name(self) -> str | None:
        e = self.model.entry_at(self.view.currentIndex().row())
        return e.name if e else None

    def _restore_cursor(self, name: str | None) -> None:
        row = self.model.row_of_name(name) if name else 0
        self.view.set_current_row(row)

    def reveal_name(self, name: str) -> None:
        self._restore_cursor(name)

    def current_entry(self) -> FileEntry | None:
        row = self.view.currentIndex().row()
        if row == 0:
            return None
        return self.model.entry_at(row)

    def selected_entries(self) -> list[FileEntry]:
        """Отмеченные объекты; если отметок нет — текущий под курсором."""
        marked = self.model.marked_entries()
        if marked:
            return marked
        cur = self.current_entry()
        return [cur] if cur else []

    def select_all(self, on: bool) -> None:
        for e in self.model.entries:
            if not e.is_dir:
                self.model.set_mark(e.name, on)
        self.update_status()

    def select_mask(self, pattern: str, on: bool) -> int:
        n = self.model.mark_mask(pattern, on)
        self.update_status()
        return n

    def invert_selection(self) -> None:
        self.model.invert_marks()
        self.update_status()

    def set_show_hidden(self, on: bool) -> None:
        self.model.show_hidden = on
        self.refresh()

    def set_active(self, active: bool) -> None:
        self.path_combo.setStyleSheet(
            "QComboBox { font-weight: bold; }" if active else "")
        font = self.status_label.font()
        font.setBold(active)
        self.status_label.setFont(font)

    # -- состояние ------------------------------------------------------------

    def update_status(self) -> None:
        if self._loading:
            self.status_label.setText("⏳ чтение каталога…")
            return
        files, dirs, total, m_count, m_bytes = self.model.summary()
        text = f"файлов: {files}   папок: {dirs}   {human_size(total)}"
        if m_count:
            text += f"   •   отмечено: {m_count} ({human_size(m_bytes)})"
        self.status_label.setText(text)

    def _on_model_reset(self) -> None:
        self.view.resizeColumnToContents(1)
        self.view.resizeColumnToContents(2)
        self.view.resizeColumnToContents(3)

    def _update_combo(self) -> None:
        self.path_combo.blockSignals(True)
        self.path_combo.clear()
        self.path_combo.addItem(self.current_path())
        self.path_combo.setCurrentIndex(0)
        self.path_combo.blockSignals(False)
        self.path_combo.lineEdit().setText(self.current_path())

    def _update_completer(self) -> None:
        from PySide6.QtCore import QStringListModel

        dirs = sorted(
            e.name + os.sep for e in self.model.entries
            if e.is_dir and not e.name.startswith(".")
        )
        self._completer.setModel(QStringListModel(dirs, self))
