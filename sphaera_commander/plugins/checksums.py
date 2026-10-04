"""Модуль «Контрольные суммы»: MD5/SHA-1/SHA-256 для отмеченных файлов,
сохранение в файл сумм и проверка по нему."""

from __future__ import annotations

import hashlib
import os
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from sphaera_commander.i18n import tr
from sphaera_commander.plugin_api import SphaeraPlugin

ALGOS = ("MD5", "SHA-1", "SHA-256")
EXT = {"MD5": ".md5", "SHA-1": ".sha1", "SHA-256": ".sha256"}


def _hash_file(path: str, algo: str, chunk: int = 1 << 20) -> str:
    h = hashlib.new(algo.replace("-", "").lower())
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


class ChecksumsDialog(QDialog):
    """Вычисление/сохранение/проверка контрольных сумм."""

    lineReady = Signal(str, str)   # путь, хеш
    batchDone = Signal(int)        # код завершения

    def __init__(self, parent, files: list[str], panel_dir: str):
        super().__init__(parent)
        self.setWindowTitle(tr("Контрольные суммы"))
        self.resize(720, 420)
        self.files = files
        self.panel_dir = panel_dir
        self._cancel = threading.Event()

        top = QHBoxLayout()
        top.addWidget(QLabel(tr("Алгоритм:")))
        self.algo = QComboBox()
        self.algo.addItems(ALGOS)
        top.addWidget(self.algo)
        self.btn_calc = QPushButton(tr("Вычислить"))
        self.btn_calc.clicked.connect(self._calc)
        top.addWidget(self.btn_calc)
        self.btn_save = QPushButton(tr("Сохранить в файл сумм"))
        self.btn_save.clicked.connect(self._save)
        self.btn_save.setEnabled(False)
        top.addWidget(self.btn_save)
        self.btn_verify = QPushButton(tr("Проверить файл сумм…"))
        self.btn_verify.clicked.connect(self._verify)
        top.addWidget(self.btn_verify)
        top.addStretch(1)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels((tr("Файл"), tr("Сумма")))
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().hide()

        self.status = QLabel("")

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)

        self.lineReady.connect(self._on_line)
        self.batchDone.connect(self._on_done)

    # -- вычисление ---------------------------------------------------------

    def _calc(self) -> None:
        algo = self.algo.currentText()
        files = list(self.files)
        self.btn_calc.setEnabled(False)
        self.btn_save.setEnabled(False)
        self.table.setRowCount(0)
        self.status.setText(tr("Вычисление…"))

        def worker():
            for path in files:
                if self._cancel.is_set():
                    break
                try:
                    digest = _hash_file(path, algo)
                except OSError as exc:
                    digest = f"{type(exc).__name__}: {exc}"
                self.lineReady.emit(path, digest)
            self.batchDone.emit(0)

        threading.Thread(target=worker, daemon=True,
                         name="checksums").start()

    def _on_line(self, path: str, digest: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(
            os.path.relpath(path, self.panel_dir)
            if path.startswith(self.panel_dir) else path))
        item = QTableWidgetItem(digest)
        item.setData(Qt.UserRole, (path, digest))
        self.table.setItem(row, 1, item)

    def _on_done(self, _rc: int) -> None:
        self.btn_calc.setEnabled(True)
        self.btn_save.setEnabled(self.table.rowCount() > 0)
        self.status.setText(tr("Готово: {n} сумм").format(
            n=self.table.rowCount()))

    # -- сохранение ---------------------------------------------------------

    def _save(self) -> None:
        algo = self.algo.currentText()
        out = os.path.join(self.panel_dir,
                           os.path.basename(self.panel_dir)
                           or "checksums") + EXT[algo]
        rows = sorted((self.table.item(r, 0).text(),
                       self.table.item(r, 1).text())
                      for r in range(self.table.rowCount()))
        with open(out, "w", encoding="utf-8") as f:
            for name, digest in rows:
                f.write(f"{digest}  {name}\n")
        self.status.setText(tr("Сохранено: {path}").format(path=out))

    # -- проверка ------------------------------------------------------------

    def _verify(self) -> None:
        algo = self.algo.currentText()
        sums_file = None
        for name in sorted(os.listdir(self.panel_dir)):
            if name.endswith(EXT[algo]):
                sums_file = os.path.join(self.panel_dir, name)
                break
        if sums_file is None:
            self.status.setText(tr("В папке нет файла сумм {ext}").format(
                ext=EXT[algo]))
            return
        bad, missing, total = [], [], 0
        with open(sums_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                digest, _, name = line.partition("  ")
                total += 1
                path = os.path.join(self.panel_dir, name.strip())
                if not os.path.isfile(path):
                    missing.append(name)
                elif _hash_file(path, algo) != digest:
                    bad.append(name)
        if bad or missing:
            self.status.setText(tr("Проверка: {total}, не совпало: {bad}, нет файла: {miss}").format(
                total=total, bad=len(bad), miss=len(missing)))
        else:
            self.status.setText(tr("Все суммы совпали ({total})").format(
                total=total))


class Plugin(SphaeraPlugin):
    id = "checksums"
    title = "Контрольные суммы"

    def tools_actions(self):
        return [(tr("Контрольные суммы…"), self._open, None)]

    def context_actions(self, panel, entry):
        if panel.selected_entries():
            return [(tr("Контрольные суммы…"), self._open)]
        return []

    def _open(self):
        from PySide6.QtWidgets import QDialog

        panel = self.app.active
        files = [e.path for e in panel.selected_entries() if not e.is_dir]
        if not files:
            return
        dlg = ChecksumsDialog(self.app, files, panel.current_path())
        dlg.setModal(False)
        dlg.show()
        # окно живёт, пока открыто; ссылку держит родитель
        dlg.setAttribute(Qt.WA_DeleteOnClose, False)
        if not hasattr(self.app, "_plugin_dialogs"):
            self.app._plugin_dialogs = []
        self.app._plugin_dialogs.append(dlg)


def create(app):
    return Plugin(app)
