"""Модуль «Веточный просмотр»: все файлы каталога рекурсивно одним
плоским списком (как Branch View в Total Commander) — таблица с путями
и размерами, список можно сохранить в файл, двойной клик открывает файл."""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
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


class BranchDialog(QDialog):
    rowReady = Signal(object)     # (путь, размер)
    scanDone = Signal(int)        # число файлов

    def __init__(self, parent, root: str):
        super().__init__(parent)
        self.root = root
        self.setWindowTitle(tr("Веточный просмотр: {name}").format(
            name=os.path.basename(root) or root))
        self.resize(880, 560)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels((tr("Файл"), tr("Размер")))
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().hide()
        self.table.itemDoubleClicked.connect(self._open_file)

        self.status = QLabel(tr("Чтение…"))
        row = QHBoxLayout()
        self.btn_save = QPushButton(tr("Сохранить список…"))
        self.btn_save.clicked.connect(self._save)
        self.btn_save.setEnabled(False)
        close = QPushButton(tr("Закрыть"))
        close.clicked.connect(self.close)
        row.addWidget(self.btn_save)
        row.addStretch(1)
        row.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)
        layout.addLayout(row)

        self.rowReady.connect(self._on_row)
        self.scanDone.connect(self._on_done)
        threading.Thread(target=self._scan, daemon=True,
                         name="branchview").start()

    def _scan(self) -> None:
        total = 0
        for dirpath, _dirs, files in os.walk(self.root):
            for name in files:
                path = os.path.join(dirpath, name)
                try:
                    size = os.path.getsize(path)
                except OSError:
                    size = -1
                total += 1
                self.rowReady.emit((path, size))
        self.scanDone.emit(total)

    def _on_row(self, item) -> None:
        path, size = item
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(
            os.path.relpath(path, self.root)))
        self.table.setItem(row, 1, QTableWidgetItem(
            human_size(size) if size >= 0 else "?"))
        self.table.item(row, 0).setData(Qt.UserRole, path)

    def _on_done(self, total: int) -> None:
        self.status.setText(tr("Файлов: {n}").format(n=total))
        self.btn_save.setEnabled(total > 0)

    def _open_file(self, item) -> None:
        path = self.table.item(item.row(), 0).data(Qt.UserRole)
        from sphaera_commander.viewer import open_viewer

        open_viewer(self, path, [path], editable=False).show()

    def _save(self) -> None:
        out = os.path.join(self.root, "branch-list.txt")
        with open(out, "w", encoding="utf-8") as f:
            for r in range(self.table.rowCount()):
                path = self.table.item(r, 0).data(Qt.UserRole)
                f.write(path + "\n")
        self.status.setText(tr("Список сохранён: {path}").format(path=out))


class Plugin(SphaeraPlugin):
    id = "branchview"
    title = "Веточный просмотр"

    def tools_actions(self):
        return [(tr("Веточный просмотр…"), self._open, None)]

    def context_actions(self, panel, entry):
        if entry is not None and entry.is_dir:
            return [(tr("Веточный просмотр…"),
                     lambda p=entry.path: self._scan(p))]
        return []

    def _open(self):
        self._scan(self.app.active.current_path())

    def _scan(self, directory: str):
        dlg = BranchDialog(self.app, directory)
        dlg.setModal(False)
        dlg.show()
        if not hasattr(self.app, "_plugin_dialogs"):
            self.app._plugin_dialogs = []
        self.app._plugin_dialogs.append(dlg)


def create(app):
    return Plugin(app)
