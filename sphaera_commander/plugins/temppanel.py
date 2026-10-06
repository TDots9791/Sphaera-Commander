"""Модуль «Временная панель» (Temp panel / эскорт-список): собрать файлы
из разных каталогов в одну коллекцию и разом скопировать/перенести в
нужное место; «Скопировать пути» — вся коллекция в буфер обмена.
Отмечайте файлы (Insert/Ctrl+клик) и жмите
«Добавить во временную панель» — собранное живёт до выхода."""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from sphaera_commander.fsmodel import human_size
from sphaera_commander.i18n import tr
from sphaera_commander.plugin_api import SphaeraPlugin


class TempPanelDialog(QDialog):
    """Содержимое коллекции и действия над ней."""

    def __init__(self, parent, plugin):
        super().__init__(parent)
        self.plugin = plugin
        self.setWindowTitle(tr("Временная панель"))
        self.resize(760, 480)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            (tr("Имя"), tr("Каталог"), tr("Размер")))
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().hide()
        self.table.itemDoubleClicked.connect(self._open_file)

        self.status = QLabel("")
        row = QHBoxLayout()
        btn_copy = QPushButton(tr("Копировать в другую панель"))
        btn_copy.clicked.connect(lambda: self._transfer(move=False))
        btn_move = QPushButton(tr("Перенести в другую панель"))
        btn_move.clicked.connect(lambda: self._transfer(move=True))
        btn_paths = QPushButton(tr("Скопировать пути"))
        btn_paths.setToolTip(
            tr("Все пути собранного — в буфер обмена (по одному в строке)"))
        btn_paths.clicked.connect(self._copy_paths)
        btn_remove = QPushButton(tr("Убрать из списка"))
        btn_remove.clicked.connect(self._remove_selected)
        btn_clear = QPushButton(tr("Очистить"))
        btn_clear.clicked.connect(self._clear)
        close = QPushButton(tr("Закрыть"))
        close.clicked.connect(self.close)
        for w in (btn_copy, btn_move, btn_paths, btn_remove, btn_clear):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)
        layout.addLayout(row)
        self.reload()

    def _copy_paths(self) -> None:
        """Все пути коллекции в буфер обмена — по одному в строке."""
        from PySide6.QtWidgets import QApplication

        paths = [e.path for e in self.plugin.entries]
        QApplication.clipboard().setText("\n".join(paths))
        self.status.setText(tr("Скопировано путей: {n}").format(n=len(paths)))

    # -- отображение ---------------------------------------------------------

    def reload(self):
        entries = self.plugin.entries
        self.table.setRowCount(len(entries))
        total = 0
        for r, entry in enumerate(entries):
            self.table.setItem(r, 0, QTableWidgetItem(entry.name))
            self.table.setItem(r, 1, QTableWidgetItem(
                os.path.dirname(entry.path)))
            self.table.setItem(r, 2, QTableWidgetItem(
                "" if entry.is_dir else human_size(entry.size)))
            total += 0 if entry.is_dir else entry.size
        self.status.setText(tr("Объектов: {n} ({size})").format(
            n=len(entries), size=human_size(total)))

    # -- действия ------------------------------------------------------------

    def _selected(self) -> list:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.plugin.entries[r] for r in rows]

    def _remove_selected(self):
        rows = {i.row() for i in self.table.selectedIndexes()}
        self.plugin.entries = [e for i, e in enumerate(self.plugin.entries)
                               if i not in rows]
        self.reload()

    def _clear(self):
        self.plugin.entries.clear()
        self.reload()

    def _open_file(self, item):
        path = self.plugin.entries[item.row()].path
        from sphaera_commander.viewer import open_viewer

        open_viewer(self, path, [path], editable=False).show()

    def _transfer(self, move: bool):
        entries = self._selected() or list(self.plugin.entries)
        if not entries:
            self.status.setText(tr("Коллекция пуста"))
            return
        from sphaera_commander.ops import (POLICY_OVERWRITE,
                                           execute_copy_move, plan_copy_move)
        from sphaera_commander.dialogs import (OverwriteAskDialog,
                                               show_op_result)

        dest_panel = self.plugin.app._other()
        if dest_panel.is_vfs:
            self.status.setText(tr("Назначение — архив (только чтение)"))
            return
        dest = dest_panel.current_path()
        try:
            plan = plan_copy_move(entries, dest, move)
        except OSError as exc:
            self.status.setText(str(exc))
            return
        if not plan.jobs:
            self.status.setText(tr("Нет объектов для обработки"))
            return
        ask = lambda info: OverwriteAskDialog.ask(self, info)  # noqa: E731
        policy = POLICY_OVERWRITE  # конфликты решает ask_cb (как F5)

        def worker():
            result = execute_copy_move(plan, move, policy,
                                       lambda p: None, lambda: False,
                                       ask_cb=ask)
            self.plugin.app.gui_call.emit(
                lambda: self._transfer_done(move, result))

        threading.Thread(target=worker, daemon=True,
                         name="temppanel").start()

    def _transfer_done(self, move: bool, result):
        from sphaera_commander.dialogs import show_op_result

        show_op_result(self, result)
        if result.ok:
            keep = [e for e in self.plugin.entries
                    if not move]
            self.plugin.entries = keep
            self.reload()
            self.app.refresh_all()


class Plugin(SphaeraPlugin):
    id = "temppanel"
    title = "Временная панель"

    def __init__(self, app):
        super().__init__(app)
        from sphaera_commander.fsmodel import FileEntry

        self.entries: list = []

    def tools_actions(self):
        return [(tr("Временная панель…"), self._show, None)]

    def context_actions(self, panel, entry):
        if panel.selected_entries():
            return [(tr("Добавить во временную панель"), self._add)]
        return []

    def _add(self):
        selected = self.app.active.selected_entries()
        known = {e.path for e in self.entries}
        added = 0
        for e in selected:
            if e.path in known or e.name == "..":
                continue
            self.entries.append(e)
            known.add(e.path)
            added += 1
        self.app._status(tr("Во временной панели: {n} объект(ов)").format(
            n=len(self.entries)))

    def _show(self):
        from PySide6.QtWidgets import QDialog

        dlg = TempPanelDialog(self.app, self)
        dlg.setModal(False)
        dlg.show()
        if not hasattr(self.app, "_plugin_dialogs"):
            self.app._plugin_dialogs = []
        self.app._plugin_dialogs.append(dlg)


def create(app):
    return Plugin(app)
