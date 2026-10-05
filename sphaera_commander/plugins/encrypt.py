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


AGE_KEY_FILE = os.path.expanduser(
    "~/.config/sphaera-commander/age.key")


def age_bin() -> str | None:
    return shutil.which("age")


def age_keygen_bin() -> str | None:
    return shutil.which("age-keygen")


def age_public_key() -> str | None:
    """Публичный ключ из файла идентичности (строка age1...)."""
    try:
        with open(AGE_KEY_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("age1"):
                    return line
    except OSError:
        pass
    return None


def create_age_key() -> str | None:
    """age-keygen → файл идентичности; вернуть публичный ключ или None."""
    keygen = age_keygen_bin()
    if keygen is None:
        return None
    os.makedirs(os.path.dirname(AGE_KEY_FILE), exist_ok=True)
    proc = subprocess.run([keygen, "-o", AGE_KEY_FILE],
                          capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return None
    return age_public_key()


def _run_age(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(["age"] + args, capture_output=True,
                          text=True, timeout=600)
    return proc.returncode, (proc.stderr or "").strip()


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


class AgeDialog(QDialog):
    """Шифрование/расшифровка по ключу age (без парольных фраз)."""

    def __init__(self, parent, files: list[str], decrypt: bool):
        super().__init__(parent)
        self.app = parent
        self.files = files
        self.decrypt = decrypt
        self.setWindowTitle("age")
        self.resize(520, 160)
        pubkey = age_public_key()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr(
            "Ключ: {key}").format(key=pubkey or tr("не создан"))))
        self.status = QLabel("")
        btn = QPushButton(tr("Выполнить"))
        btn.clicked.connect(self._run)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn)
        row.addWidget(QPushButton(tr("Отмена"), clicked=self.reject))
        layout.addWidget(self.status)
        layout.addLayout(row)

    def _targets(self) -> list[tuple[str, str]]:
        out = []
        for path in self.files:
            if self.decrypt:
                if path.endswith(".age"):
                    target = path[:-4]
                    if os.path.exists(target):
                        target += ".dec"
                    out.append((path, target))
            else:
                out.append((path, path + ".age"))
        return out

    def _run(self):
        if age_bin() is None:
            self.status.setText(tr("age не установлен (sudo dnf install age)"))
            return
        pubkey = age_public_key()
        if pubkey is None:
            self.status.setText(tr("Ключ age не создан (age-keygen)"))
            return
        identity = AGE_KEY_FILE
        targets = self._targets()
        if not targets:
            self.status.setText(tr("Нет подходящих файлов"))
            return
        args_for = []
        for src, dst in targets:
            if self.decrypt:
                args_for.append((["-d", "-i", identity, "-o", dst, src], src, dst))
            else:
                args_for.append((["-r", pubkey, "-o", dst, src], src, dst))

        def worker():
            failures = []
            for args, src, dst in args_for:
                rc, err = _run_age(args)
                if rc != 0:
                    if os.path.exists(dst):
                        os.remove(dst)
                    failures.append(f"{os.path.basename(src)}: "
                                    + (err.splitlines() or [""])[-1])
            self.app.gui_call.emit(
                lambda: self.status.setText(
                    tr("Готово: {ok}, с ошибками: {n}").format(
                        ok=len(targets) - len(failures), n=len(failures))))

        self.status.setText(tr("Работаю…"))
        threading.Thread(target=worker, daemon=True, name="age").start()


class Plugin(SphaeraPlugin):
    id = "encrypt"
    title = "Шифрование (gpg)"

    def tools_actions(self):
        actions = [(tr("Зашифровать/расшифровать…"), self._open, None)]
        if age_bin() is not None:
            actions.append((tr("Создать ключ age"), self._age_keygen, None))
        return actions

    def _age_keygen(self):
        pubkey = create_age_key()
        if pubkey is None:
            self.app._status(tr("age не установлен (sudo dnf install age)"))
            return
        self.app._status(tr("Ключ age создан: {key}").format(key=pubkey))

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
        if decrypt and age_bin() is not None \
                and all(p.endswith(".age") for p in files):
            dlg = AgeDialog(self.app, files, decrypt=True)
            dlg.exec()
            return
        if gpg_bin() is None:
            self.app._status(tr("gpg не найден (sudo dnf install gnupg2)"))
            return
        dlg = CryptoDialog(self.app, files, decrypt)
        dlg.exec()


def create(app):
    return Plugin(app)
