"""Модуль «Шифрование»: симметричное шифрование файлов через GnuPG
(gpg -c / gpg -d, passphrase не попадает в argv — через --passphrase-fd).
Файл получает расширение .gpg; расшифровка — по контекстному меню файла
.gpg. Если gpg не установлен — вежливая ошибка без падения."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from sphaera_commander.i18n import tr
from sphaera_commander.plugin_api import SphaeraPlugin


def gpg_bin() -> str | None:
    return shutil.which("gpg")


def _run_gpg(args: list[str], passphrase: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["gpg", "--batch", "--yes", "--passphrase-fd", "0"] + args,
        input=passphrase + "\n", capture_output=True, text=True, timeout=600)
    return proc.returncode, (proc.stderr or "").strip()


class CryptoDialog(QDialog):
    """Ввод парольной фразы и запуск шифрования/расшифровки в фоне."""

    def __init__(self, parent, files: list[str], decrypt: bool):
        super().__init__(parent)
        self.app = parent
        self.files = files
        self.decrypt = decrypt
        self.setWindowTitle(tr("Расшифровать") if decrypt
                            else tr("Зашифровать"))
        self.resize(520, 200)

        layout = QVBoxLayout(self)
        for path in files[:8]:
            layout.addWidget(QLabel("• " + os.path.basename(path)))
        if len(files) > 8:
            layout.addWidget(QLabel(
                tr("… и ещё {n}").format(n=len(files) - 8)))

        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Парольная фраза:")))
        self.pass1 = QLineEdit()
        self.pass1.setEchoMode(QLineEdit.Password)
        row.addWidget(self.pass1, 1)
        layout.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel(tr("Повторите:")))
        self.pass2 = QLineEdit()
        self.pass2.setEchoMode(QLineEdit.Password)
        row2.addWidget(self.pass2, 1)
        layout.addLayout(row2)
        self.del_original = QCheckBox(tr("Удалить исходный файл после операции"))
        layout.addWidget(self.del_original)

        self.status = QLabel("")
        btn = QPushButton(tr("Выполнить"))
        btn.setDefault(True)
        btn.clicked.connect(self._run)
        row3 = QHBoxLayout()
        row3.addStretch(1)
        row3.addWidget(btn)
        row3.addWidget(QPushButton(tr("Отмена"), clicked=self.reject))
        layout.addWidget(self.status)
        layout.addLayout(row3)

    def _targets(self) -> list[tuple[str, str]]:
        out = []
        for path in self.files:
            if self.decrypt:
                if path.endswith(".gpg"):
                    target = path[:-4]
                    if os.path.exists(target):
                        target += ".dec"
                    out.append((path, target))
            else:
                out.append((path, path + ".gpg"))
        return out

    def _run(self):
        if gpg_bin() is None:
            self.status.setText(tr("gpg не найден (sudo dnf install gnupg2)"))
            return
        passphrase = self.pass1.text()
        if not passphrase:
            self.status.setText(tr("Введите парольную фразу"))
            return
        if passphrase != self.pass2.text():
            self.status.setText(tr("Фразы не совпадают"))
            return
        targets = self._targets()
        if not targets:
            self.status.setText(tr("Нет подходящих файлов"))
            return
        wipe = self.del_original.isChecked()
        args_for = []
        for src, dst in targets:
            if self.decrypt:
                args_for.append((["--output", dst, "--decrypt", src], src, dst))
            else:
                args_for.append((["--symmetric", "--output", dst, src],
                                 src, dst))

        def worker():
            failures = []
            for args, src, dst in args_for:
                rc, err = _run_gpg(args, passphrase)
                if rc != 0:
                    if os.path.exists(dst):
                        os.remove(dst)
                    failures.append(f"{os.path.basename(src)}: "
                                    + (err.splitlines() or [""])[-1])
                elif wipe:
                    try:
                        os.remove(src)
                    except OSError as exc:
                        failures.append(f"{src}: {exc}")
            self.app.gui_call.emit(
                lambda: self._done(len(targets) - len(failures), failures))

        self.status.setText(tr("Работаю…"))
        threading.Thread(target=worker, daemon=True, name="crypto").start()

    def _done(self, ok_count: int, failures: list[str]):
        if failures:
            self.status.setText(tr("Готово: {ok}, с ошибками: {n}").format(
                ok=ok_count, n=len(failures)) + "\n" + "\n".join(failures[:3]))
        else:
            self.status.setText(tr("Готово: {n}").format(n=ok_count))
            QTimer_singleShot_close(self)


def QTimer_singleShot_close(dialog):
    from PySide6.QtCore import QTimer

    QTimer.singleShot(900, dialog.accept)


class Plugin(SphaeraPlugin):
    id = "encrypt"
    title = "Шифрование (gpg)"

    def tools_actions(self):
        return [(tr("Зашифровать/расшифровать…"), self._open, None)]

    def context_actions(self, panel, entry):
        files = [e for e in panel.selected_entries() if not e.is_dir]
        if not files:
            return []
        if all(f.name.endswith(".gpg") for f in files):
            return [(tr("Расшифровать…"),
                     lambda: self._open(decrypt=True))]
        if not any(f.name.endswith(".gpg") for f in files):
            return [(tr("Зашифровать…"),
                     lambda: self._open(decrypt=False))]
        return []

    def _open(self, decrypt: bool = False):
        panel = self.app.active
        files = [e.path for e in panel.selected_entries() if not e.is_dir]
        if not files:
            return
        if gpg_bin() is None:
            self.app._status(tr("gpg не найден (sudo dnf install gnupg2)"))
            return
        dlg = CryptoDialog(self.app, files, decrypt)
        dlg.exec()


def create(app):
    return Plugin(app)
