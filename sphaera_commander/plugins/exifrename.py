"""Модуль «Переименовать по EXIF-дате»: фото JPEG получают имя из даты
съёмки (шаблон с плейсхолдерами), без EXIF — по выбору — дата файла или
пропуск. Предпросмотр таблицей «было → станет», коллизии получают -2, -3…

Шаблон по умолчанию «{date}_{time}»; поддерживаются {date} (ГГГГ-ММ-ДД),
{time} (ЧЧММСС), {orig} (исходное имя), {n} (счётчик в группе одинаковых
дат). Выполняется синхронно как групповое переименование (os.rename).
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
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from sphaera_commander import exif
from sphaera_commander.fsmodel import human_size
from sphaera_commander.i18n import tr
from sphaera_commander.plugin_api import SphaeraPlugin

DEFAULT_PATTERN = "{date}_{time}"


def plan_renames(paths: list[str], pattern: str) -> list[tuple[str, str]]:
    """(было, станет) для каждого JPEG. Нет EXIF — дата файла; коллизии
    имён получают суффикс -2, -3…"""
    counters: dict[str, int] = {}
    used: set[str] = set()
    out: list[tuple[str, str]] = []
    for path in paths:
        name = os.path.basename(path)
        stamp = exif.exif_stamp(path, fmt="%Y-%m-%d|%H%M%S",
                                fallback_mtime=True)
        if stamp is None:
            out.append((path, path))
            continue
        date, _sep, tm = stamp.partition("|")
        new_base = pattern.format(date=date, time=tm,
                                  orig=os.path.splitext(name)[0])
        ext = os.path.splitext(name)[1].lower()
        base_key = new_base.lower()
        counters[base_key] = counters.get(base_key, 0) + 1
        n = counters[base_key]
        candidate = f"{new_base}{ext}" if n == 1 else f"{new_base}-{n}{ext}"
        while candidate.lower() in used:
            n += 1
            counters[base_key] = n
            candidate = f"{new_base}-{n}{ext}"
        used.add(candidate.lower())
        if candidate == name:
            out.append((path, path))
        else:
            out.append((path, candidate))
    return out


class ExifRenameDialog(QDialog):
    planReady = Signal(list)

    def __init__(self, parent, panel):
        super().__init__(parent)
        self.panel = panel
        self._paths = [e.path for e in panel.selected_entries()
                       if exif.is_jpeg(e.path) and not e.is_dir]
        self.setWindowTitle(tr("Переименовать по EXIF-дате"))
        self.resize(760, 520)

        pattern_row = QHBoxLayout()
        pattern_row.addWidget(QLabel(tr("Шаблон:")))
        self.pattern = QLineEdit(DEFAULT_PATTERN)
        self.pattern.setToolTip(
            tr("{date} — ГГГГ-ММ-ДД, {time} — ЧЧММСС, {orig} — исходное имя, {n} — счётчик"))
        pattern_row.addWidget(self.pattern, 1)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            (tr("Было"), tr("Станет"), tr("Размер")))
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().hide()

        self.status = QLabel(tr("файлов: {n}").format(n=len(self._paths)))
        btn_run = QPushButton(tr("Переименовать"))
        btn_run.clicked.connect(self._run)
        btn_close = QPushButton(tr("Закрыть"))
        btn_close.clicked.connect(self.close)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn_run)
        row.addWidget(btn_close)

        layout = QVBoxLayout(self)
        layout.addLayout(pattern_row)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)
        layout.addLayout(row)

        self.planReady.connect(self._show_plan)
        self.pattern.textChanged.connect(self._schedule)
        self._schedule()

    def _schedule(self) -> None:
        pattern = self.pattern.text().strip() or DEFAULT_PATTERN
        paths = list(self._paths)

        def worker():
            plan = plan_renames(paths, pattern)
            self.planReady.emit(plan)

        threading.Thread(target=worker, daemon=True,
                         name="exifrename-plan").start()

    def _show_plan(self, plan: list) -> None:
        self.table.setRowCount(0)
        changed = 0
        for path, new_name in plan:
            row = self.table.rowCount()
            self.table.insertRow(row)
            old = os.path.basename(path)
            self.table.setItem(row, 0, QTableWidgetItem(old))
            self.table.setItem(row, 1, QTableWidgetItem(
                new_name if new_name != old else "—"))
            self.table.setItem(row, 2, QTableWidgetItem(
                human_size(os.path.getsize(path))))
            if new_name != old:
                changed += 1
        self.status.setText(tr("переименуется: {n} из {total}").format(
            n=changed, total=len(plan)))

    def _run(self) -> None:
        errors = []
        done = 0
        for row in range(self.table.rowCount()):
            old_item = self.table.item(row, 0)
            new_item = self.table.item(row, 1)
            if old_item is None or new_item is None:
                continue
            new_name = new_item.text()
            if new_name == "—":
                continue
            old_path = os.path.join(self.panel.current_path(), old_item.text())
            new_path = os.path.join(self.panel.current_path(), new_name)
            if os.path.lexists(new_path):
                errors.append(f"{old_item.text()} → {new_name}: "
                              + tr("цель уже существует"))
                continue
            try:
                os.rename(old_path, new_path)
                done += 1
            except OSError as exc:
                errors.append(f"{old_item.text()}: {exc.strerror or exc}")
        if errors:
            from PySide6.QtWidgets import QMessageBox

            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle(tr("Переименование"))
            box.setText(tr("Переименовано: {done}, ошибок: {n}.").format(
                done=done, n=len(errors)))
            box.setDetailedText("\n".join(errors[:200]))
            box.exec()
        else:
            self.status.setText(tr("Переименовано: {n}").format(n=done))
        self._paths = [p for p in self._paths if os.path.exists(p)]
        self.panel.refresh()
        self._schedule()


class Plugin(SphaeraPlugin):
    id = "exifrename"
    title = "Переименование по EXIF"

    def context_actions(self, panel, entry):
        jpegs = [e for e in panel.selected_entries()
                 if exif.is_jpeg(e.path) and not e.is_dir]
        if not jpegs or panel.is_vfs:
            return []
        return [(tr("Переименовать по EXIF-дате…"),
                 lambda: ExifRenameDialog(self.app, panel).show())]

    def tools_actions(self):
        def open_dialog():
            panel = self.app.active
            if panel.is_vfs:
                self.app._status(tr("Переименование в архиве не поддерживается"))
                return
            dlg = ExifRenameDialog(self.app, panel)
            self.app._plugin_dialogs.append(dlg)
            dlg.show()

        return [(tr("Переименовать по EXIF-дате…"), open_dialog, None)]


def create(app):
    return Plugin(app)
