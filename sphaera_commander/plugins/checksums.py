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


def parse_sums_file(path: str) -> list[tuple[str, str]]:
    """Строки (хеш, имя) из файла сумм (*.md5/*.sha1/*.sha256)."""
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            digest, sep, name = line.partition(" ")
            if not sep:
                continue
            entries.append((digest.strip("*"), name.strip().lstrip("*")))
    return entries


def find_sums_for(directory: str) -> list[str]:
    return sorted(
        os.path.join(directory, n) for n in os.listdir(directory)
        if n.endswith((".md5", ".sha1", ".sha256", ".sha512")))


def verify_against_sums(files: list[str], directory: str) -> tuple[list, list, list]:
    """Проверить файлы по всем файлам сумм каталога.
    Возвращает (проверено-ok, не совпало [(имя, файл)], нет записи [имя])."""
    records: dict[str, tuple[str, str]] = {}  # имя → (хеш, алгоритм-файл)
    algo_by_ext = {".md5": "MD5", ".sha1": "SHA-1",
                   ".sha256": "SHA-256", ".sha512": "SHA-512"}
    for sums in find_sums_for(directory):
        algo = algo_by_ext.get(os.path.splitext(sums)[1].lower(), "MD5")
        for digest, name in parse_sums_file(sums):
            records.setdefault(os.path.basename(name), (digest, algo))
    ok, bad, unknown = [], [], []
    for path in files:
        name = os.path.basename(path)
        if name not in records:
            unknown.append(name)
            continue
        digest, algo = records[name]
        try:
            actual = _hash_file(path, algo)
        except OSError as exc:
            bad.append((name, str(exc)))
            continue
        if actual == digest:
            ok.append(name)
        else:
            bad.append((name, f"{digest[:12]}…"))
    return ok, bad, unknown


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
            return [(tr("Контрольные суммы…"), self._open),
                    (tr("Проверить по файлу сумм"), self._verify_selected)]
        return []

    def _verify_selected(self):
        panel = self.app.active
        files = [e.path for e in panel.selected_entries() if not e.is_dir]
        if not files:
            return
        directory = panel.current_path()
        if not find_sums_for(directory):
            self.app._status(tr("В папке нет файлов сумм (.md5/.sha256)"))
            return

        def worker():
            result = verify_against_sums(files, directory)
            self.app.gui_call.emit(lambda: self._verify_report(result))

        threading.Thread(target=worker, daemon=True,
                         name="sumverify").start()

    def _verify_report(self, result):
        ok, bad, unknown = result
        if bad:
            details = "; ".join(f"{name} ({why})" for name, why in bad[:3])
            self.app._status(tr("Не совпало: {n} — {details}").format(
                n=len(bad), details=details))
        elif unknown:
            self.app._status(tr("Нет записи в файлах сумм: {n}").format(
                n=len(unknown)))
        else:
            self.app._status(tr("Проверено: {n} — все суммы совпали").format(
                n=len(ok)))

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
