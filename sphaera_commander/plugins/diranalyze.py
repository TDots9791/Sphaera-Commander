"""Модуль «Анализ места»: что съело дисковое пространство в каталоге.

Считает рекурсивные размеры всех объектов каталога активной панели
(dir_stats на каждого ребёнка, в фоновом потоке), таблица сортируется
по размеру, с долей от целого; двойной клик по каталогу — перейти внутрь
и пересчитать. Кнопка «Обновить» пересчитывает текущий каталог.
"""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from sphaera_commander.fsmodel import dir_stats, human_size
from sphaera_commander.i18n import tr
from sphaera_commander.plugin_api import SphaeraPlugin


class _SizeItem(QTableWidgetItem):
    """Человекочитаемый размер, сортируется по байтам."""

    def __init__(self, size: int):
        super().__init__(human_size(size))
        self.size = size

    def __lt__(self, other):
        try:
            return self.size < other.size
        except AttributeError:
            return super().__lt__(other)


class DirAnalyzeDialog(QDialog):
    rowReady = Signal(object)          # (имя, размер, файлы, подпапки, is_dir)
    scanDone = Signal(int, int, int)   # всего детей, файлов, байт
    progress = Signal(int, int)        # посчитано, всего

    def __init__(self, parent, root: str):
        super().__init__(parent)
        self.root = root
        self._closed = threading.Event()
        self._thread = None
        self.setWindowTitle(tr("Анализ места: {name}").format(
            name=os.path.basename(root) or root))
        self.resize(860, 560)

        titles = (tr("Объект"), tr("Тип"), tr("Размер"), tr("Доля"),
                  tr("Файлы"), tr("Подпапки"))
        self.table = QTableWidget(0, len(titles))
        self.table.setHorizontalHeaderLabels(titles)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        for col in range(1, len(titles)):
            self.table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeToContents)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().hide()
        self.table.itemDoubleClicked.connect(self._on_double)

        self.bar = QProgressBar()
        self.bar.hide()
        self.status = QLabel(tr("Чтение…"))
        btn_again = QPushButton(tr("Обновить"))
        btn_again.clicked.connect(self.rescan)
        btn_up = QPushButton(tr("На уровень выше"))
        btn_up.clicked.connect(self._go_up)
        close = QPushButton(tr("Закрыть"))
        close.clicked.connect(self.close)
        row = QHBoxLayout()
        row.addWidget(btn_again)
        row.addWidget(btn_up)
        row.addStretch(1)
        row.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.bar)
        layout.addWidget(self.status)
        layout.addLayout(row)

        self.rowReady.connect(self._on_row)
        self.scanDone.connect(self._on_done)
        self.progress.connect(self._on_progress)
        self.rescan()

    # -- сканирование --------------------------------------------------------

    def rescan(self) -> None:
        self.table.setRowCount(0)
        self.table.setSortingEnabled(False)
        self.status.setText(tr("Чтение…"))
        root = self.root
        closed = self._closed

        def worker():
            try:
                children = sorted(os.scandir(root),
                                  key=lambda e: e.name.lower())
            except OSError as exc:
                if not closed.is_set():
                    self.scanDone.emit(0, 0, 0)
                return
            entries = [e for e in children]
            total = len(entries)
            total_files = total_dirs = total_bytes = 0
            done = 0
            for entry in entries:
                if closed.is_set():
                    return
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    stats = dir_stats(entry.path)
                    size, files, dirs = stats.size, stats.files, stats.dirs
                else:
                    try:
                        size = entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        size = 0
                    files, dirs = 1, 0
                total_files += files
                total_dirs += dirs
                total_bytes += size
                if not closed.is_set():
                    self.rowReady.emit((entry.name, size, files, dirs, is_dir))
                done += 1
                if done % 5 == 0 or done == total:
                    if not closed.is_set():
                        self.progress.emit(done, total)
            if not closed.is_set():
                self.scanDone.emit(total, total_files, total_bytes)

        self._thread = threading.Thread(target=worker, daemon=True,
                                        name="diranalyze")
        self._thread.start()

    # -- отображение ---------------------------------------------------------

    def _on_row(self, payload) -> None:
        name, size, files, dirs, is_dir = payload
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(name))
        self.table.setItem(row, 1, QTableWidgetItem(
            tr("папка") if is_dir else tr("файл")))
        self.table.setItem(row, 2, _SizeItem(size))
        self.table.setItem(row, 4, QTableWidgetItem(str(files)))
        self.table.setItem(row, 5, QTableWidgetItem(str(dirs)))

    def _on_progress(self, done: int, total: int) -> None:
        self.bar.show()
        self.bar.setMaximum(max(1, total))
        self.bar.setValue(done)

    def _on_done(self, total: int, files: int, size: int) -> None:
        self.bar.hide()
        self.table.setSortingEnabled(True)
        # доли — после подсчёта целого
        grand = max(1, size)
        for row in range(self.table.rowCount()):
            size_item = self.table.item(row, 2)
            size_value = getattr(size_item, "size", 0)
            share = size_value * 100.0 / grand
            share_item = QTableWidgetItem(f"{share:.1f}%")
            share_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 3, share_item)
        self.table.sortItems(2, Qt.DescendingOrder)
        self.status.setText(
            tr("объектов: {n} • файл(ов): {f} • {size}").format(
                n=total, f=files, size=human_size(size)))

    def _on_double(self, item) -> None:
        row = item.row()
        name_item = self.table.item(row, 0)
        kind_item = self.table.item(row, 1)
        if name_item is None or kind_item is None:
            return
        if kind_item.text() != tr("папка"):
            return
        candidate = os.path.join(self.root, name_item.text())
        if os.path.isdir(candidate):
            self.root = candidate
            self.setWindowTitle(tr("Анализ места: {name}").format(
                name=os.path.basename(self.root) or self.root))
            self.rescan()

    def _go_up(self) -> None:
        parent = os.path.dirname(self.root.rstrip(os.sep))
        if parent and parent != self.root:
            self.root = parent
            self.setWindowTitle(tr("Анализ места: {name}").format(
                name=os.path.basename(self.root) or self.root))
            self.rescan()

    def closeEvent(self, event) -> None:
        self._closed.set()
        super().closeEvent(event)


class Plugin(SphaeraPlugin):
    id = "diranalyze"
    title = "Анализ места"

    def tools_actions(self):
        panel = self.app.active

        def open_dialog():
            root = panel.current_path()
            if panel.is_vfs:
                self.app._status(tr("Анализ места в архиве не считается"))
                return
            dlg = DirAnalyzeDialog(self.app, root)
            self.app._plugin_dialogs.append(dlg)
            dlg.show()

        return [(tr("Анализ места…"), open_dialog, None)]

    def context_actions(self, panel, entry):
        if entry is None or not entry.is_dir or panel.is_vfs:
            return []

        def open_on():
            dlg = DirAnalyzeDialog(self.app, entry.path)
            self.app._plugin_dialogs.append(dlg)
            dlg.show()

        return [(tr("Анализ места…"), open_on)]


def create(app):
    return Plugin(app)
