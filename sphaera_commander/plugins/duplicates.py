"""Модуль «Поиск дубликатов»: рекурсивный обход каталога активной панели,
группировка по размеру, затем по SHA-256; группы одинаковых файлов —
в таблице, выбранные можно удалить (через корзину, с подтверждением)."""

from __future__ import annotations

import hashlib
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


def _sha256(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


class DuplicatesDialog(QDialog):
    groupReady = Signal(list)   # [(путь, размер)] — одна группа дубликатов
    scanDone = Signal(int, int)  # групп, «пустых» байт

    def __init__(self, parent, root: str):
        super().__init__(parent)
        self.app = parent
        self.root = root
        self.setWindowTitle(tr("Поиск дубликатов: {name}").format(
            name=os.path.basename(root) or root))
        self.resize(880, 560)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            (tr("Файл"), tr("Размер"), tr("Группа")))
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView_stretch())
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().hide()

        self.status = QLabel(tr("Чтение…"))
        row = QHBoxLayout()
        btn_delete = QPushButton(tr("Удалить выбранные (в корзину)"))
        btn_delete.clicked.connect(self._delete_selected)
        btn_report = QPushButton(tr("Сохранить отчёт…"))
        btn_report.clicked.connect(self._save_report)
        close = QPushButton(tr("Закрыть"))
        close.clicked.connect(self.close)
        row.addWidget(btn_delete)
        row.addWidget(btn_report)
        row.addStretch(1)
        row.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)
        layout.addLayout(row)

        self.groupReady.connect(self._on_group)
        self.scanDone.connect(self._on_done)
        self._cancel = threading.Event()
        threading.Thread(target=self._scan, daemon=True,
                         name="duplicates").start()

    def _scan(self) -> None:
        by_size: dict[int, list[str]] = {}
        for dirpath, _dirs, files in os.walk(self.root):
            for name in files:
                if self._cancel.is_set():
                    return
                path = os.path.join(dirpath, name)
                try:
                    by_size.setdefault(os.path.getsize(path), []).append(path)
                except OSError:
                    continue
        groups = wasted = 0
        for size, paths in sorted(by_size.items(), reverse=True):
            if size == 0 or len(paths) < 2:
                continue
            by_hash: dict[str, list[str]] = {}
            for path in paths:
                try:
                    by_hash.setdefault(_sha256(path), []).append(path)
                except OSError:
                    continue
            for paths_same in by_hash.values():
                if len(paths_same) < 2:
                    continue
                groups += 1
                wasted += size * (len(paths_same) - 1)
                self.groupReady.emit([(p, size) for p in paths_same])
        self.scanDone.emit(groups, wasted)

    def _on_group(self, items):
        self._group_no = getattr(self, "_group_no", 0) + 1
        for path, size in items:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(path))
            self.table.setItem(row, 1, QTableWidgetItem(human_size(size)))
            self.table.setItem(row, 2, QTableWidgetItem(f"#{self._group_no}"))

    def _on_done(self, groups: int, wasted: int):
        self.status.setText(tr("Групп дубликатов: {g}, лишнего: {w}").format(
            g=groups, w=human_size(wasted)))

    def _delete_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        paths = [self.table.item(r, 0).text() for r in rows]
        from sphaera_commander.dialogs import confirm_delete
        from sphaera_commander.fsmodel import entry_for
        from sphaera_commander.ops import execute_trash

        entries = [e for e in (entry_for(p) for p in paths) if e]
        if not confirm_delete(self, entries, self.root):
            return

        def worker():
            result = execute_trash(entries, lambda p: None, lambda: False)
            self.app.gui_call.emit(lambda: self._delete_done(result.ok))

        threading.Thread(target=worker, daemon=True,
                         name="dup-delete").start()

    def _delete_done(self, ok: bool):
        if not ok:
            self.status.setText(tr("Не удалось удалить часть файлов"))
            return
        # убрать удалённые строки
        for r in range(self.table.rowCount() - 1, -1, -1):
            if not os.path.exists(self.table.item(r, 0).text()):
                self.table.removeRow(r)
        self.status.setText(tr("Удалено в корзину"))

    def _save_report(self):
        out = os.path.join(self.root, "duplicates-report.txt")
        with open(out, "w", encoding="utf-8") as f:
            group = None
            for r in range(self.table.rowCount()):
                g = self.table.item(r, 2).text()
                if g != group:
                    group = g
                    f.write(f"--- {g}\n")
                f.write(self.table.item(r, 0).text() + "\n")
        self.status.setText(tr("Отчёт сохранён: {path}").format(path=out))


def QHeaderView_stretch():
    from PySide6.QtWidgets import QHeaderView

    return QHeaderView.Stretch


class Plugin(SphaeraPlugin):
    id = "duplicates"
    title = "Поиск дубликатов"

    def tools_actions(self):
        return [(tr("Поиск дубликатов…"), self._open, None)]

    def context_actions(self, panel, entry):
        if entry is not None and entry.is_dir:
            return [(tr("Поиск дубликатов…"),
                     lambda d=entry.path: self._open_dir(d))]
        return []

    def _open(self):
        self._open_dir(self.app.active.current_path())

    def _open_dir(self, directory: str):
        dlg = DuplicatesDialog(self.app, directory)
        dlg.setModal(False)
        dlg.show()
        if not hasattr(self.app, "_plugin_dialogs"):
            self.app._plugin_dialogs = []
        self.app._plugin_dialogs.append(dlg)


def create(app):
    return Plugin(app)
