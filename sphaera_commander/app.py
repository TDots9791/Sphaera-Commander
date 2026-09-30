"""Главное окно Sphaera Commander: панели, клавиатура TC, очередь операций,
подтверждения перезаписи, архивы, групповое переименование, сравнение каталогов."""

from __future__ import annotations

import os
import shlex
import shutil
import sys
import tarfile
import tempfile
import threading
import zipfile

from PySide6.QtCore import QObject, QProcess, QStorageInfo, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QInputDialog,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
)

from . import __version__, config
from .archives import ArchiveBrowser, archive_format, pack_items, unpack_archive
from .dialogs import (
    BatchRenameDialog,
    OverwriteAskDialog,
    ProgressOpDialog,
    confirm_delete,
    show_op_result,
)
from .fsmodel import MTIME_COL, NAME_COL, SIZE_COL, compare_name_sets
from .ops import (
    ASK_CANCEL,
    KIND_COPY,
    KIND_DELETE,
    KIND_MOVE,
    POLICY_OVERWRITE,
    ConflictInfo,
    OpResult,
    execute,
    plan_copy_move,
    run_in_thread,
)
from .panel import FilePanel
from .viewer import looks_binary, open_viewer


class _ConfirmBridge(QObject):
    """Переносит запрос перезаписи из рабочего потока в GUI и ответ обратно.

    ask() блокирует рабочий поток до ответа GUI; отмена операции не блокируется
    (опрос события с таймаутом).
    """

    confirmRequested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._event = threading.Event()
        self._answer = ASK_CANCEL

    def ask(self, info: ConflictInfo) -> str:  # рабочий поток
        self._event.clear()
        self._answer = ASK_CANCEL
        self.confirmRequested.emit(info)
        while not self._event.wait(0.1):
            pass
        return self._answer

    def respond(self, answer: str) -> None:  # GUI-поток
        self._answer = answer
        self._event.set()


