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
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence
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
    QStackedWidget,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
)

from . import __version__, config, i18n
from .cloudsync import rclone_remotes as cloudsync_remotes
from .i18n import tr
from . import cloudmount, hotlist, mounts
from . import pluginmgr as plugins_mod
from .archives import ArchiveBrowser, archive_format, pack_items, unpack_archive
from .dialogs import (
    BatchRenameDialog,
    OverwriteAskDialog,
    ProgressOpDialog,
    confirm_delete,
    confirm_overwrite,
    show_op_result,
)
from .fsmodel import (
    EXT_COL,
    MTIME_COL,
    NAME_COL,
    SIZE_COL,
    compare_name_sets,
    entry_for,
)
from .ops import (
    ASK_CANCEL,
    KIND_COPY,
    KIND_DELETE,
    KIND_MOVE,
    POLICY_OVERWRITE,
    ConflictInfo,
    OpResult,
    execute,
    execute_trash,
    plan_copy_move,
    run_in_thread,
)
from .panel import FilePanel
from . import previewers as pv
from .dialogs import SearchDialog
from .quick_preview import QuickPreview
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
        self.setPlaceholderText(tr("Командная строка: Enter — выполнить (cd — сменить каталог панели)"))
        self.setToolTip(tr("История — стрелки ↑/↓"))

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


def select_base_name(line_edit: QLineEdit, name: str, is_dir: bool) -> None:
    """Выделить имя без расширения (как в TC): случайный ввод не сотрёт «.расш».
    У каталогов и файлов без расширения выделяется всё имя."""
    if is_dir:
        base = name
    else:
        base, ext = os.path.splitext(name)
        if not ext:
            base = name
    line_edit.setSelection(0, len(base))


def make_rename_dialog(parent, entry) -> QInputDialog:
    dlg = QInputDialog(parent)
    dlg.setWindowTitle(tr("Переименовать"))
    dlg.setLabelText(tr("Новое имя:"))
    dlg.setTextValue(entry.name)
    edit = dlg.findChild(QLineEdit)
    if edit is not None:
        select_base_name(edit, entry.name, entry.is_dir)
    return dlg


# Шелл командной строки окна: win64/run.py инжектирует платформенный
# shell_argv (ТЗ §4); по умолчанию — /bin/sh -c.
shell_argv_provider = None


