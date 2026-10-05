"""Модуль «Статистика папки»: размеры, число файлов и подпапок для каждого
объекта каталога активной панели (рекурсивно), сортировка по размеру."""

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

from sphaera_commander.fsmodel import dir_stats, human_size
from sphaera_commander.i18n import tr
from sphaera_commander.plugin_api import SphaeraPlugin


class DirStatsDialog(QDialog):
    rowReady = Signal(object)   # (имя, DirStats, is_dir)
    scanDone = Signal(int, int, int)  # суммарные файлы/папки/байты

    def __init__(self, parent, root: str):
        super().__init__(parent)
        self.root = root
        self.setWindowTitle(tr("Статистика папки: {name}").format(
            name=os.path.basename(root) or root))
        self.resize(760, 520)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            (tr("Объект"), tr("Файлы"), tr("Подпапки"), tr("Размер")))
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().hide()

        self.status = QLabel(tr("Чтение…"))
        close = QPushButton(tr("Закрыть"))
        close.clicked.connect(self.close)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)
        layout.addLayout(row)

        self.rowReady.connect(self._on_row)
        self.scanDone.connect(self._on_done)
        threading.Thread(target=self._scan, daemon=True,
                         name="dirstats").start()

    def _scan(self) -> None:
        tf = td = tb = 0
        for name in sorted(os.listdir(self.root)):
            path = os.path.join(self.root, name)
            if os.path.isdir(path) and not os.path.islink(path):
                stats = dir_stats(path)
                tf += stats.files
                td += stats.dirs + 1
                tb += stats.size
                self.rowReady.emit((name, stats, True))
            else:
                try:
                    size = os.path.getsize(path)
                except OSError:
                    size = 0
                tf += 1
                tb += size
                self.rowReady.emit(
                    (name, type("S", (), {"files": 1, "dirs": 0, "size": size}),
                     False))
        self.scanDone.emit(tf, td, tb)

    def _on_row(self, item) -> None:
        name, stats, _is_dir = item
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(name))
        self.table.setItem(row, 1, QTableWidgetItem(str(stats.files)))
        self.table.setItem(row, 2, QTableWidgetItem(str(stats.dirs)))
        self.table.setItem(row, 3, QTableWidgetItem(human_size(stats.size)))

    def _on_done(self, files: int, dirs: int, size: int) -> None:
        self.status.setText(tr("Всего: {files} • {dirs} • {size}").format(
            files=unit_files(files), dirs=unit_dirs(dirs),
            size=human_size(size)))


def unit_files(n):
    from sphaera_commander.i18n import unit

    return unit(n, ("файл", "файла", "файлов"), ("file", "files"), "个文件")


def unit_dirs(n):
    from sphaera_commander.i18n import unit

    return unit(n, ("папка", "папки", "папок"), ("folder", "folders"), "个文件夹")


class Plugin(SphaeraPlugin):
    id = "dirstats"
    title = "Статистика папки"

    def tools_actions(self):
        return [(tr("Статистика папки…"), self._open, None)]

    def context_actions(self, panel, entry):
        if entry is not None and entry.is_dir:
            return [(tr("Статистика папки…"),
                     lambda d=entry.path: self._open_dir(d))]
        return []

    def _open(self):
        self._open_dir(self.app.active.current_path())

    def _open_dir(self, directory: str):
        dlg = DirStatsDialog(self.app, directory)
        dlg.setModal(False)
        dlg.show()
        if not hasattr(self.app, "_plugin_dialogs"):
            self.app._plugin_dialogs = []
        self.app._plugin_dialogs.append(dlg)


def create(app):
    return Plugin(app)
