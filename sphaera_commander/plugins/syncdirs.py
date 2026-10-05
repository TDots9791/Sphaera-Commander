"""Модуль «Синхронизация каталогов» — как в TC/DC. Не-рекурсивное сравнение
двух обычных каталогов, таблица направлений (→/←/=/≠) с размерами и временем,
выполнение отмеченного копирования через ops-движок приложения (очередь
операций, диалог перезаписи). Каталог, существующий с обеих сторон, направления
не получает: его mtime меняется при копировании. Основа сравнения —
fsmodel.scan_directory + fsmodel.compare_name_sets (размер + mtime)."""

from __future__ import annotations

import os
import threading
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from sphaera_commander.fsmodel import (
    compare_name_sets,
    entry_for,
    human_size,
    scan_directory,
)
from sphaera_commander.i18n import tr
from sphaera_commander.plugin_api import SphaeraPlugin

ARROW_LR = "→"   # только слева / левый свежее — копировать вправо
ARROW_RL = "←"   # только справа / правый свежее — копировать влево
ARROW_EQ = "="   # одинаково (размер и время)
ARROW_CONFLICT = "≠"  # оба существуют и расходятся — направления нет


def _fmt_time(mtime: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))


def collect_rows(left: str, right: str, show_hidden: bool = False) -> list:
    """Сравнить два каталога (один уровень, как панели).

    Возвращает строки (имя, направление, entry_l | None, entry_r | None).
    Различие — по compare_name_sets: размер или целая секунда mtime.
    """
    lmap = {e.name: e for e in scan_directory(left, show_hidden)
            if e.name != ".."}
    rmap = {e.name: e for e in scan_directory(right, show_hidden)
            if e.name != ".."}
    ldiff, _rdiff = compare_name_sets(lmap, rmap)
    rows = []
    for name in sorted(set(lmap) | set(rmap)):
        le = lmap.get(name)
        re_ = rmap.get(name)
        if re_ is None:
            direction = ARROW_LR
        elif le is None:
            direction = ARROW_RL
        elif name not in ldiff:
            direction = ARROW_EQ
        elif le.is_dir or re_.is_dir:
            # Содержимое каталогов не сравнивается на этом уровне, а mtime
            # каталога меняется от любого копирования — направления нет.
            direction = ARROW_CONFLICT
        elif le.mtime > re_.mtime:
            direction = ARROW_LR
        elif re_.mtime > le.mtime:
            direction = ARROW_RL
        else:
            # Размер различается при равном времени — автоматически не решить.
            direction = ARROW_CONFLICT
        rows.append((name, direction, le, re_))
    return rows