class CommandLine(QLineEdit):
    """Командная строка с историей (стрелки вверх/вниз)."""

    def __init__(self, history: list[str], parent=None):
        super().__init__(parent)
        self.history = history
        self._pos = len(history)
        self.setPlaceholderText("Командная строка: Enter — выполнить (cd — сменить каталог панели)")
        self.setToolTip("История — стрелки ↑/↓")

    def remember(self, cmd: str) -> None:
        if cmd in self.history:
            self.history.remove(cmd)
        self.history.append(cmd)
        self._pos = len(self.history)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Up:
            if self.history and self._pos > 0:
                self._pos -= 1
                self.setText(self.history[self._pos])
            event.accept()
            return
        if event.key() == Qt.Key_Down:
            if self._pos < len(self.history):
                self._pos += 1
                self.setText(self.history[self._pos] if self._pos < len(self.history) else "")
            event.accept()
            return
        super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sphaera Commander")
        self.show_hidden = False

        self.left = FilePanel()
        self.right = FilePanel()
        self.active: FilePanel = self.left

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.left)
        self.splitter.addWidget(self.right)
        self.splitter.setChildrenCollapsible(False)

        self.cmdline = CommandLine(config.load_cmd_history())
        self.cmdline.returnPressed.connect(self._run_command)
        self._proc: QProcess | None = None
        self._out: list[str] = []

        self._bridge = _ConfirmBridge(self)
        self._bridge.confirmRequested.connect(self._on_confirm)
        self._queue: list[dict] = []
        self._temp_dir: str | None = None

        fnbar = QWidget()
        fn_layout = QHBoxLayout(fnbar)
        fn_layout.setContentsMargins(2, 1, 2, 1)
        fn_layout.setSpacing(2)
        for key, title, slot in (
            ("F3", "Просмотр", self.open_viewer_cmd),
            ("F4", "Правка", self.open_editor_cmd),
            ("F5", "Копирование", self.do_copy),
            ("F6", "Перенос", self.do_move),
            ("F7", "Папка", self.do_mkdir),
            ("F8", "Удаление", self.do_delete),
            ("F10", "Выход", self.close),
        ):
            btn = QPushButton(f" {key} {title} ")
            btn.setFlat(True)
            btn.clicked.connect(slot)
            fn_layout.addWidget(btn)
        fn_layout.addStretch(1)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        layout.addWidget(self.splitter, 1)
        layout.addWidget(self.cmdline)
        layout.addWidget(fnbar)
        self.setCentralWidget(central)

        # состояние окна — после построения UI (нужны splitter и панели)
        self.show_hidden = config.load_window(self)

        self._thread = None
        self._worker = None
        self._dialog = None
        self._after_op = None

        self._make_actions()
        self._make_menu()
        self._restore_panels()
        self._set_active(self.left)
        self.left.view.installEventFilter(self)
        self.right.view.installEventFilter(self)
        self.left.view.context_requested.connect(
            lambda pos, p=self.left: self._context_menu(p, pos))
        self.right.view.context_requested.connect(
            lambda pos, p=self.right: self._context_menu(p, pos))
        self.left.entry_activated.connect(self._open_entry)
        self.right.entry_activated.connect(self._open_entry)
        self.left.path_changed.connect(lambda _p: self._update_title())
        self.right.path_changed.connect(lambda _p: self._update_title())
        self._update_title()

    # ------------------------------------------------------------- построение

    def _make_actions(self):
        def act(title, keys, slot, checkable=False):
            a = QAction(title, self)
            if keys:
                a.setShortcut(QKeySequence(keys))
            a.triggered.connect(slot)
            if checkable:
                a.setCheckable(True)
            self.addAction(a)
            return a

        act("Просмотр", "F3", self.open_viewer_cmd)
        act("Правка", "F4", self.open_editor_cmd)
        self.act_copy = act("Копирование", "F5", self.do_copy)
        self.act_move = act("Перенос", "F6", self.do_move)
        act("Новая папка", "F7", self.do_mkdir)
        self.act_delete = act("Удаление", "F8", self.do_delete)
        act("Переименовать", "Shift+F6", self.do_rename)
        act("Групповое переименование…", "Ctrl+M", self.do_batch_rename)
        act("Запаковать…", "Alt+F5", self.do_pack)
        act("Распаковать…", "Alt+F6", self.do_unpack)
        act("Сравнить каталоги", "Shift+F2", self.compare_dirs)
        act("Открыть системным приложением", None, self.open_system)
        act("Обновить", "Ctrl+R", self.refresh_all)
        self.act_hidden = act("Скрытые файлы", "Ctrl+H",
                              lambda: self.toggle_hidden(), checkable=True)
        self.act_hidden.setChecked(self.show_hidden)
        act("Поменять панели местами", "Ctrl+U", self.swap_panels)
        act("Выход", "Ctrl+Q", self.close)
        act("Сортировка: имя", "Ctrl+F3", lambda: self._sort_active(NAME_COL))
        act("Сортировка: дата", "Ctrl+F5", lambda: self._sort_active(MTIME_COL))
        act("Сортировка: размер", "Ctrl+F6", lambda: self._sort_active(SIZE_COL))
        self.act_alt_f1 = act("Левая панель: смена диска", "Alt+F1",
                              lambda: self._places_menu(self.left))
        self.act_alt_f2 = act("Правая панель: смена диска", "Alt+F2",
                              lambda: self._places_menu(self.right))

    def _make_menu(self):
        m_file = self.menuBar().addMenu("&Файл")
        for title in ("Просмотр", "Правка", "Открыть системным приложением"):
            m_file.addAction(self._find_action(title))
        m_file.addSeparator()
        for title in ("Копирование", "Перенос", "Новая папка", "Удаление",
                      "Переименовать", "Групповое переименование…"):
            m_file.addAction(self._find_action(title))
        m_file.addSeparator()
        for title in ("Запаковать…", "Распаковать…"):
            m_file.addAction(self._find_action(title))
        m_file.addSeparator()
        m_file.addAction(self._find_action("Выход"))

        m_view = self.menuBar().addMenu("&Вид")
        m_view.addAction(self.act_hidden)
        m_view.addAction(self._find_action("Обновить"))
        m_sort = m_view.addMenu("Сортировка")
        for t in ("имя", "дата", "размер"):
            m_sort.addAction(self._find_action(f"Сортировка: {t}"))

        m_panels = self.menuBar().addMenu("&Панели")
        m_panels.addAction(self.act_alt_f1)
        m_panels.addAction(self.act_alt_f2)
        m_panels.addAction(self._find_action("Поменять панели местами"))
        m_panels.addSeparator()
        m_panels.addAction(self._find_action("Сравнить каталоги"))

        m_help = self.menuBar().addMenu("&Справка")
        about = QAction("О программе", self)
        about.triggered.connect(self._about)
        m_help.addAction(about)

    def _find_action(self, title: str) -> QAction:
        for a in self.findChildren(QAction):
            if a.text() == title:
                return a
        raise KeyError(title)

    def _restore_panels(self):
        config.load_panel(self.left, "left")
        config.load_panel(self.right, "right")
        if self.show_hidden:
            self.left.set_show_hidden(True)
            self.right.set_show_hidden(True)
        self.left.wait_loaded()
        self.right.wait_loaded()

    # ------------------------------------------------------------- служебные

    def eventFilter(self, obj, event):
        if event.type() == event.Type.FocusIn and obj in (self.left.view, self.right.view):
            self._set_active(self.left if obj is self.left.view else self.right)
        return super().eventFilter(obj, event)

    def _set_active(self, panel: FilePanel) -> None:
        self.active = panel
        self.left.set_active(panel is self.left)
        self.right.set_active(panel is self.right)
        self._update_title()

    def _other(self) -> FilePanel:
        return self.right if self.active is self.left else self.left

    def _update_title(self):
        self.setWindowTitle(
            f"Sphaera Commander — {self.left.current_path()} | {self.right.current_path()}")

    def _status(self, msg: str) -> None:
        self.statusBar().showMessage(msg, 5000)

    def toggle_hidden(self):
        self.show_hidden = not self.show_hidden
        self.left.set_show_hidden(self.show_hidden)
        self.right.set_show_hidden(self.show_hidden)
        self.act_hidden.setChecked(self.show_hidden)

    def refresh_all(self):
        self.left.refresh()
        self.right.refresh()

    def swap_panels(self):
        lpath, rpath = self.left.current_path(), self.right.current_path()
        self.left.cd(rpath, quiet=True)
        self.right.cd(lpath, quiet=True)
        self._update_title()

    def _sort_active(self, col: int):
        self.active.model.apply_sort(col)
        self.active.view.scrollToTop()

    def _places_menu(self, panel: FilePanel):
        menu = QMenu("Смена диска", self)
        menu.addAction("Домой", lambda: panel.cd(os.path.expanduser("~")))
        menu.addAction("Корень (/)", lambda: panel.cd("/"))
        menu.addSeparator()
        seen = set()
        for vol in QStorageInfo.mountedVolumes():
            root = vol.rootPath()
            if not vol.isReady() or root in seen:
                continue
            if root.startswith(("/run", "/sys", "/proc", "/dev", "/boot/efi", "/var/lib")):
                continue
            seen.add(root)
            label = vol.displayName() or root
            menu.addAction(f"{label} ({root})", lambda r=root: panel.cd(r))
        menu.exec(self.cursor().pos())

    def _about(self):
        QMessageBox.about(
            self, "О программе",
            f"<b>Sphaera Commander</b> {__version__}<br><br>"
            "Двухпанельный файловый менеджер для Linux в духе Total Commander.<br>"
            "Tab — смена панели, F5/F6 — копирование/перенос, F3/F4 — просмотр/правка, "
            "Alt+F5/Alt+F6 — запаковать/распаковать, Ctrl+M — групповое переименование, "
            "Shift+F2 — сравнить каталоги.")

    def _context_menu(self, panel: FilePanel, pos) -> None:
        self._set_active(panel)
        entry = panel.current_entry()
        menu = QMenu(self)
        if entry is not None and not entry.is_dir:
            menu.addAction(self._find_action("Просмотр"))
            menu.addAction(self._find_action("Правка"))
            menu.addSeparator()
        if entry is not None and archive_format(entry.path):
            menu.addAction(self._find_action("Распаковать…"))
        for title in ("Копирование", "Перенос", "Удаление", "Переименовать"):
            menu.addAction(self._find_action(title))
        menu.addSeparator()
        menu.addAction(self._find_action("Открыть системным приложением"))
        menu.exec(panel.view.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------- просмотр/правка

    def _ensure_temp(self) -> str:
        if self._temp_dir is None:
            self._temp_dir = tempfile.mkdtemp(prefix="sphaera-")
        return self._temp_dir

    def _open_entry(self, entry) -> None:
        """Enter на файле: открыть системным приложением (для члена архива — через temp)."""
        if entry is None or entry.is_dir:
            return
        if "::" in entry.path:
            archive, _, member = entry.path.partition("::")
            try:
                browser = ArchiveBrowser(archive)
                tmp = browser.extract_member_to_temp(member, self._ensure_temp())
            except (ValueError, OSError, KeyError,
                    zipfile.BadZipFile, tarfile.TarError) as exc:
                QMessageBox.warning(self, "Архив", f"Не удалось извлечь:\n{exc}")
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(tmp))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(entry.path))

    def _viewer_targets(self) -> tuple[str | None, list[str], ArchiveBrowser | None, str]:
        """(файл, список для навигации, браузер архива, член архива)."""
        entry = self.active.current_entry()
        if entry is None or entry.is_dir:
            return None, [], None, ""
        if "::" in entry.path:
            archive, _, member = entry.path.partition("::")
            try:
                browser = ArchiveBrowser(archive)
                tmp = browser.extract_member_to_temp(member, self._ensure_temp())
            except (ValueError, OSError, KeyError,
                    zipfile.BadZipFile, tarfile.TarError) as exc:
                QMessageBox.warning(self, "Архив", f"Не удалось извлечь:\n{exc}")
                return None, [], None, ""
            return tmp, [tmp], browser, member
        files = [e.path for e in self.active.model.entries if not e.is_dir]
        return entry.path, files, None, ""

    def open_viewer_cmd(self):
        path, files, _browser, _member = self._viewer_targets()
        if path is None:
            self._status("Нет файла под курсором")
            return
        open_viewer(self, path, files, editable=False).exec()

    def open_editor_cmd(self):
        path, files, browser, member = self._viewer_targets()
        if path is None:
            self._status("Нет файла под курсором")
            return
        if browser is None:
            try:
                with open(path, "rb") as f:
                    head = f.read(8192)
            except OSError as exc:
                QMessageBox.warning(self, "Правка", f"Не удалось открыть файл:\n{exc}")
                return
            if looks_binary(head):
                QMessageBox.information(
                    self, "Правка",
                    "Похоже, файл бинарный — встроенный редактор его не открывает.")
                return
        dlg = open_viewer(self, path, files, editable=True)
        dlg.exec()
        if browser is not None and dlg.saved_on_close:
            self._enqueue_op(
                f"Запись в архив {os.path.basename(browser.archive_path)}",
                lambda progress_cb, is_cancelled:
                browser.replace_member(member, path),
                after=self.refresh_all)

    def open_system(self):
        entry = self.active.current_entry()
        if entry is None:
            entry_path = self.active.current_path()
        else:
            entry_path = entry.path
        QDesktopServices.openUrl(QUrl.fromLocalFile(entry_path))

    # ------------------------------------------------------------- операции

    def _fs_fn(self, kind: str, plan, sources: list):
        bridge = self._bridge

        def fn(progress_cb, is_cancelled) -> OpResult:
            return execute(kind, plan, sources, POLICY_OVERWRITE,
                           progress_cb, is_cancelled, ask_cb=bridge.ask)

        return fn

    def _enqueue_op(self, title: str, fn, after=None) -> None:
        item = {"title": title, "fn": fn, "after": after}
        if self._thread is not None:
            self._queue.append(item)
            if self._dialog is not None:
                self._dialog.set_queue(len(self._queue))
            return
        self._begin_op(item)

    def _begin_op(self, item: dict) -> None:
        self._after_op = item.get("after")
        self._thread, self._worker = run_in_thread(item["fn"])
        self._dialog = ProgressOpDialog(self, item["title"],
                                        cancel_callable=self._worker.cancel,
                                        abort_callable=self._abort_all)
        self._worker.progressChanged.connect(self._on_op_progress)
        self._worker.finished.connect(self._on_op_finished)
        if self._queue:
            self._dialog.set_queue(len(self._queue))
        self._thread.start()
        self._dialog.open()

    def _on_op_progress(self, p) -> None:
        if self._dialog is not None:
            self._dialog.update_progress(p)

    def _on_op_finished(self, result: OpResult) -> None:
        self._dialog.op_finished(result)
        QTimer.singleShot(350, self._dialog.accept)
        self._thread.quit()
        self._thread.wait()
        self._thread.deleteLater()
        self._worker.deleteLater()
        self._thread = self._worker = self._dialog = None
        show_op_result(self, result)
        if self._after_op is not None:
            after, self._after_op = self._after_op, None
            after()
        self._update_title()
        if self._queue:
            self._begin_op(self._queue.pop(0))

    def _cancel_current(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _abort_all(self) -> None:
        self._queue.clear()
        self._cancel_current()

    def _on_confirm(self, info: ConflictInfo) -> None:
        self._bridge.respond(OverwriteAskDialog.ask(self, info))

    def do_copy(self):
        self._start_copy_move(move=False)

    def do_move(self):
        self._start_copy_move(move=True)

    def _start_copy_move(self, move: bool):
        if self.active.is_vfs:
            self._extract_selected(move=move)
            return
        if self._other().is_vfs:
            self._status("Архив в панели назначения открыт только для чтения")
            return
        sources = self.active.selected_entries()
        if not sources:
            self._status("Нет отмеченных объектов (Insert — отметить)")
            return
        dest = self._other().current_path()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            plan = plan_copy_move(sources, dest, move)
        except OSError as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Ошибка", f"Не удалось построить план:\n{exc}")
            return
        QApplication.restoreOverrideCursor()
        if not plan.jobs:
            self._status("Нет объектов для обработки")
            return
        kind = KIND_MOVE if move else KIND_COPY
        title = "Перенос" if move else "Копирование"
        self._enqueue_op(title, self._fs_fn(kind, plan, sources),
                         after=lambda m=move: self._after_copy_move(m))

    def _after_copy_move(self, move: bool):
        self.refresh_all()
        if move:
            self.active.model.clear_marks()

    def _extract_selected(self, move: bool) -> None:
        """Извлечение выбранных членов архива в противоположную панель (F5/F6)."""
        panel = self.active
        browser = panel.vfs
        sources = panel.selected_entries()
        if not sources or browser is None:
            self._status("Нет отмеченных объектов (Insert — отметить)")
            return
        if self._other().is_vfs:
            self._status("Извлечение в другой архив не поддерживается")
            return
        members = [e.path.partition("::")[2] for e in sources]
        dest = self._other().current_path()
        title = "Перенос из архива" if move else "Извлечение из архива"
        extract_fn = (lambda progress_cb, is_cancelled:
                      browser.extract_members(members, dest, progress_cb,
                                              is_cancelled,
                                              ask_cb=self._bridge.ask))
        if move:
            self._enqueue_op(title, extract_fn)
            self._enqueue_op(
                f"Удаление из {os.path.basename(browser.archive_path)}",
                lambda progress_cb, is_cancelled: browser.delete_members(members),
                after=self.refresh_all)
        else:
            self._enqueue_op(title, extract_fn, after=self.refresh_all)

    def do_delete(self):
        if self.active.is_vfs:
            self._delete_in_archive()
            return
        sources = self.active.selected_entries()
        if not sources:
            self._status("Нет отмеченных объектов (Insert — отметить)")
            return
        if not confirm_delete(self, sources, self.active.current_path()):
            return
        self._enqueue_op("Удаление", self._fs_fn(KIND_DELETE, None, sources),
                         after=self.refresh_all)

    def _delete_in_archive(self) -> None:
        panel = self.active
        browser = panel.vfs
        sources = panel.selected_entries()
        if not sources or browser is None:
            self._status("Нет отмеченных объектов (Insert — отметить)")
            return
        if not confirm_delete(self, sources, panel.current_path()):
            return
        members = [e.path.partition("::")[2] for e in sources]
        self._enqueue_op(
            f"Удаление из {os.path.basename(browser.archive_path)}",
            lambda progress_cb, is_cancelled: browser.delete_members(members),
            after=self.refresh_all)

    def do_pack(self):
        if self.active.is_vfs:
            self._status("Сначала извлеките объекты из архива")
            return
        sources = self.active.selected_entries()
        if not sources:
            self._status("Нет отмеченных объектов (Insert — отметить)")
            return
        dest_panel = self._other()
        default_name = os.path.splitext(sources[0].name)[0] + ".zip"
        name, ok = QInputDialog.getText(self, "Запаковать",
                                        f"Имя архива (в {dest_panel.current_path()}):",
                                        text=default_name)
        if not ok or not name.strip():
            return
        fmt, ok = QInputDialog.getItem(self, "Запаковать", "Формат:",
                                       ("zip", "tar.gz", "tar.bz2", "tar.xz"), 0, False)
        if not ok:
            return
        out = os.path.join(dest_panel.current_path(), name.strip())
        if os.path.lexists(out):
            ret = QMessageBox.question(self, "Файл существует",
                                       f"{out}\nПерезаписать?",
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                return
        self._enqueue_op("Запаковка",
                         lambda progress_cb, is_cancelled:
                         pack_items(sources, out, fmt, progress_cb, is_cancelled),
                         after=lambda: dest_panel.reveal(os.path.basename(out)))

    def do_unpack(self):
        entries = [e for e in self.active.selected_entries() if archive_format(e.path)]
        if not entries:
            self._status("Выберите архив (.zip, .tar, .tgz, .tar.bz2, .tar.xz)")
            return
        dest = self._other().current_path()
        bridge = self._bridge
        for entry in entries:
            def fn(progress_cb, is_cancelled, _archive=entry.path):
                return unpack_archive(_archive, dest, progress_cb, is_cancelled,
                                      ask_cb=bridge.ask)

            self._enqueue_op(f"Распаковка {entry.name}", fn, after=self.refresh_all)

    def do_batch_rename(self):
        panel = self.active
        if panel.is_vfs:
            self._status("Групповое переименование в архиве не поддерживается")
            return
        names = [e.name for e in panel.selected_entries()]
        if not names:
            self._status("Нет объектов для переименования")
            return
        dlg = BatchRenameDialog(self, names)
        if dlg.exec() != QDialog.Accepted or not dlg.pairs:
            return
        errors: list[str] = []
        done = 0
        first_new = None
        for old, new in dlg.pairs:
            if old == new:
                continue
            src = os.path.join(panel.current_path(), old)
            dst = os.path.join(panel.current_path(), new)
            if os.path.lexists(dst):
                errors.append(f"{old} — назначение уже существует: {new}")
                continue
            try:
                os.rename(src, dst)
                done += 1
                if first_new is None:
                    first_new = new
            except OSError as exc:
                errors.append(f"{old} — {exc.strerror or exc}")
        panel.reveal(first_new or names[0])
        if errors:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Переименование")
            box.setText(f"Переименовано: {done}, ошибок: {len(errors)}.")
            box.setDetailedText("\n".join(errors[:200]))
            box.exec()
        else:
            self._status(f"Переименовано: {done}")

    def compare_dirs(self):
        if self.left.is_vfs or self.right.is_vfs:
            self._status("Сравнение каталогов работает для обычных каталогов")
            return
        left = {e.name: e for e in self.left.model.entries if not e.is_dir}
        right = {e.name: e for e in self.right.model.entries if not e.is_dir}
        diff_left, diff_right = compare_name_sets(left, right)
        self.left.model.set_compared(diff_left)
        self.right.model.set_compared(diff_right)
        self._status(f"Сравнение каталогов: {len(diff_left)} отличий слева, "
                     f"{len(diff_right)} справа (выделено синим)")

    def do_mkdir(self):
        panel = self.active
        if panel.is_vfs:
            self._status("Создание папок в архиве не поддерживается")
            return
        name, ok = QInputDialog.getText(self, "Новая папка", "Имя папки:", text="")
        if not ok or not name.strip():
            return
        try:
            os.makedirs(os.path.join(panel.current_path(), name.strip()))
        except OSError as exc:
            QMessageBox.critical(self, "Ошибка",
                                 f"Не удалось создать папку:\n{exc.strerror or exc}")
            return
        panel.reveal(name.strip())

    def do_rename(self):
        panel = self.active
        if panel.is_vfs:
            self._status("Переименование в архиве не поддерживается")
            return
        entry = panel.current_entry()
        if entry is None:
            self._status("Нет объекта под курсором")
            return
        name, ok = QInputDialog.getText(self, "Переименовать", "Новое имя:",
                                        text=entry.name)
        if not ok or not name.strip() or name == entry.name:
            return
        try:
            os.rename(entry.path, os.path.join(panel.current_path(), name.strip()))
        except OSError as exc:
            QMessageBox.critical(self, "Ошибка",
                                 f"Не удалось переименовать:\n{exc.strerror or exc}")
            return
        panel.reveal(name.strip())

    # ------------------------------------------------------------- командная строка

    def _run_command(self):
        cmd = self.cmdline.text().strip()
        if not cmd:
            return
        if cmd.startswith("cd"):
            try:
                parts = shlex.split(cmd)
            except ValueError as exc:
                self._status(f"cd: {exc}")
                return
            target = parts[1] if len(parts) > 1 else "~"
            if self.active.is_vfs:
                base = os.path.dirname(self.active.vfs.archive_path)
            else:
                base = self.active.current_path()
            resolved = os.path.abspath(os.path.expanduser(
                target if os.path.isabs(target) else os.path.join(base, target)))
            if self.active.cd(resolved, quiet=True):
                self.cmdline.remember(cmd)
                self.cmdline.clear()
            else:
                self._status(f"cd: каталог не найден: {resolved}")
            return
        if self._proc is not None and self._proc.state() != QProcess.NotRunning:
            self._status("Предыдущая команда ещё выполняется")
            return
        self._out = []
        self._proc = QProcess(self)
        self._proc.setWorkingDirectory(self.active.current_path())
        self._proc.readyReadStandardOutput.connect(self._collect_out)
        self._proc.readyReadStandardError.connect(self._collect_out)
        self._proc.finished.connect(self._cmd_finished)
        self.statusBar().showMessage(f"$ {cmd}")
        self._proc.start("/bin/sh", ["-c", cmd])

    def _collect_out(self):
        if self._proc is None:
            return
        self._out.append(bytes(self._proc.readAllStandardOutput()).decode(errors="replace"))
        self._out.append(bytes(self._proc.readAllStandardError()).decode(errors="replace"))

    def _cmd_finished(self, code, _status):
        output = "".join(self._out).strip()
        cmd = self.cmdline.text()
        self.cmdline.remember(cmd)
        self.cmdline.clear()
        if output or code != 0:
            text = output or "(без вывода)"
            if len(text) > 8000:
                text = text[:8000] + "\n…"
            box = QMessageBox(self)
            box.setWindowTitle("Командная строка")
            box.setIcon(QMessageBox.Information if code == 0 else QMessageBox.Warning)
            box.setText(f"$ {cmd}\nкод выхода: {code}")
            box.setDetailedText(text)
            box.exec()
        else:
            self._status(f"$ {cmd} — готово")
        self.refresh_all()

    # ------------------------------------------------------------- жизненный цикл

    def closeEvent(self, event):
        if self._thread is not None:
            ret = QMessageBox.question(
                self, "Операция выполняется",
                "Идёт файловая операция. Прервать её и выйти?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                event.ignore()
                return
            self._worker.cancel()
            self._bridge.respond(ASK_CANCEL)  # разблокировать ожидание диалога
            self._thread.quit()
            self._thread.wait(5000)
        config.save_window(self)
        config.save_panel(self.left, "left")
        config.save_panel(self.right, "right")
        config.save_cmd_history(self.cmdline.history)
        if self._temp_dir is not None:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        event.accept()


def _app_icon():
    from pathlib import Path

    from PySide6.QtGui import QIcon

    path = Path(__file__).parent / "assets" / "icon.svg"
    return QIcon(str(path)) if path.exists() else QIcon()


def main(argv=None):
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Sphaera Commander")
    app.setOrganizationName("Sphaera")
    app.setWindowIcon(_app_icon())
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
