"""Модуль «Сравнить по содержимому»: два отмеченных файла — байтовое
сравнение, для текстов — unified diff в отдельном окне."""

from __future__ import annotations

import difflib
import os

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

from sphaera_commander.i18n import tr
from sphaera_commander.plugin_api import SphaeraPlugin

TEXT_LIMIT = 4 * 1024 * 1024  # дифф строим только для разумных текстов


def _is_text(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(8192)
    except OSError:
        return False
    return b"\x00" not in head


def _first_diff_offset(a: str, b: str, chunk: int = 1 << 20) -> int | None:
    size = min(os.path.getsize(a), os.path.getsize(b))
    pos = 0
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while pos < size:
            block_a = fa.read(chunk)
            block_b = fb.read(chunk)
            if block_a != block_b:
                for i, (x, y) in enumerate(zip(block_a, block_b)):
                    if x != y:
                        return pos + i
                return pos
            pos += len(block_a)
    return None


class DiffDialog(QDialog):
    def __init__(self, parent, left: str, right: str):
        super().__init__(parent)
        self.setWindowTitle(tr("Сравнение по содержимому"))
        self.resize(900, 620)
        layout = QVBoxLayout(self)
        self.lbl = QLabel(tr("{a}  ↔  {b}").format(
            a=os.path.basename(left), b=os.path.basename(right)))
        layout.addWidget(self.lbl)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        layout.addWidget(self.text, 1)

        if os.path.getsize(left) != os.path.getsize(right):
            self.text.setPlainText(tr("Размеры различаются: {sa} и {sb} байт").format(
                sa=os.path.getsize(left), sb=os.path.getsize(right)))
            return
        offset = _first_diff_offset(left, right)
        if offset is None:
            self.text.setPlainText(tr("Файлы идентичны"))
            return
        if _is_text(left) and _is_text(right) \
                and os.path.getsize(left) <= TEXT_LIMIT:
            with open(left, encoding="utf-8", errors="replace") as f:
                lines_a = f.readlines()
            with open(right, encoding="utf-8", errors="replace") as f:
                lines_b = f.readlines()
            diff = list(difflib.unified_diff(
                [line.rstrip("\n") for line in lines_a],
                [line.rstrip("\n") for line in lines_b],
                fromfile=os.path.basename(left),
                tofile=os.path.basename(right), lineterm=""))[:4000]
            self.text.setPlainText(
                tr("Первое отличие по смещению {offset}\n\n").format(offset=offset)
                + "\n".join(diff))
        else:
            self.text.setPlainText(tr("Файлы различаются с байта {offset}").format(
                offset=offset))


class Plugin(SphaeraPlugin):
    id = "compare_content"
    title = "Сравнить по содержимому"

    def context_actions(self, panel, entry):
        files = [e for e in panel.selected_entries() if not e.is_dir]
        if len(files) == 2:
            return [(tr("Сравнить по содержимому"),
                     lambda a=files[0], b=files[1]: self._compare(a, b))]
        return []

    def tools_actions(self):
        return [(tr("Сравнить по содержимому…"), self._open, None)]

    def _open(self):
        panel = self.app.active
        files = [e.path for e in panel.selected_entries() if not e.is_dir]
        if len(files) != 2:
            self.app._status(tr("Отметьте ровно два файла (Insert/Ctrl+клик)"))
            return
        self._compare(files[0], files[1])

    def _compare(self, a: str, b: str):
        dlg = DiffDialog(self.app, a, b)
        dlg.setModal(False)
        dlg.show()
        if not hasattr(self.app, "_plugin_dialogs"):
            self.app._plugin_dialogs = []
        self.app._plugin_dialogs.append(dlg)


def create(app):
    return Plugin(app)