class SyncDirsDialog(QDialog):
    rowsReady = Signal(list)  # [(имя, направление, entry_l, entry_r)]

    def __init__(self, parent, left: str, right: str, show_hidden: bool = False):
        super().__init__(parent)
        self.app = parent
        self.left = left
        self.right = right
        self.show_hidden = show_hidden
        self.setWindowTitle(tr("Синхронизация каталогов: {a} ⇄ {b}").format(
            a=os.path.basename(left) or left,
            b=os.path.basename(right) or right))
        self.resize(920, 560)

        titles = (tr("Имя"), tr("Направление"),
                  tr("Размер (L)"), tr("Изменён (L)"),
                  tr("Размер (R)"), tr("Изменён (R)"))
        self.table = QTableWidget(0, len(titles))
        self.table.setHorizontalHeaderLabels(titles)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for col in (1, 2, 3, 4, 5):
            self.table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().hide()

        self.status = QLabel(tr("Сканирование…"))
        row = QHBoxLayout()
        btn_sync = QPushButton(tr("Синхронизировать отмеченные"))
        btn_sync.clicked.connect(self._sync_selected)
        close = QPushButton(tr("Закрыть"))
        close.clicked.connect(self.close)
        row.addWidget(btn_sync)
        row.addStretch(1)
        row.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)
        layout.addLayout(row)

        self.rowsReady.connect(self._apply_rows)
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._scan_async, daemon=True,
                                        name="syncdirs")
        self._thread.start()

    def closeEvent(self, event):
        self._closed.set()
        super().closeEvent(event)

    def _scan_async(self) -> None:
        rows = collect_rows(self.left, self.right, self.show_hidden)
        if not self._closed.is_set():
            self.rowsReady.emit(rows)

    def _apply_rows(self, rows: list) -> None:
        counts = {ARROW_LR: 0, ARROW_RL: 0, ARROW_EQ: 0, ARROW_CONFLICT: 0}
        for name, direction, le, re_ in rows:
            counts[direction] += 1
            r = self.table.rowCount()
            self.table.insertRow(r)
            item = QTableWidgetItem(name)
            self.table.setItem(r, 0, item)
            arrow = QTableWidgetItem(direction)
            arrow.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 1, arrow)
            self.table.setItem(r, 2, QTableWidgetItem(
                "" if le is None or le.is_dir else human_size(le.size)))
            self.table.setItem(r, 3, QTableWidgetItem(
                "" if le is None else _fmt_time(le.mtime)))
            self.table.setItem(r, 4, QTableWidgetItem(
                "" if re_ is None or re_.is_dir else human_size(re_.size)))
            self.table.setItem(r, 5, QTableWidgetItem(
                "" if re_ is None else _fmt_time(re_.mtime)))
            actionable = direction in (ARROW_LR, ARROW_RL)
            arrow.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
                           | Qt.ItemIsSelectable)
            arrow.setCheckState(Qt.Checked if actionable else Qt.Unchecked)
        self.status.setText(tr("→ {a}   ← {b}   = {c}   ≠ {d}").format(
            a=counts[ARROW_LR], b=counts[ARROW_RL],
            c=counts[ARROW_EQ], d=counts[ARROW_CONFLICT]))

    def _checked_rows(self) -> list:
        """Отмеченные строки: [(имя, направление)]."""
        out = []
        for r in range(self.table.rowCount()):
            arrow = self.table.item(r, 1)
            if arrow is not None and arrow.checkState() == Qt.Checked:
                out.append((self.table.item(r, 0).text(), arrow.text()))
        return out

    def _sync_selected(self) -> None:
        checked = self._checked_rows()
        if not checked:
            self.status.setText(tr("Нет отмеченных строк для синхронизации"))
            return
        plan = {ARROW_LR: [], ARROW_RL: []}
        for name, direction in checked:
            source = self.left if direction == ARROW_LR else self.right
            entry = entry_for(os.path.join(source, name))
            if entry is not None:
                plan[direction].append(entry)
        try:
            from sphaera_commander.ops import KIND_COPY, plan_copy_move

            jobs = []
            for direction, entries in plan.items():
                if not entries:
                    continue
                dest = self.right if direction == ARROW_LR else self.left
                title = tr("Синхронизация {n} объект(ов) {arrow} {dir}").format(
                    n=len(entries), arrow=direction,
                    dir=os.path.basename(dest) or dest)
                jobs.append((title, plan_copy_move(entries, dest, False), entries))
        except OSError as exc:
            QMessageBox.critical(self, tr("Ошибка"),
                                 tr("Не удалось построить план:\n{exc}").format(exc=exc))
            return
        if not jobs:
            self.status.setText(tr("Нет объектов для обработки"))
            return
        after = self.app.refresh_all
        for title, copy_plan, entries in reversed(jobs):
            # Очередь LIFO: добавленные первыми выполняются первыми, поэтому
            # after (обновление панелей) вешаем на самую первую операцию.
            self.app._enqueue_op(title, self.app._fs_fn(KIND_COPY, copy_plan, entries),
                                 after=after)
            after = None
        self.status.setText(tr("Поставлено в очередь: {n}").format(n=len(jobs)))


class Plugin(SphaeraPlugin):
    id = "syncdirs"
    title = "Синхронизация каталогов"

    def tools_actions(self):
        return [(tr("Синхронизация каталогов…"), self._open, None)]

    def _open(self):
        left, right = self.app.active, self.app._other()
        if left.is_vfs or right.is_vfs:
            self.app._status(tr("Синхронизация работает для обычных каталогов"))
            return
        if left.current_path() == right.current_path():
            self.app._status(tr("Панели указывают на один и тот же каталог"))
            return
        dlg = SyncDirsDialog(
            self.app, left.current_path(), right.current_path(),
            show_hidden=left.model.show_hidden or right.model.show_hidden)
        dlg.setModal(False)
        dlg.show()
        if not hasattr(self.app, "_plugin_dialogs"):
            self.app._plugin_dialogs = []
        self.app._plugin_dialogs.append(dlg)


def create(app):
    return Plugin(app)
