"""Модуль «Подключить сервер»: создание rclone-remote для FTP/SFTP/WebDAV
прямо из интерфейса (пароль скрывается через `rclone obscure`).
После создания сервер появляется в меню дисков (Alt+F1/F2, Ctrl+F1/F2)
и его можно смонтировать как диск."""

from __future__ import annotations

import subprocess
import threading

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from sphaera_commander.i18n import tr
from sphaera_commander.plugin_api import SphaeraPlugin

TYPES = (
    ("sftp", "SFTP (SSH)", 22),
    ("ftp", "FTP", 21),
    ("dav", "WebDAV", None),
)


def build_config_args(name: str, rtype: str, host: str, port: str,
                      user: str, obscured: str,
                      skip_host_key: bool) -> list[str]:
    """Аргументы rclone config create (пароль уже скрыт rclone obscure)."""
    args = ["config", "create", name, rtype,
            "host", host, "user", user, "pass", obscured]
    if port:
        args += ["port", port]
    if rtype == "sftp" and skip_host_key:
        args += ["host_key_override", "sphaera-trusted",
                 "ask_password", "false"]
    return args


def obscure(rclone: str, password: str) -> str | None:
    try:
        proc = subprocess.run([rclone, "obscure", password],
                              capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


class RemoteDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.app = parent
        self.setWindowTitle(tr("Подключить сервер"))
        self.resize(520, 280)

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Тип:")))
        self.type = QComboBox()
        for _rtype, title, _port in TYPES:
            self.type.addItem(title)
        self.type.currentIndexChanged.connect(self._type_changed)
        row.addWidget(self.type, 1)
        layout.addLayout(row)

        grid = QHBoxLayout()
        col1 = QVBoxLayout()
        col1.addWidget(QLabel(tr("Адрес (хост):")))
        self.host = QLineEdit()
        col1.addWidget(self.host)
        col1.addWidget(QLabel(tr("Пользователь:")))
        self.user = QLineEdit()
        col1.addWidget(self.user)
        grid.addLayout(col1, 1)

        col2 = QVBoxLayout()
        col2.addWidget(QLabel(tr("Порт (пусто — по умолчанию):")))
        self.port = QLineEdit()
        col2.addWidget(self.port)
        col2.addWidget(QLabel(tr("Пароль:")))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        col2.addWidget(self.password)
        grid.addLayout(col2, 1)
        layout.addLayout(grid)

        self.skip_key = QCheckBox(tr("SFTP: не проверять ключ хоста (домашняя сеть)"))
        layout.addWidget(self.skip_key)

        self.status = QLabel("")
        row2 = QHBoxLayout()
        btn_create = QPushButton(tr("Создать и подключить как диск"))
        btn_create.setDefault(True)
        btn_create.clicked.connect(self._create)
        close = QPushButton(tr("Закрыть"))
        close.clicked.connect(self.reject)
        row2.addStretch(1)
        row2.addWidget(btn_create)
        row2.addWidget(close)
        layout.addWidget(self.status)
        layout.addLayout(row2)
        self._type_changed(0)

    def _type_changed(self, index: int):
        default_port = TYPES[index][2]
        self.port.setText(str(default_port) if default_port else "")
        self.skip_key.setVisible(TYPES[index][0] == "sftp")

    def _create(self):
        from sphaera_commander import cloudsync

        rclone = cloudsync.rclone_bin()
        if rclone is None:
            self.status.setText(tr("rclone не найден"))
            return
        rtype, title, _default = TYPES[self.type.currentIndex()]
        host = self.host.text().strip()
        user = self.user.text().strip() or "anonymous"
        password = self.password.text()
        port = self.port.text().strip()
        if not host:
            self.status.setText(tr("Введите адрес сервера"))
            return
        name = f"{rtype}-{host.split('.')[0]}"
        obscured = obscure(rclone, password or "")
        if obscured is None:
            self.status.setText(tr("rclone не смог скрыть пароль"))
            return
        args = [rclone] + build_config_args(
            name, rtype, host, port, user, obscured,
            self.skip_key.isChecked())

        def worker():
            try:
                proc = subprocess.run(args, capture_output=True,
                                      text=True, timeout=60)
                rc = proc.returncode
            except (OSError, subprocess.TimeoutExpired):
                rc = -1
            self.app.gui_call.emit(lambda: self._created(rc, name, title))

        self.status.setText(tr("Создаю подключение…"))
        threading.Thread(target=worker, daemon=True,
                         name="remoteadd").start()

    def _created(self, rc: int, name: str, title: str):
        if rc != 0:
            self.status.setText(tr("Не удалось создать remote {name}").format(
                name=name))
            return
        self.status.setText(tr("Сервер {name} добавлен — монтирую как диск…").format(
            name=name))
        self.app._mount_cloud(name + ":", self.app.active)
        QTimer_close_later(self)


def QTimer_close_later(dialog):
    from PySide6.QtCore import QTimer

    QTimer.singleShot(1500, dialog.accept)


class Plugin(SphaeraPlugin):
    id = "remoteadd"
    title = "Подключение серверов"

    def tools_actions(self):
        return [(tr("Подключить сервер (FTP/SFTP/WebDAV)…"), self._open, None)]

    def _open(self):
        RemoteDialog(self.app).exec()


def create(app):
    return Plugin(app)
