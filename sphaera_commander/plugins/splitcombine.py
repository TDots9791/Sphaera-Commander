"""Модуль «Разделить/собрать файл» в формате Total Commander:
части name.ext.001, name.ext.002, … + файл name.ext.crc
(1-я строка — имя файла, 2-я — размер, 3-я — CRC32 всего файла)."""

from __future__ import annotations

import os
import threading
import zlib

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from sphaera_commander.i18n import tr
from sphaera_commander.plugin_api import SphaeraPlugin

SIZES = (("1.44 МиБ (дискета)", 1440 * 1024),
         ("10 МиБ", 10 * 1024 * 1024),
         ("100 МиБ", 100 * 1024 * 1024),
         ("1 ГиБ", 1024 * 1024 * 1024))
CHUNK = 1024 * 1024


def crc32_of(path: str) -> int:
    crc = 0
    with open(path, "rb") as f:
        while True:
            block = f.read(CHUNK)
            if not block:
                break
            crc = zlib.crc32(block, crc)
    return crc & 0xFFFFFFFF


class SplitCombineDialog(QDialog):
    """Разделение/сборка одного файла; формат совместим с Total Commander."""

    progress = Signal(int)      # 0..100
    finished_ = Signal(bool, str)  # успех, сообщение

    def __init__(self, parent, panel_dir: str, files: list[str]):
        super().__init__(parent)
        self.setWindowTitle(tr("Разделить / собрать файл"))
        self.resize(560, 200)
        self.panel_dir = panel_dir
        self.files = files  # отмеченные файлы (для разделения — первый)
        self._cancel = threading.Event()

        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Часть:")))
        self.size = QComboBox()
        self.size.setEditable(True)
        for name, _bytes in SIZES:
            self.size.addItem(name)
        self.size.addItem(tr("своя, МиБ"))
        self.custom = QLineEdit("100")
        self.custom.setMaximumWidth(60)
        row.addWidget(self.size)
        row.addWidget(self.custom)
        self.btn_split = QPushButton(tr("Разделить"))
        self.btn_split.clicked.connect(self._split)
        row.addWidget(self.btn_split)
        row.addStretch(1)

        row2 = QHBoxLayout()
        self.btn_combine = QPushButton(tr("Собрать (по .crc или первой части)"))
        self.btn_combine.clicked.connect(self._combine)
        row2.addWidget(self.btn_combine)
        row2.addStretch(1)

        self.bar = QProgressBar()
        self.status = QLabel("")

        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addLayout(row2)
        layout.addWidget(self.bar)
        layout.addWidget(self.status)

        self.progress.connect(self.bar.setValue)
        self.finished_.connect(self._done)

    # -- вспомогательное -----------------------------------------------------

    def _part_bytes(self) -> int:
        text = self.size.currentText()
        for name, nbytes in SIZES:
            if text == name:
                return nbytes
        try:
            return max(1024, int(float(self.custom.text())) * 1024 * 1024)
        except ValueError:
            return SIZES[1][1]

    def _run(self, fn):
        self.btn_split.setEnabled(False)
        self.btn_combine.setEnabled(False)
        self._cancel.clear()

        def worker():
            try:
                message = fn()
                self.finished_.emit(True, message)
            except OSError as exc:
                self.finished_.emit(False, str(exc))

        threading.Thread(target=worker, daemon=True,
                         name="splitcombine").start()

    def _done(self, ok: bool, message: str) -> None:
        self.btn_split.setEnabled(True)
        self.btn_combine.setEnabled(True)
        self.status.setText(("✔ " if ok else "✖ ") + message)

    # -- разделение ----------------------------------------------------------

    def _split(self) -> None:
        if not self.files:
            self.status.setText(tr("Нет файла под курсором"))
            return
        src = self.files[0]
        part = self._part_bytes()
        base = src + "."

        def job():
            total = os.path.getsize(src)
            crc = 0
            index, written = 1, 0
            out = None
            with open(src, "rb") as f:
                while True:
                    if self._cancel.is_set():
                        raise OSError(tr("прервано"))
                    block = f.read(min(CHUNK, part - written))
                    if not block:
                        break
                    crc = zlib.crc32(block, crc)
                    if out is None:
                        out = open(f"{base}{index:03d}", "wb")
                    out.write(block)
                    written += len(block)
                    if written >= part:
                        out.close()
                        out = None
                        index += 1
                        written = 0
                    self.progress.emit(
                        int(f.tell() * 100 / total) if total else 100)
            if out is not None:
                out.close()
            crc_path = src + ".crc"
            # имя файла — в UTF-8 (у TC под Windows здесь ANSI; чтение
            # у нас толерантно к любому)
            with open(crc_path, "w", encoding="utf-8") as f:
                f.write(f"{os.path.basename(src)}\n{total}\n"
                        f"{crc & 0xFFFFFFFF}\n")
            return tr("Разделено на {n} частей + .crc").format(n=index)
        self._run(job)

    # -- сборка ----------------------------------------------------------------

    def _combine(self) -> None:
        crc_path = None
        first_part = None
        for name in sorted(os.listdir(self.panel_dir)):
            if name.endswith(".crc"):
                crc_path = os.path.join(self.panel_dir, name)
                break
        if crc_path is None:
            for name in sorted(os.listdir(self.panel_dir)):
                if len(name) > 4 and name[-4:-3] == "." and name[-3:].isdigit():
                    first_part = os.path.join(self.panel_dir, name)
                    crc_path = name[:-4] + ".crc"
                    break
        if crc_path is None:
            self.status.setText(tr("В папке нет частей или файла .crc"))
            return
        stem = crc_path[:-4]

        def job():
            orig_name, total, expected = None, None, None
            if os.path.isfile(crc_path):
                with open(crc_path, "rb") as f:
                    raw = f.read().splitlines()

                def _dec(b: bytes) -> str:
                    try:
                        return b.decode("utf-8")
                    except UnicodeDecodeError:
                        return b.decode("cp1251", errors="replace")

                lines = [_dec(line) for line in raw]
                if len(lines) >= 3:
                    orig_name, total, expected = (lines[0], int(lines[1]),
                                                  int(lines[2]))
            parts = []
            index = 1
            while True:
                p = f"{stem}.{index:03d}"
                if not os.path.isfile(p):
                    break
                parts.append(p)
                index += 1
            if not parts:
                raise OSError(tr("части не найдены рядом с {name}").format(
                    name=os.path.basename(crc_path)))
            out_name = orig_name or os.path.basename(stem)
            out = os.path.join(self.panel_dir, out_name)
            crc = 0
            size = 0
            with open(out, "wb") as f:
                done = 0
                for p in parts:
                    with open(p, "rb") as src:
                        while True:
                            if self._cancel.is_set():
                                raise OSError(tr("прервано"))
                            block = src.read(CHUNK)
                            if not block:
                                break
                            f.write(block)
                            crc = zlib.crc32(block, crc)
                            size += len(block)
                    done += os.path.getsize(p)
                    self.progress.emit(50 * done // max(1, sum(
                        os.path.getsize(x) for x in parts)))
            if expected is not None and (crc & 0xFFFFFFFF) != expected:
                raise OSError(tr("CRC32 не совпал: файл собран, но повреждён"))
            if total is not None and size != total:
                raise OSError(tr("размер не совпал: {have} из {want}").format(
                    have=size, want=total))
            return tr("Собрано: {name} ({parts} ч.) — CRC32 OK").format(
                name=out_name, parts=len(parts))
        self._run(job)


class Plugin(SphaeraPlugin):
    id = "splitcombine"
    title = "Разделить/собрать файл"

    def tools_actions(self):
        return [(tr("Разделить / собрать файл…"), self._open, None)]

    def context_actions(self, panel, entry):
        files = [e for e in panel.selected_entries() if not e.is_dir]
        if files:
            return [(tr("Разделить / собрать файл…"), self._open)]
        return []

    def _open(self):
        panel = self.app.active
        files = [e.path for e in panel.selected_entries() if not e.is_dir]
        dlg = SplitCombineDialog(self.app, panel.current_path(), files)
        dlg.setModal(False)
        dlg.show()
        if not hasattr(self.app, "_plugin_dialogs"):
            self.app._plugin_dialogs = []
        self.app._plugin_dialogs.append(dlg)


def create(app):
    return Plugin(app)