class MainWindow(QMainWindow):
    gui_call = Signal(object)  # callable из фонового потока — выполнить в GUI
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sphaera Commander")
        self.show_hidden = False

        self.left = FilePanel()
        self.right = FilePanel()
        self.active: FilePanel = self.left
        self.quick_view = config.qsettings().value(
            "view/quick_view", "false") in (True, "true", "1")

        # быстрый просмотр: на неактивной стороне показывается содержимое
        # (по экземпляру на сторону — виджет не может жить в двух стеках)
        self.quick_left = QuickPreview()
        self.quick_right = QuickPreview()
        self.left_stack = QStackedWidget()
        self.left_stack.addWidget(self.left)
        self.left_stack.addWidget(self.quick_left)
        self.right_stack = QStackedWidget()
        self.right_stack.addWidget(self.right)
        self.right_stack.addWidget(self.quick_right)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.left_stack)
        self.splitter.addWidget(self.right_stack)
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
            ("F3", tr("Просмотр"), self.open_viewer_cmd),
            ("F4", tr("Правка"), self.open_editor_cmd),
            ("F5", tr("Копирование"), self.do_copy),
            ("F6", tr("Перенос"), self.do_move),
            ("F7", tr("Папка"), self.do_mkdir),
            ("F8", tr("В корзину"), self.do_delete),
            ("F10", tr("Выход"), self.close),
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
        self._load_plugins()
        self._make_corner_close()
        self._restore_panels()
        self._set_active(self.left)
        # фокус — панели, а не строке адреса: клавиши работают сразу
        self.left.view.setFocus()
        self.left.view.installEventFilter(self)
        self.right.view.installEventFilter(self)
        self.left.view.switch_requested.connect(self._switch_panels)
        self.right.view.switch_requested.connect(self._switch_panels)
        for v in (self.left.view, self.right.view):
            v.delete_requested.connect(self._delete_by_key)
        self.gui_call.connect(lambda fn: fn())
        for pnl in (self.left, self.right):
            pnl.drive_menu_requested.connect(
                lambda p=pnl: self._places_menu(p))
        self.left.view.context_requested.connect(
            lambda pos, p=self.left: self._context_menu(p, pos))
        self.right.view.context_requested.connect(
            lambda pos, p=self.right: self._context_menu(p, pos))
        self.left.view.drop_requested.connect(
            lambda paths, target, move, p=self.left: self._on_drop(paths, target, p, move))
        self.right.view.drop_requested.connect(
            lambda paths, target, move, p=self.right: self._on_drop(paths, target, p, move))
        self.left.entry_activated.connect(self._open_entry)
        self.right.entry_activated.connect(self._open_entry)
        self.left.cursor_changed.connect(
            lambda e, p=self.left: self._on_panel_cursor(p, e))
        self.right.cursor_changed.connect(
            lambda e, p=self.right: self._on_panel_cursor(p, e))
        self._update_quick_view_visibility()
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

        act(tr("Просмотр"), "F3", self.open_viewer_cmd)
        act(tr("Правка"), "F4", self.open_editor_cmd)
        act(tr("Создать файл"), "Shift+F4", self.do_create_file)
        self.act_copy = act(tr("Копирование"), "F5", self.do_copy)
        self.act_move = act(tr("Перенос"), "F6", self.do_move)
        act(tr("Новая папка"), "F7", self.do_mkdir)
        self.act_delete = act(tr("Удаление (в корзину)"), "F8", self.do_delete)
        act(tr("Удалить безвозвратно"), "Shift+F8", self.do_delete_permanent)
        act(tr("Переименовать"), "Shift+F6", self.do_rename)
        act(tr("Групповое переименование…"), "Ctrl+M", self.do_batch_rename)
        act(tr("Создать символьную ссылку…"), None, self.do_symlink)
        act(tr("Создать жёсткую ссылку…"), None, self.do_hardlink)
        act(tr("Поиск файлов…"), "Alt+F7", self.do_search)
        act(tr("Запаковать…"), "Alt+F5", self.do_pack)
        act(tr("Распаковать…"), "Alt+F6", self.do_unpack)
        act(tr("Сравнить каталоги"), "Shift+F2", self.compare_dirs)
        act(tr("Синхронизация с облаком…"), None, self.open_cloud_sync)
        act(tr("Открыть системным приложением"), "Ctrl+E", self.open_system)
        act(tr("Свойства"), "Alt+Enter", self.do_properties)
        act(tr("Назад (история панели)"), "Alt+Left",
            lambda: self.active.navigate_history(-1))
        act(tr("Вперёд (история панели)"), "Alt+Right",
            lambda: self.active.navigate_history(1))
        act(tr("История панели"), "Alt+Down", self._panel_history_menu)
        act(tr("Обновить"), "Ctrl+R", self.refresh_all)
        self.act_thumbs = act(tr("Миниатюры (картинки и PDF)"), None,
                              lambda: self.toggle_thumbnails(), checkable=True)
        self.act_thumbs.setChecked(config.qsettings().value(
            "view/thumbnails", "false") in (True, "true", "1"))
        self.act_colorize = act(tr("Раскраска по типу"), None,
                                lambda: self.toggle_colorize(), checkable=True)
        self.act_colorize.setChecked(config.qsettings().value(
            "view/colorize", "true") in (True, "true", "1"))
        self.act_theme_dark = act(tr("Тёмная тема (Iustitia)"), None,
                                  lambda: self._set_theme("dark"),
                                  checkable=True)
        self.act_theme_light = act(tr("Светлая тема (пергамент)"), None,
                                   lambda: self._set_theme("light"),
                                   checkable=True)
        self.act_theme_system = act(tr("Системная тема"), None,
                                    lambda: self._set_theme("system"),
                                    checkable=True)
        self.act_hidden = act(tr("Скрытые файлы"), "Ctrl+H",
                              lambda: self.toggle_hidden(), checkable=True)
        self.act_hidden.setChecked(self.show_hidden)
        act(tr("Избранные папки (hotlist)"), "Ctrl+D", self._hotlist_menu)
        act(tr("Поменять панели местами"), "Ctrl+U", self.swap_panels)
        self.act_quick = act(tr("Быстрый просмотр (вторая панель)"), "Ctrl+Q",
                             self.toggle_quick_view, checkable=True)
        self.act_quick.setChecked(self.quick_view)
        self.act_fullscreen = act(tr("Полноэкранный режим"), "F11",
                                  self.toggle_fullscreen, checkable=True)
        act(tr("Выйти из полноэкранного режима"), "Escape", self._exit_fullscreen)
        act(tr("Выход"), "F10", self.close)
        act(tr("Сортировка: имя"), "Ctrl+F3", lambda: self._sort_active(NAME_COL))
        act(tr("Сортировка: расширение"), "Ctrl+F4",
            lambda: self._sort_active(EXT_COL))
        act(tr("Сортировка: дата"), "Ctrl+F5", lambda: self._sort_active(MTIME_COL))
        act(tr("Сортировка: размер"), "Ctrl+F6", lambda: self._sort_active(SIZE_COL))
        self.act_dirsizes = act(tr("Размеры каталогов"), None,
                                lambda: self.toggle_dirsizes(), checkable=True)
        self.act_dirsizes.setChecked(config.qsettings().value(
            "view/dirsizes", "false") in (True, "true", "1"))
        self.act_alt_f1 = act(tr("Левая панель: смена диска"), "Alt+F1",
                              lambda: self._places_menu(self.left))
        self.act_alt_f2 = act(tr("Правая панель: смена диска"), "Alt+F2",
                              lambda: self._places_menu(self.right))
        act(tr("Левая панель: диски (NC)"), "Ctrl+F1",
            lambda: self._places_menu(self.left, anchor=self.left.drive_button))
        act(tr("Правая панель: диски (NC)"), "Ctrl+F2",
            lambda: self._places_menu(self.right, anchor=self.right.drive_button))

    def _make_menu(self):
        m_file = self.menuBar().addMenu(tr("&Файл"))
        for title in (tr("Просмотр"), tr("Правка"), tr("Открыть системным приложением")):
            m_file.addAction(self._find_action(title))
        m_file.addSeparator()
        for title in (tr("Копирование"), tr("Перенос"), tr("Новая папка"), tr("Удаление (в корзину)"),
                      tr("Удалить безвозвратно"), tr("Переименовать"),
                      tr("Групповое переименование…"), tr("Поиск файлов…")):
            m_file.addAction(self._find_action(title))
        m_file.addSeparator()
        for title in (tr("Запаковать…"), tr("Распаковать…"),
                      tr("Создать символьную ссылку…"),
                      tr("Создать жёсткую ссылку…")):
            m_file.addAction(self._find_action(title))
        m_file.addSeparator()
        m_file.addAction(self._find_action(tr("Свойства")))
        m_file.addSeparator()
        m_file.addAction(self._find_action(tr("Выход")))

        m_view = self.menuBar().addMenu(tr("&Вид"))
        m_view.addAction(self.act_hidden)
        m_view.addAction(self._find_action(tr("Избранные папки (hotlist)")))
        m_view.addAction(self.act_quick)
        m_view.addAction(self.act_thumbs)
        m_view.addAction(self.act_colorize)
        m_view.addAction(self.act_dirsizes)
        m_theme = m_view.addMenu(tr("Тема"))
        theme_group = QActionGroup(m_theme)
        for a in (self.act_theme_dark, self.act_theme_light,
                  self.act_theme_system):
            theme_group.addAction(a)
            a.setChecked(False)
            m_theme.addAction(a)
        from . import theme as theme_mod

        mode = theme_mod.current_mode()
        {"dark": self.act_theme_dark, "light": self.act_theme_light,
         "system": self.act_theme_system}[mode].setChecked(True)
        m_view.addAction(self._find_action(tr("Обновить")))
        m_view.addAction(self.act_fullscreen)
        m_sort = m_view.addMenu(tr("Сортировка"))
        for key in (tr("Сортировка: имя"), tr("Сортировка: расширение"),
                    tr("Сортировка: дата"), tr("Сортировка: размер")):
            m_sort.addAction(self._find_action(tr(key)))
        m_lang = m_view.addMenu(tr("Язык / Language / 语言"))
        lang_group = QActionGroup(m_lang)
        for code, label in i18n.LANGUAGES:
            a = QAction(label, m_lang)
            a.setCheckable(True)
            a.setChecked(i18n.LANG == code)
            lang_group.addAction(a)
            a.triggered.connect(lambda _=False, c=code: self._set_language(c))
            m_lang.addAction(a)

        m_panels = self.menuBar().addMenu(tr("&Панели"))
        m_panels.addAction(self.act_alt_f1)
        m_panels.addAction(self.act_alt_f2)
        m_panels.addAction(self._find_action(tr("Поменять панели местами")))
        m_panels.addSeparator()
        m_panels.addAction(self._find_action(tr("Сравнить каталоги")))
        m_panels.addAction(self._find_action(tr("Синхронизация с облаком…")))

        m_help = self.menuBar().addMenu(tr("&Справка"))
        about = QAction(tr("О программе"), self)
        about.triggered.connect(self._about)
        m_help.addAction(about)

    def _load_plugins(self) -> None:
        self._plugin_dialogs = []
        self._plugins, self._plugin_errors = plugins_mod.load_all(self)
        for plugin in self._plugins:
            try:
                plugin.on_loaded()
            except Exception as exc:
                self._status(tr("Модуль {name}: {err}").format(
                    name=getattr(plugin, "id", "?"), err=exc))
        tools = self.menuBar().addMenu(tr("&Инструменты"))
        for plugin in self._plugins:
            try:
                actions = plugin.tools_actions()
            except Exception:
                actions = []
            for title, slot, hotkey in actions:
                act = tools.addAction(title)
                if hotkey:
                    act.setShortcut(hotkey)
                act.triggered.connect(slot)
        tools.addSeparator()
        act = tools.addAction(tr("Плагины…"))
        act.triggered.connect(self._manage_plugins)
        for name, err in self._plugin_errors:
            self._status(tr("Модуль {name} не загрузился: {err}").format(
                name=name, err=err))

    def _manage_plugins(self) -> None:
        from sphaera_commander.dialogs import PluginsDialog

        PluginsDialog(self).exec()

    def _plugin_context_actions(self, panel, entry):
        out = []
        for plugin in getattr(self, "_plugins", []):
            try:
                out.extend(plugin.context_actions(panel, entry))
            except Exception:
                pass
        return out

    def _make_corner_close(self):
        """Крестик в строке меню: в полноэкранном рамки окна нет
        (GNOME показывает её без кнопок), закрывать должно приложение."""
        from PySide6.QtWidgets import QToolButton

        self._corner_close = QToolButton(self.menuBar())
        self._corner_close.setText("✕")
        self._corner_close.setToolTip(tr("Закрыть приложение"))
        self._corner_close.setAutoRaise(True)
        self._corner_close.clicked.connect(self.close)
        self._corner_close.hide()  # виден только в полноэкранном режиме
        self.menuBar().setCornerWidget(self._corner_close, Qt.TopRightCorner)

    def _set_language(self, code: str) -> None:
        config.qsettings().setValue("view/language", code)
        self._status(tr("Язык изменится после перезапуска приложения"))

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
        if self.act_thumbs.isChecked():
            self.toggle_thumbnails()
        if self.act_dirsizes.isChecked():
            self.toggle_dirsizes()
        self.left.wait_loaded()
        self.right.wait_loaded()

    def _set_theme(self, mode: str) -> None:
        from . import theme as theme_mod

        config.qsettings().setValue("view/theme", mode)
        theme_mod.apply_theme(QApplication.instance(), mode)
        names = {"dark": tr("Тёмная тема (Iustitia)"),
                 "light": tr("Светлая тема (пергамент)"),
                 "system": tr("Системная тема")}
        self._status(tr("Тема: {name}").format(name=names.get(mode, mode)))

    def toggle_dirsizes(self):
        """Показывать вычисленные размеры каталогов в колонке «Размер»."""
        on = self.act_dirsizes.isChecked()
        config.qsettings().setValue("view/dirsizes", on)
        self.left.set_dirsizes(on)
        self.right.set_dirsizes(on)
        self._status(tr("Размеры каталогов: ")
                     + (tr("включены") if on else tr("выключены")))

    def toggle_thumbnails(self):
        on = self.act_thumbs.isChecked()
        self.left.set_thumbnails(on)
        self.right.set_thumbnails(on)
        config.qsettings().setValue("view/thumbnails", on)

    def toggle_colorize(self):
        from . import colorize

        on = self.act_colorize.isChecked()
        colorize.set_enabled(on)
        config.qsettings().setValue("view/colorize", on)
        # ForegroundRole кэшируется вью: перерисовать модели обеих панелей
        for panel in (self.left, self.right):
            panel.model.layoutChanged.emit()

    # ------------------------------------------------------------- служебные

    def eventFilter(self, obj, event):
        if event.type() == event.Type.FocusIn and obj in (self.left.view, self.right.view):
            self._set_active(self.left if obj is self.left.view else self.right)
        return super().eventFilter(obj, event)

    def _set_active(self, panel: FilePanel) -> None:
        self.active = panel
        self.left.set_active(panel is self.left)
        self.right.set_active(panel is self.right)
        self._update_quick_view_visibility()
        self._update_quick_preview()
        self._update_title()

    def _delete_by_key(self, permanent: bool) -> None:
        """Del/Shift+Del в таблице: та же обработка, что F8/Shift+F8."""
        if permanent:
            self.do_delete_permanent()
        else:
            self.do_delete()

    def _switch_panels(self) -> None:
        """Tab/Shift+Tab (TC): активной становится другая панель,
        фокус переходит в её таблицу."""
        other = self._other()
        self._set_active(other)
        other.view.setFocus()

    # ------------------------------------------------------------- быстрый просмотр

    def _inactive_preview(self) -> QuickPreview:
        return self.quick_right if self.active is self.left else self.quick_left

    @property
    def quick_preview(self) -> QuickPreview:
        """Активный предпросмотр (на неактивной стороне) — для тестов и статуса."""
        return self._inactive_preview()

    def toggle_quick_view(self):
        self.quick_view = not self.quick_view
        self.act_quick.setChecked(self.quick_view)
        config.qsettings().setValue("view/quick_view", self.quick_view)
        self._update_quick_view_visibility()
        self._update_quick_preview()

    def _update_quick_view_visibility(self) -> None:
        if not self.quick_view:
            self.left_stack.setCurrentWidget(self.left)
            self.right_stack.setCurrentWidget(self.right)
            return
        self.left_stack.setCurrentWidget(
            self.left if self.active is self.left else self.quick_left)
        self.right_stack.setCurrentWidget(
            self.quick_right if self.active is self.left else self.right)

    def _on_panel_cursor(self, panel: FilePanel, entry) -> None:
        if self.quick_view and panel is self.active:
            self._inactive_preview().show_entry(entry)

    def _update_quick_preview(self) -> None:
        if self.quick_view:
            self._inactive_preview().show_entry(self.active.current_entry())

    def do_search(self):
        dlg = SearchDialog(self, self.active.current_path(),
                           other_root=self._other().current_path())
        dlg.attach()
        dlg.openRequested.connect(self._open_search_hit)
        self._search_dialog = dlg  # держим ссылку, окно немодальное
        dlg.show()
        dlg.raise_()

    def _open_search_hit(self, path: str, line: int) -> None:
        open_viewer(self, path, [path], editable=False,
                    goto_line=line if line > 0 else 0).exec()

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

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.menuBar().show()
            self._corner_close.hide()
            self.act_fullscreen.setChecked(False)
        else:
            self.showFullScreen()
            # меню не прячем: выход F11/Esc, крестик — в углу меню
            self.menuBar().show()
            self._corner_close.show()
            self.act_fullscreen.setChecked(True)

    def _exit_fullscreen(self):
        if self.isFullScreen():
            self.toggle_fullscreen()

    def _sort_active(self, col: int):
        self.active.model.apply_sort(col)
        self.active.view.scrollToTop()

    def _places_menu(self, panel: FilePanel, anchor=None):
        if anchor is None and getattr(panel, "drive_button", None) is not None:
            anchor = panel.drive_button
        menu = QMenu(tr("Смена диска"), self)
        menu.addAction(tr("Домой"), lambda: panel.cd(os.path.expanduser("~")))
        menu.addAction(tr("Корень (/)"), lambda: panel.cd("/"))
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
        if mounts.available():
            menu.addSeparator()
            for dev in mounts.block_devices():
                label = dev["label"] or dev["name"]
                if dev["mountpoint"]:
                    if dev["mountpoint"] == "/":
                        continue  # корень не размонтируем
                    menu.addAction(
                        tr("⏏ {label} ({mountpoint}) — размонтировать").format(
                            label=label, mountpoint=dev["mountpoint"]),
                        lambda d=dev: self._unmount_device(d, panel))
                else:
                    menu.addAction(
                        tr("Подключить {label} [{size}]").format(
                            label=label, size=dev["size"]),
                        lambda d=dev: self._mount_device(d, panel))
        menu.addSeparator()
        menu.addAction(tr("Синхронизация с облаком…"), self.open_cloud_sync)
        for remote in cloudsync_remotes():
            name = cloudmount.title(remote)
            if cloudmount.is_mounted(remote):
                path = cloudmount.mountpoint(remote)
                menu.addAction(
                    tr("{title} — открыть").format(title=name),
                    lambda p=path: panel.cd(p))
                menu.addAction(
                    tr("⏏ {title} — отключить облачный диск").format(title=name),
                    lambda r=remote: self._unmount_cloud(r, panel))
            else:
                menu.addAction(
                    tr("{title} — подключить как диск").format(title=name),
                    lambda r=remote, pnl=panel: self._mount_cloud(r, pnl))
        if anchor is not None:
            menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        else:
            menu.exec(self.cursor().pos())

    def _hotlist_menu(self):
        """Ctrl+D: добавить текущую / перейти / изменить список (как в TC)."""
        panel = self.active
        menu = QMenu(tr("Избранные папки"), self)
        menu.addAction(
            tr("Добавить текущую папку"),
            lambda: self._hotlist_add(panel.current_path()))
        menu.addSeparator()
        for item in hotlist.load_hotlist():
            menu.addAction(
                item.get("title") or item["path"],
                lambda p=item["path"], pnl=panel: pnl.cd(p))
        menu.addSeparator()
        menu.addAction(tr("Изменить список…"), self._hotlist_edit)
        menu.exec(self.cursor().pos())

    def _hotlist_add(self, path: str):
        hotlist.save_hotlist(
            hotlist.add_current(path, hotlist.load_hotlist()))

    def _hotlist_edit(self):
        from .dialogs import HotlistEditor

        dlg = HotlistEditor(self, hotlist.load_hotlist(),
                            self.active.current_path())
        if dlg.exec():
            hotlist.save_hotlist(dlg.result_items())

    @staticmethod
    def _cloud_remotes() -> list[str]:
        return cloudsync_remotes()

    def _mount_cloud(self, remote: str, panel: FilePanel) -> None:
        self._status(tr("⏳ подключение облачного диска {title}…").format(
            title=cloudmount.title(remote)))

        def on_done(path, error):
            def apply():
                if error is not None:
                    self._status(tr("Не удалось подключить {title}: {err}").format(
                        title=cloudmount.title(remote), err=error))
                    return
                panel.cd(path)
                self._status(tr("{title} подключён: {path}").format(
                    title=cloudmount.title(remote), path=path))
            self.gui_call.emit(apply)

        cloudmount.mount_async(remote, on_done)

    def _unmount_cloud(self, remote: str, panel: FilePanel) -> None:
        try:
            cloudmount.unmount(remote)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            QMessageBox.warning(self, tr("Облачный диск"),
                                tr("Не удалось отключить: {err}").format(err=exc))
            return
        if cloudmount.is_cloud_path(panel.current_path()):
            panel.up()
        self._status(tr("{title} отключён").format(title=cloudmount.title(remote)))

    def _mount_device(self, dev: dict, panel: FilePanel) -> None:
        ok, message = mounts.mount(dev["path"])
        if not ok:
            QMessageBox.warning(self, tr("Монтирование"),
                                tr("{path}:\n{message}").format(path=dev["path"], message=message))
            return
        self._status(tr("Примонтировано: {message}").format(message=message))
        if os.path.isdir(message):
            panel.cd(message)
        panel.refresh()

    def _unmount_device(self, dev: dict, panel: FilePanel) -> None:
        ok, message = mounts.unmount(dev["path"])
        if not ok:
            QMessageBox.warning(self, tr("Размонтирование"),
                                tr("{path}:\n{message}").format(path=dev["path"], message=message))
            return
        self._status(tr("Размонтировано: {name}").format(name=dev["name"]))
        for p in (self.left, self.right):
            if p.current_path().startswith(dev["mountpoint"]):
                p.up()
        panel.refresh()

    def _about(self):
        from pathlib import Path

        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPixmap

        box = QMessageBox(self)
        box.setWindowTitle(tr("О программе"))
        logo = Path(__file__).parent / "assets" / "logo.png"
        if logo.exists():
            pixmap = QPixmap(str(logo)).scaled(
                96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            box.setIconPixmap(pixmap)
        box.setText(
            "<span style=\"font-family:'PT Serif', Georgia, serif; "
            "font-size:15pt;\">Sphaera Commander</span><br>"
            "<span style='color:#c6baa6;'>"
            + tr("версия {v}").format(v=__version__) + "</span>")
        box.setInformativeText(
            tr("Двухпанельный файловый менеджер для Linux в духе Total Commander.<br><br>"
               "Tab — панели, F5/F6 — копирование/перенос, F3/F4 — просмотр/правка,<br>"
               "Alt+F7 — поиск, Ctrl+Q — быстрый просмотр, F8 — корзина.<br><br>"
               "<span style='color:#5fbab4;'>Айдентика — Iustitia:</span> "
               "графит · пергамент · бирюза · бронза."))
        box.exec()

    def open_cloud_sync(self, start_pair: dict | None = None) -> None:
        """Диалог синхронизации с облаком (ya.d + rclone)."""
        from sphaera_commander.dialogs import CloudSyncDialog

        if getattr(self, "_cloud_dialog", None) is None \
                or not self._cloud_dialog.isVisible():
            panel = self.active
            cur = panel.current_entry()
            current_dir = (cur.path if cur is not None and cur.is_dir
                           else panel.current_path())
            self._cloud_dialog = CloudSyncDialog(self, current_dir)
        self._cloud_dialog.show()
        self._cloud_dialog.raise_()
        if start_pair is not None:
            self._cloud_dialog.start_sync(start_pair)

    def _context_menu(self, panel: FilePanel, pos) -> None:
        self._set_active(panel)
        entry = panel.current_entry()
        menu = QMenu(self)
        if entry is not None and entry.is_dir and not panel.is_vfs:
            from sphaera_commander import cloudsync

            pair = cloudsync.pair_for_dir(entry.path)
            if pair is not None:
                menu.addAction(tr("Синхронизировать с облаком"),
                               lambda p=pair: self.open_cloud_sync(p))
                menu.addSeparator()
        if entry is not None and not entry.is_dir:
            menu.addAction(self._find_action(tr("Просмотр")))
            menu.addAction(self._find_action(tr("Правка")))
            menu.addSeparator()
        if entry is not None and archive_format(entry.path):
            menu.addAction(self._find_action(tr("Распаковать…")))
        for title in (tr("Копирование"), tr("Перенос"), tr("Удаление (в корзину)"),
                      tr("Переименовать")):
            menu.addAction(self._find_action(title))
        menu.addSeparator()
        menu.addAction(self._find_action(tr("Открыть системным приложением")))
        menu.addAction(tr("Копировать полный путь"),
                       lambda: self._copy_paths(panel))
        if not panel.is_vfs and entry is not None:
            menu.addAction(self._find_action(tr("Свойства")))
        plugin_actions = self._plugin_context_actions(panel, entry)
        if plugin_actions:
            menu.addSeparator()
            for title, slot in plugin_actions:
                menu.addAction(title, slot)
        menu.exec(panel.view.viewport().mapToGlobal(pos))

    def _copy_paths(self, panel: FilePanel) -> None:
        """Полные пути отмеченных объектов (или под курсором/панели) в буфер обмена."""
        entries = panel.selected_entries()
        if entries:
            text = "\n".join(e.path for e in entries)
            self._status(tr("Скопировано путей: {n}").format(n=len(entries)))
        else:
            text = panel.current_path()
            self._status(tr("Путь панели скопирован в буфер обмена"))
        QApplication.clipboard().setText(text)

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
                QMessageBox.warning(self, tr("Архив"), tr("Не удалось извлечь:\n{exc}").format(exc=exc))
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
                QMessageBox.warning(self, tr("Архив"), tr("Не удалось извлечь:\n{exc}").format(exc=exc))
                return None, [], None, ""
            return tmp, [tmp], browser, member
        files = [e.path for e in self.active.model.entries if not e.is_dir]
        return entry.path, files, None, ""

    def open_viewer_cmd(self):
        """F3: просмотр в немодальном окне (как в TC) — панели живут своей
        жизнью; ссылки на окна держим, пока не закроют."""
        path, files, _browser, _member = self._viewer_targets()
        if path is None:
            self._status(tr("Нет файла под курсором"))
            return
        dlg = open_viewer(self, path, files, editable=False, modal=False)
        self._viewers = [v for v in getattr(self, "_viewers", [])
                         if v.isVisible()]
        self._viewers.append(dlg)
        dlg.show()
        dlg.raise_()

    def open_editor_cmd(self):
        path, files, browser, member = self._viewer_targets()
        if path is None:
            self._status(tr("Нет файла под курсором"))
            return
        doc_kind = pv.document_kind(path)
        if doc_kind is not None:
            # форматы со встроенной обработкой: pdf/docx/rtf/doc/xls/xlsx
            # редактируются, остальные (pptx/csv/html/xml/fb2/epub) —
            # только просмотр
            open_viewer(self, path, files,
                        editable=doc_kind in ("pdf", "docx", "rtf", "doc",
                                              "xls", "xlsx")).exec()
            return
        if browser is None:
            try:
                with open(path, "rb") as f:
                    head = f.read(8192)
            except OSError as exc:
                QMessageBox.warning(self, tr("Правка"), tr("Не удалось открыть файл:\n{exc}").format(exc=exc))
                return
            if looks_binary(head):
                QMessageBox.information(
                    self, tr("Правка"),
                    tr("Похоже, файл бинарный — встроенный редактор его не открывает."))
                return
        dlg = open_viewer(self, path, files, editable=True)
        dlg.exec()
        if browser is not None and dlg.saved_on_close:
            self._enqueue_op(
                tr("Запись в архив {name}").format(
                    name=os.path.basename(browser.archive_path)),
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

    def do_properties(self):
        """Alt+Enter: свойства объекта под курсором (права/владелец/размеры)."""
        if self.active.is_vfs:
            self._status(tr("Свойства в архиве не показываются"))
            return
        entry = self.active.current_entry()
        if entry is None:
            self._status(tr("Нет объекта под курсором"))
            return
        from .dialogs import PropertiesDialog

        def apply_async(fn, after):
            self._enqueue_op(
                tr("Права: {path}").format(path=entry.path), fn,
                after=lambda: (self.refresh_all(), after()))

        dlg = PropertiesDialog(self, entry.path, apply_async=apply_async)
        if dlg.exec():
            self.refresh_all()

    def do_create_file(self):
        """Shift+F4 (TC): создать файл и открыть во встроенном редакторе."""
        panel = self.active
        if panel.is_vfs:
            self._status(tr("Создание файлов в архиве не поддерживается"))
            return
        name, ok = QInputDialog.getText(self, tr("Новый файл"),
                                        tr("Имя файла:"), text="новый.txt")
        if not ok or not name.strip():
            return
        name = name.strip()
        path = os.path.join(panel.current_path(), name)
        if not os.path.exists(path):
            try:
                with open(path, "w"):
                    pass
            except OSError as exc:
                QMessageBox.critical(self, tr("Ошибка"),
                                     tr("Не удалось создать файл:\n{err}").format(
                                         err=exc.strerror or exc))
                return
        panel.reveal(name)
        open_viewer(self, path, [path], editable=True).exec()

    def do_symlink(self):
        """Символьная ссылка на объект под курсором (цель — абсолютный путь)."""
        panel = self.active
        if panel.is_vfs:
            self._status(tr("Ссылки в архиве не создаются"))
            return
        entry = panel.current_entry()
        if entry is None:
            self._status(tr("Нет объекта под курсором"))
            return
        default = tr("Ссылка на {name}").format(name=entry.name)
        name, ok = QInputDialog.getText(
            self, tr("Символьная ссылка"),
            tr("Имя ссылки (цель: {target}):").format(target=entry.path),
            text=default)
        if not ok or not name.strip():
            return
        dest = os.path.join(panel.current_path(), name.strip())
        if os.path.lexists(dest):
            QMessageBox.warning(self, tr("Символьная ссылка"),
                                tr("{out}\nуже существует").format(out=dest))
            return
        try:
            os.symlink(entry.path, dest)
        except OSError as exc:
            QMessageBox.critical(self, tr("Ошибка"),
                                 tr("Не удалось создать ссылку:\n{err}").format(
                                     err=exc.strerror or exc))
            return
        panel.reveal(name.strip())

    def do_hardlink(self):
        """Жёсткая ссылка на файл: создаётся в каталоге противоположной панели."""
        panel = self.active
        if panel.is_vfs or self._other().is_vfs:
            self._status(tr("Ссылки в архиве не создаются"))
            return
        entry = panel.current_entry()
        if entry is None or entry.is_dir:
            self._status(tr("Жёсткая ссылка — только для файла под курсором"))
            return
        dest = os.path.join(self._other().current_path(), entry.name)
        if os.path.lexists(dest):
            QMessageBox.warning(self, tr("Жёсткая ссылка"),
                                tr("{out}\nуже существует").format(out=dest))
            return
        try:
            os.link(entry.path, dest)
        except OSError as exc:
            QMessageBox.critical(self, tr("Ошибка"),
                                 tr("Не удалось создать ссылку:\n{err}").format(
                                     err=exc.strerror or exc))
            return
        self._status(tr("Жёсткая ссылка создана: {path}").format(path=dest))
        self.refresh_all()

    def _panel_history_menu(self):
        """Alt+↓: последние каталоги активной панели (список сверху — свежие)."""
        panel = self.active
        menu = QMenu(tr("История панели"), self)
        for path in reversed(panel.history()):
            menu.addAction(path, lambda p=path: panel.cd(p))
        menu.exec(self.cursor().pos())

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
            self._status(tr("Архив в панели назначения открыт только для чтения"))
            return
        sources = self.active.selected_entries()
        if not sources:
            self._status(tr("Нет отмеченных объектов (Insert — отметить)"))
            return
        dest = self._other().current_path()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            plan = plan_copy_move(sources, dest, move)
        except OSError as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, tr("Ошибка"), tr("Не удалось построить план:\n{exc}").format(exc=exc))
            return
        QApplication.restoreOverrideCursor()
        if not plan.jobs:
            self._status(tr("Нет объектов для обработки"))
            return
        kind = KIND_MOVE if move else KIND_COPY
        title = tr("Перенос") if move else tr("Копирование")
        self._enqueue_op(title, self._fs_fn(kind, plan, sources),
                         after=lambda m=move: self._after_copy_move(m))

    def _after_copy_move(self, move: bool):
        self.refresh_all()
        if move:
            self.active.model.clear_marks()

    def _on_drop(self, paths: list[str], target: str, panel: FilePanel,
                 move: bool = False) -> None:
        """Файлы перетащили в панель (из другого приложения или между панелями).
        С Shift — перенос (как в TC), без — копирование."""
        if "::" in target:
            self._status(tr("В архив перетащить нельзя"))
            return
        entries = []
        for p in paths:
            if os.path.dirname(p) == target:
                continue  # уже в этой папке
            entry = entry_for(p)
            if entry is not None:
                entries.append(entry)
        if not entries:
            self._status(tr("Перетаскивать нечего: объекты уже в этой папке"))
            return
        try:
            plan = plan_copy_move(entries, target, move=move)
        except OSError as exc:
            QMessageBox.critical(self, tr("Ошибка"), tr("Не удалось построить план:\n{exc}").format(exc=exc))
            return
        policy = POLICY_OVERWRITE
        if plan.conflicts:
            policy = confirm_overwrite(self, plan.conflicts, target)
            if policy == "cancel":
                return

        kind = KIND_MOVE if move else KIND_COPY
        title = (tr("Перенос (перетащено): {n}") if move
                 else tr("Копирование (перетащено): {n}")).format(n=len(entries))

        def fn(progress_cb, is_cancelled):
            return execute(kind, plan, entries, policy,
                           progress_cb, is_cancelled, ask_cb=self._bridge.ask)

        def after(p=panel):
            self.refresh_all()
            p.refresh()
            if move:
                for pnl in (self.left, self.right):
                    pnl.model.clear_marks()

        self._enqueue_op(title, fn, after=after)

    def _extract_selected(self, move: bool) -> None:
        """Извлечение выбранных членов архива в противоположную панель (F5/F6)."""
        panel = self.active
        browser = panel.vfs
        sources = panel.selected_entries()
        if not sources or browser is None:
            self._status(tr("Нет отмеченных объектов (Insert — отметить)"))
            return
        if self._other().is_vfs:
            self._status(tr("Извлечение в другой архив не поддерживается"))
            return
        members = [e.path.partition("::")[2] for e in sources]
        dest = self._other().current_path()
        title = tr("Перенос из архива") if move else tr("Извлечение из архива")
        extract_fn = (lambda progress_cb, is_cancelled:
                      browser.extract_members(members, dest, progress_cb,
                                              is_cancelled,
                                              ask_cb=self._bridge.ask))
        if move:
            self._enqueue_op(title, extract_fn)
            self._enqueue_op(
                tr("Удаление из {name}").format(
                name=os.path.basename(browser.archive_path)),
                lambda progress_cb, is_cancelled: browser.delete_members(members),
                after=self.refresh_all)
        else:
            self._enqueue_op(title, extract_fn, after=self.refresh_all)

    def do_delete(self):
        """F8 — переместить в корзину (как в Nautilus); Shift+F8 — безвозвратно."""
        self._delete_selected(permanent=False)

    def do_delete_permanent(self):
        self._delete_selected(permanent=True)

    def _delete_selected(self, permanent: bool):
        if self.active.is_vfs:
            self._delete_in_archive()
            return
        sources = self.active.selected_entries()
        if not sources:
            self._status(tr("Нет отмеченных объектов (Insert — отметить)"))
            return
        if permanent:
            action, button = tr("Удалить безвозвратно"), tr("Удалить навсегда")
            title = tr("Удаление безвозвратно")
            fn = self._fs_fn(KIND_DELETE, None, sources)
        else:
            action, button = tr("Переместить в корзину"), tr("В корзину")
            title = tr("Удаление (в корзину)")
            fn = (lambda progress_cb, is_cancelled:
                  execute_trash(sources, progress_cb, is_cancelled))
        if not confirm_delete(self, sources, self.active.current_path(),
                              action=action, button=button):
            return
        self._enqueue_op(title, fn, after=self.refresh_all)

    def _delete_in_archive(self) -> None:
        panel = self.active
        browser = panel.vfs
        sources = panel.selected_entries()
        if not sources or browser is None:
            self._status(tr("Нет отмеченных объектов (Insert — отметить)"))
            return
        if not confirm_delete(self, sources, panel.current_path()):
            return
        members = [e.path.partition("::")[2] for e in sources]
        self._enqueue_op(
            tr("Удаление из {name}").format(
                name=os.path.basename(browser.archive_path)),
            lambda progress_cb, is_cancelled: browser.delete_members(members),
            after=self.refresh_all)

    def do_pack(self):
        if self.active.is_vfs:
            self._status(tr("Сначала извлеките объекты из архива"))
            return
        sources = self.active.selected_entries()
        if not sources:
            self._status(tr("Нет отмеченных объектов (Insert — отметить)"))
            return
        dest_panel = self._other()
        default_name = os.path.splitext(sources[0].name)[0] + ".zip"
        name, ok = QInputDialog.getText(self, tr("Запаковать"),
                                        tr("Имя архива (в {dir}):").format(dir=dest_panel.current_path()),
                                        text=default_name)
        if not ok or not name.strip():
            return
        fmt, ok = QInputDialog.getItem(self, tr("Запаковать"), tr("Формат:"),
                                       ("zip", "tar.gz", "tar.bz2", "tar.xz", "7z"), 0, False)
        if not ok:
            return
        out = os.path.join(dest_panel.current_path(), name.strip())
        if os.path.lexists(out):
            ret = QMessageBox.question(self, tr("Файл существует"),
                                       tr("{out}\nПерезаписать?").format(out=out),
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                return
        self._enqueue_op(tr("Запаковка"),
                         lambda progress_cb, is_cancelled:
                         pack_items(sources, out, fmt, progress_cb, is_cancelled),
                         after=lambda: dest_panel.reveal(os.path.basename(out)))

    def do_unpack(self):
        entries = [e for e in self.active.selected_entries() if archive_format(e.path)]
        if not entries:
            self._status(tr("Выберите архив (.zip, .tar, .tgz, .tar.bz2, .tar.xz)"))
            return
        dest = self._other().current_path()
        bridge = self._bridge
        for entry in entries:
            def fn(progress_cb, is_cancelled, _archive=entry.path):
                return unpack_archive(_archive, dest, progress_cb, is_cancelled,
                                      ask_cb=bridge.ask)

            self._enqueue_op(tr("Распаковка {name}").format(name=entry.name), fn, after=self.refresh_all)

    def do_batch_rename(self):
        panel = self.active
        if panel.is_vfs:
            self._status(tr("Групповое переименование в архиве не поддерживается"))
            return
        names = [e.name for e in panel.selected_entries()]
        if not names:
            self._status(tr("Нет объектов для переименования"))
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
                errors.append(tr("{old} — назначение уже существует: {new}").format(old=old, new=new))
                continue
            try:
                os.rename(src, dst)
                done += 1
                if first_new is None:
                    first_new = new
            except OSError as exc:
                errors.append(tr("{old} — {err}").format(old=old, err=exc.strerror or exc))
        panel.reveal(first_new or names[0])
        if errors:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle(tr("Переименование"))
            box.setText(tr("Переименовано: {done}, ошибок: {n}.").format(done=done, n=len(errors)))
            box.setDetailedText("\n".join(errors[:200]))
            box.exec()
        else:
            self._status(tr("Переименовано: {n}").format(n=done))

    def compare_dirs(self):
        if self.left.is_vfs or self.right.is_vfs:
            self._status(tr("Сравнение каталогов работает для обычных каталогов"))
            return
        left = {e.name: e for e in self.left.model.entries if not e.is_dir}
        right = {e.name: e for e in self.right.model.entries if not e.is_dir}
        diff_left, diff_right = compare_name_sets(left, right)
        self.left.model.set_compared(diff_left)
        self.right.model.set_compared(diff_right)
        self._status(tr("Сравнение каталогов: {left} отличий слева, {right} справа (выделено синим)").format(
            left=len(diff_left), right=len(diff_right)))

    def do_mkdir(self):
        panel = self.active
        if panel.is_vfs:
            self._status(tr("Создание папок в архиве не поддерживается"))
            return
        name, ok = QInputDialog.getText(self, tr("Новая папка"), tr("Имя папки:"), text="")
        if not ok or not name.strip():
            return
        try:
            os.makedirs(os.path.join(panel.current_path(), name.strip()))
        except OSError as exc:
            QMessageBox.critical(self, tr("Ошибка"),
                                 tr("Не удалось создать папку:\n{err}").format(err=exc.strerror or exc))
            return
        panel.reveal(name.strip())

    def do_rename(self):
        panel = self.active
        if panel.is_vfs:
            self._status(tr("Переименование в архиве не поддерживается"))
            return
        entry = panel.current_entry()
        if entry is None:
            self._status(tr("Нет объекта под курсором"))
            return
        dlg = make_rename_dialog(self, entry)
        name = dlg.textValue() if dlg.exec() else ""
        if not name or not name.strip() or name == entry.name:
            return
        try:
            os.rename(entry.path, os.path.join(panel.current_path(), name.strip()))
        except OSError as exc:
            QMessageBox.critical(self, tr("Ошибка"),
                                 tr("Не удалось переименовать:\n{err}").format(err=exc.strerror or exc))
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
                self._status(tr("cd: каталог не найден: {path}").format(path=resolved))
            return
        if self._proc is not None and self._proc.state() != QProcess.NotRunning:
            self._status(tr("Предыдущая команда ещё выполняется"))
            return
        self._out = []
        self._proc = QProcess(self)
        self._proc.setWorkingDirectory(self.active.current_path())
        self._proc.readyReadStandardOutput.connect(self._collect_out)
        self._proc.readyReadStandardError.connect(self._collect_out)
        self._proc.finished.connect(self._cmd_finished)
        self.statusBar().showMessage(f"$ {cmd}")
        argv = (shell_argv_provider(cmd) if shell_argv_provider
                else ["/bin/sh", "-c", cmd])
        self._proc.start(argv[0], argv[1:])

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
            text = output or tr("(без вывода)")
            if len(text) > 8000:
                text = text[:8000] + "\n…"
            box = QMessageBox(self)
            box.setWindowTitle(tr("Командная строка"))
            box.setIcon(QMessageBox.Information if code == 0 else QMessageBox.Warning)
            box.setText(tr("$ {cmd}\nкод выхода: {code}").format(cmd=cmd, code=code))
            box.setDetailedText(text)
            box.exec()
        else:
            self._status(tr("$ {cmd} — готово").format(cmd=cmd))
        self.refresh_all()

    # ------------------------------------------------------------- жизненный цикл

    def closeEvent(self, event):
        if getattr(self, "_plugins", None):
            plugins_mod.shutdown(self._plugins)
        cloudmount.unmount_ours()
        if self._thread is not None:
            ret = QMessageBox.question(
                self, tr("Операция выполняется"),
                tr("Идёт файловая операция. Прервать её и выйти?"),
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

    base = Path(__file__).parent / "assets"
    for name in ("icon-256.png", "icon-128.png"):
        if (base / name).exists():
            return QIcon(str(base / name))
    return QIcon()


def _selfcheck() -> int:
    """Импорт всех форматных библиотек (для проверки замороженной сборки)."""
    import importlib

    modules = ("pypdfium2", "pypdf", "docx", "mammoth", "openpyxl", "pptx",
               "xlrd", "xlwt", "striprtf")
    failed = []
    for name in modules:
        try:
            importlib.import_module(name)
            print(f"selfcheck: {name} OK")
        except Exception as exc:
            failed.append(name)
            print(f"selfcheck: {name} FAIL: {exc}")
    print("SELFCHECK:", "PASS" if not failed else f"FAIL {failed}")
    return 0 if not failed else 1


def _apply_language() -> None:
    """Выбранный в меню язык применяется до построения интерфейса."""
    val = config.qsettings().value("view/language", "ru")
    if val in ("ru", "en", "zh"):
        i18n.LANG = val


def main(argv=None):
    _apply_language()
    import argparse

    raw = list(sys.argv[1:] if argv is None else argv)
    if os.environ.get("SPHAERA_SELFCHECK") == "1":
        return _selfcheck()
    parser = argparse.ArgumentParser(
        prog="sphaera-commander",
        description=tr("Sphaera Commander — двухпанельный файловый менеджер"))
    parser.add_argument("paths", nargs="*", metavar=tr("КАТАЛОГ"),
                        help=tr("каталог для левой (и правой) панели"))
    parser.add_argument("--version", action="version",
                        version=f"Sphaera Commander {__version__}")
    ns = parser.parse_args(raw)

    app = QApplication([sys.argv[0] if argv is None else "sphaera-commander"])
    app.setApplicationName("Sphaera Commander")
    app.setOrganizationName("Sphaera")
    app.setDesktopFileName("sphaera-commander")
    app.setWindowIcon(_app_icon())
    from . import theme

    theme.apply_theme(app, theme.current_mode())
    win = MainWindow()
    for panel, path in ((win.left, ns.paths[0] if ns.paths else None),
                        (win.right, ns.paths[1] if len(ns.paths) > 1 else None)):
        if path:
            panel.cd(path)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
