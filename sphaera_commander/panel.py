"""Панель файлового менеджера: путь, таблица, статус; асинхронное чтение каталога."""

from __future__ import annotations

import os
import queue
import threading
import time
import tarfile
import zipfile

from PySide6.QtCore import QEvent, QFileSystemWatcher, QSize, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QCompleter,
    QHeaderView,
    QLabel,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTableView,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .archives import ArchiveBrowser, archive_format, norm_member
from . import thumbnails
from .buttonbar import ButtonBar
from .fsmodel import (
    EXT_COL,
    DirStats,
    FileEntry,
    FileTableModel,
    NAME_COL,
    dir_stats,
    human_size,
    scan_directory,
    sort_entries,
)
from .i18n import tr, unit


class FileView(QTableView):
    """Таблица с TC-поведением клавиш: Tab, Enter, Insert, +, -, *;
    перетаскивание файлов из панели и приём drop'ов."""

    switch_requested = Signal()
    delete_requested = Signal(bool)  # True — безвозвратно (Shift+Del)
    entry_activated = Signal(object)  # FileEntry | None
    mask_requested = Signal(bool)     # True — отметить по маске, False — снять
    context_requested = Signal(object)  # QPoint
    drop_requested = Signal(list, str, bool)  # пути, каталог, Shift=перенос

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
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.CopyAction)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(22)
        # колонки всегда подгоняются под ширину панели; не влезающие имена
        # показываются всплывающим бейджем (см. name_tooltip)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        header = self.horizontalHeader()
        header.setStretchLastSection(False)
        header.setHighlightSections(False)
        header.sectionClicked.connect(self._on_header_clicked)

    def apply_column_layout(self):
        """Колонки по ширине панели: «Имя» забирает всё свободное место,
        «Расш.» узкая фиксированная, остальные — по содержимому.
        Вызывать после setModel(): он сбрасывает режимы заголовка."""
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(EXT_COL, QHeaderView.Fixed)
        header.setSectionResizeMode(NAME_COL, QHeaderView.Stretch)
        self.setColumnWidth(EXT_COL, 64)

    def _emit_context(self, pos) -> None:
        self.context_requested.emit(pos)

    # -- drag & drop ---------------------------------------------------------

    def _drop_target_dir(self, pos) -> str:
        index = self.indexAt(pos)
        entry = self.model().entry_at(index.row()) if index.isValid() else None
        if entry is not None and entry.is_dir:
            return entry.path
        return self.model().path

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if not event.mimeData().hasUrls():
            super().dropEvent(event)
            return
        paths = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.isLocalFile()]
        if not paths:
            return
        target = self._drop_target_dir(event.position().toPoint())
        move = bool(event.modifiers() & Qt.ShiftModifier)  # TC: Shift — перенос
        event.acceptProposedAction()
        self.drop_requested.emit(paths, target, move)

    def _on_header_clicked(self, section: int):
        self.model().apply_sort(section)
        self.scrollToTop()

    def name_tooltip(self, idx) -> str:
        """Полное имя, если оно не влезает в колонку «Имя», иначе пусто."""
        if not idx.isValid() or idx.row() <= 0 or idx.column() != NAME_COL:
            return ""
        text = self.model().data(idx, Qt.DisplayRole) or ""
        available = self.visualRect(idx).width() - self.iconSize().width() - 8
        if available <= 0:
            return ""
        return text if self.fontMetrics().horizontalAdvance(text) > available else ""

    def event(self, ev):
        # всплывающий бейдж с полным именем вместо усечённого
        if ev.type() == QEvent.ToolTip:
            idx = self.indexAt(ev.pos())
            tip = self.name_tooltip(idx)
            if tip:
                QToolTip.showText(ev.globalPos(), tip, self, self.visualRect(idx))
                return True
        return super().event(ev)

    def mousePressEvent(self, event):
        # Ctrl+клик — отметить/снять отметку, курсор не двигается (как в TC)
        if (event.button() == Qt.LeftButton
                and event.modifiers() & Qt.ControlModifier):
            idx = self.indexAt(event.position().toPoint())
            if idx.isValid() and idx.row() > 0:
                self.model().toggle_mark(idx.row())
                event.accept()
                return
        super().mousePressEvent(event)

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
        if key == Qt.Key_Delete:
            # TC: Del — в корзину, Shift+Del — безвозвратно
            self.delete_requested.emit(
                bool(event.modifiers() & Qt.ShiftModifier))
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
    cursor_changed = Signal(object)  # FileEntry | None — для быстрого просмотра
    loaded = Signal(object)
    drop_requested = Signal(list, str, bool)  # [пути], каталог, Shift=перенос
    dir_info_ready = Signal(str, object)  # путь, DirStats — из фонового воркера
    dir_size_ready = Signal(str, int, int)  # путь, размер, поколение
    drive_menu_requested = Signal()  # клик по кнопке дисков

    def __init__(self, parent=None):
        super().__init__(parent)
        # ниже этой ширины имена перестают читаться: панель не сжимается
        self.setMinimumWidth(400)
        self.model = FileTableModel(self)
        self._gen = 0
        self._pending = ""
        self._loading = False
        self._reveal_name: str | None = None
        self._vfs: ArchiveBrowser | None = None
        self._vfs_dir = ""
        # история навигации (Alt+←/→, Alt+↓ — список)
        self._history: list[str] = []
        self._hist_pos = -1

        self.drive_button = QPushButton()
        self.drive_button.setToolTip(tr("Диски и облака (меню подключения)"))
        self.drive_button.clicked.connect(self.drive_menu_requested.emit)

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
        self.view.apply_column_layout()  # после setModel: он сбрасывает режимы
        self.view.selectionModel().currentRowChanged.connect(self._on_cursor_changed)
        self.view.entry_activated.connect(self._on_entry_activated)
        self.view.doubleClicked.connect(
            lambda idx: self._on_entry_activated(self.model.entry_at(idx.row())))
        self.view.drop_requested.connect(self.drop_requested)

        self.status_label = QLabel()

        path_row = QHBoxLayout()
        path_row.setSpacing(2)
        path_row.addWidget(self.drive_button)
        path_row.addWidget(self.path_combo, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(1)
        layout.addLayout(path_row)
        self.button_bar = ButtonBar(self)
        layout.addWidget(self.button_bar)
        layout.addWidget(self.view)
        layout.addWidget(self.status_label)

        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_dir_changed)
        self._watcher.fileChanged.connect(self._on_dir_changed)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(150)
        self._refresh_timer.timeout.connect(self._watched_refresh)

        self._dirinfo_cache: dict[str, DirStats] = {}
        self._dirinfo_lock = threading.Lock()
        self._dirinfo_wanted: str | None = None
        self._dirinfo_wake = threading.Event()
        threading.Thread(target=self._dirinfo_worker, daemon=True,
                         name="panel-dirinfo").start()
        self.dir_info_ready.connect(self._on_dir_info_ready)
        # размеры каталогов (по включению «Вид → Размеры каталогов»)
        self.dirsizes_on = False
        self._dirsize_queue: queue.Queue = queue.Queue()
        self._dirsize_pending: set[str] = set()
        self._dirsize_lock = threading.Lock()
        threading.Thread(target=self._dirsize_worker, daemon=True,
                         name="panel-dirsize").start()
        self.dir_size_ready.connect(self._on_dir_size_ready)

        self.model.dataChanged.connect(lambda *_: self.update_status())
        self.path_changed.connect(lambda _p: self._update_drive_label())
        self._update_drive_label()
        self.model.modelReset.connect(self._on_model_reset)
        self.loaded.connect(self._on_loaded)
        thumbnails.store().ready.connect(self._on_thumb_ready)
        self.cd(os.path.expanduser("~"), quiet=True)

    def _on_thumb_ready(self, path: str) -> None:
        self.model.notify_thumbnail(path)
        self.view.viewport().update()

    # -- навигация ---------------------------------------------------------

    def current_path(self) -> str:
        return self.model.path

    @property
    def is_vfs(self) -> bool:
        return self._vfs is not None

    @property
    def vfs(self) -> ArchiveBrowser | None:
        return self._vfs

    def cd(self, path: str, quiet: bool = False,
           _from_history: bool = False) -> bool:
        if "::" in path:
            archive, _, inner = path.partition("::")
            return self._cd_vfs(archive, inner, quiet,
                                _from_history=_from_history)
        target = os.path.abspath(os.path.expanduser(path))
        if os.path.isfile(target) and archive_format(target):
            return self._cd_vfs(target, "", quiet, _from_history=_from_history)
        self._vfs = None
        self._vfs_dir = ""
        if not os.path.isdir(target):
            if not quiet:
                QMessageBox.warning(self, tr("Переход"),
                                    tr("Каталог не найден:\n{target}").format(target=target))
            return False
        self._rewatch(target)
        self._pending = target
        self._gen += 1
        self.path_changed.emit(target)
        if not _from_history:
            self._push_history(target)
        self._start_scan(target, self._gen)
        return True

    def _cd_vfs(self, archive: str, inner: str, quiet: bool,
                _from_history: bool = False) -> bool:
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
                QMessageBox.warning(self, tr("Архив"),
                                    tr("Не удалось открыть:\n{exc}").format(exc=exc))
            return False
        self._vfs_dir = norm_member(inner)
        self._rewatch(archive)
        self._pending = f"{archive}::{self._vfs_dir}"
        self._gen += 1
        self.path_changed.emit(self._pending)
        if not _from_history:
            self._push_history(self._pending)
        self._start_scan(self._pending, self._gen)
        return True

    # -- история навигации ----------------------------------------------------

    MAX_HISTORY = 100

    def _push_history(self, path: str) -> None:
        if self._history and self._history[-1] == path:
            self._hist_pos = len(self._history) - 1
            return
        if self._hist_pos < len(self._history) - 1:
            del self._history[self._hist_pos + 1:]  # ветвление истории
        self._history.append(path)
        if len(self._history) > self.MAX_HISTORY:
            del self._history[:len(self._history) - self.MAX_HISTORY]
        self._hist_pos = len(self._history) - 1

    def navigate_history(self, delta: int) -> bool:
        """Alt+←/→: назад/вперёд по истории этой панели."""
        pos = self._hist_pos + delta
        if not 0 <= pos < len(self._history):
            return False
        target = self._history[pos]
        self._hist_pos = pos
        self.cd(target, quiet=True, _from_history=True)
        return True

    def history(self) -> list[str]:
        return list(self._history)

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
            self.status_label.setText(
                tr("не удалось прочитать каталог: {error}").format(error=payload["error"]))
            return
        keep = self._reveal_name or self._cursor_name()
        self._reveal_name = None
        self._dirinfo_cache.clear()  # содержимое каталога могло измениться
        self.model.set_entries(payload["path"], payload["entries"])
        self._restore_cursor(keep)
        # курсор восстановлен — содержимое под ним могло измениться
        self.cursor_changed.emit(self.current_entry())
        self._update_combo()
        self._update_completer()
        self.update_status()
        self._schedule_dir_sizes()  # размеры каталогов, если включены

    def _watched_refresh(self) -> None:
        self.refresh()

    def _on_dir_changed(self, _path: str) -> None:
        self._refresh_timer.start()

    def _on_cursor_changed(self, current, _previous) -> None:
        row = current.row()
        self.cursor_changed.emit(None if row <= 0 else self.model.entry_at(row))
        self._schedule_dir_info(self.model.entry_at(row))

    # -- сведения о папке под курсором (фон) ---------------------------------

    def _schedule_dir_info(self, entry: FileEntry | None) -> None:
        self.update_status()
        if entry is None or not entry.is_dir or self.is_vfs:
            return
        from . import cloudmount

        if cloudmount.is_cloud_path(entry.path):
            return  # рекурсивный обход облака по сети — слишком дорого
        if entry.path in self._dirinfo_cache:
            return  # посчитано ранее — уже показано
        with self._dirinfo_lock:
            self._dirinfo_wanted = entry.path
            self._dirinfo_wake.set()

    def _dirinfo_worker(self) -> None:
        """Рабочий поток панели: считает статистику последней запрошенной
        папки; накопившиеся промежуточные запросы вытесняются последним."""
        while True:
            self._dirinfo_wake.wait()
            with self._dirinfo_lock:
                path = self._dirinfo_wanted
                self._dirinfo_wanted = None
                self._dirinfo_wake.clear()
            if path is None:
                continue
            stats = dir_stats(path)
            self.dir_info_ready.emit(path, stats)

    def _on_dir_info_ready(self, path: str, stats: DirStats) -> None:
        self._dirinfo_cache[path] = stats
        cur = self.current_entry()
        if cur is not None and cur.path == path:
            self.update_status()

    # -- размеры каталогов в колонке «Размер» (фон, по включению) ------------

    def set_dirsizes(self, on: bool) -> None:
        self.dirsizes_on = on
        if not on:
            self.model.dir_sizes.clear()
            if self.model.entries:
                self.model.dataChanged.emit(
                    self.model.index(1, 0),
                    self.model.index(len(self.model.entries), 0))
            return
        self._schedule_dir_sizes()

    def _schedule_dir_sizes(self) -> None:
        """Поставить в очередь подсчёт размеров всех каталогов текущего списка."""
        if not self.dirsizes_on or self.is_vfs:
            return
        from . import cloudmount

        gen = self._gen
        for e in self.model.entries:
            if not e.is_dir or e.name == "..":
                continue
            if cloudmount.is_cloud_path(e.path):
                continue  # рекурсивный обход облака по сети — слишком дорого
            with self._dirsize_lock:
                if e.path in self._dirsize_pending:
                    continue
                self._dirsize_pending.add(e.path)
            self._dirsize_queue.put((e.path, gen))

    def _dirsize_worker(self) -> None:
        """Рабочий поток: считает размер каталога за каталогом из очереди."""
        while True:
            path, gen = self._dirsize_queue.get()
            try:
                stats = dir_stats(path)
            except OSError:
                with self._dirsize_lock:
                    self._dirsize_pending.discard(path)
                continue
            self.dir_size_ready.emit(path, stats.size, gen)

    def _on_dir_size_ready(self, path: str, size: int, gen: int) -> None:
        with self._dirsize_lock:
            self._dirsize_pending.discard(path)
        if gen != self._gen:
            return  # список уже сменился
        self.model.set_dir_size(path, size)

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
        self.model.mark_mask("*", on)  # массово, без поштучных обновлений
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

    def set_thumbnails(self, on: bool) -> None:
        """Миниатюры картинок и PDF: фоновая генерация + кэш; иконка 64px."""
        self.model.set_thumbnails(on, thumbnails.store())
        self.view.setIconSize(QSize(64, 64) if on else QSize(20, 20))
        if self.model.entries:
            self.model.dataChanged.emit(
                self.model.index(1, 0),
                self.model.index(len(self.model.entries), 0))
        self.view.viewport().update()

    def set_active(self, active: bool) -> None:
        # тиловый акцент активной панели — фирменный цвет Iustitia
        self.path_combo.setStyleSheet(
            "QComboBox { font-weight: bold; color: #2f8f8b; }" if active else "")
        font = self.status_label.font()
        font.setBold(active)
        self.status_label.setFont(font)

    # -- состояние ------------------------------------------------------------

    def update_status(self) -> None:
        if self._loading:
            self.status_label.setText(tr("⏳ чтение каталога…"))
            return
        files, dirs, total, m_count, m_bytes = self.model.summary()
        text = tr("файлов: {files}   папок: {dirs}   {size}").format(
            files=files, dirs=dirs, size=human_size(total))
        if m_count:
            text += (tr("   •   отмечено: {count} ({size})")
                     .format(count=m_count, size=human_size(m_bytes)))
        cur = self.current_entry()
        if cur is not None and cur.is_dir and not self.is_vfs:
            st = self._dirinfo_cache.get(cur.path)
            if st is not None:
                text += tr("   •   {name}: {size} • {dirs} • {files}").format(
                    name=cur.name, size=human_size(st.size),
                    dirs=unit(st.dirs, ("подпапка", "подпапки", "подпапок"),
                              ("subfolder", "subfolders"), "个子文件夹"),
                    files=unit(st.files, ("файл", "файла", "файлов"),
                               ("file", "files"), "个文件"))
        self.status_label.setText(text)

    def _on_model_reset(self) -> None:
        self.view.apply_column_layout()

    def _update_drive_label(self) -> None:
        from PySide6.QtCore import QStorageInfo

        root = QStorageInfo(self.current_path()).rootPath()
        label = root if root == "/" else (os.path.basename(root) or root)
        self.drive_button.setText("💾 " + label)

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
