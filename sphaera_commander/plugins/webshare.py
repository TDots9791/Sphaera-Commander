"""Модуль «Раздать папку по сети»: HTTP-сервер (только чтение) на базе
stdlib — каталог становится доступным по ссылке с любого устройства в
локальной сети. WebDAV-запись без внешних зависимостей не поддерживается."""

from __future__ import annotations

import functools
import http.server
import os
import socket
import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from sphaera_commander.i18n import tr
from sphaera_commander.plugin_api import SphaeraPlugin


def local_urls(port: int) -> list[str]:
    """URL-ы, по которым папка доступна из сети."""
    urls = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        urls.append(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    try:
        urls.append(socket.gethostbyname(socket.gethostname()))
    except OSError:
        pass
    seen, out = set(), []
    for ip in urls:
        if ip and ip not in seen:
            seen.add(ip)
            out.append(f"http://{ip}:{port}/")
    return out


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):  # не мусорим в stderr приложения
        pass


class ShareDialog(QDialog):
    def __init__(self, parent, directory: str):
        super().__init__(parent)
        self.app = parent
        self.directory = directory
        self.server = None
        self.thread = None
        self.port = 8000
        self.setWindowTitle(tr("Раздача папки по сети"))
        self.resize(560, 180)

        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Порт:")))
        self.port_edit = QLineEdit("8000")
        self.port_edit.setMaximumWidth(80)
        row.addWidget(self.port_edit)
        self.btn_toggle = QPushButton(tr("Раздавать"))
        self.btn_toggle.clicked.connect(self._toggle)
        row.addWidget(self.btn_toggle)
        row.addStretch(1)

        self.lbl_dir = QLabel(tr("Папка: {dir}").format(dir=directory))
        self.lbl_urls = QLabel("")
        self.lbl_urls.setWordWrap(True)
        self.lbl_urls.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status = QLabel(tr("Не запущено"))

        layout = QVBoxLayout(self)
        layout.addWidget(self.lbl_dir)
        layout.addLayout(row)
        layout.addWidget(self.lbl_urls)
        layout.addWidget(self.status)
        layout.addStretch(1)

    def _toggle(self):
        if self.server is not None:
            self._stop()
            return
        try:
            self.port = int(self.port_edit.text())
            if not 1 <= self.port <= 65535:
                raise ValueError
        except ValueError:
            self.status.setText(tr("Порт — число от 1 до 65535"))
            return
        handler = functools.partial(_Handler, directory=self.directory)
        try:
            self.server = http.server.ThreadingHTTPServer(
                ("", self.port), handler)
        except OSError as exc:
            self.server = None
            self.status.setText(tr("Не удалось занять порт: {err}").format(err=exc))
            return
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True, name="webshare")
        self.thread.start()
        self.btn_toggle.setText(tr("Остановить"))
        self.port_edit.setEnabled(False)
        urls = "\n".join(local_urls(self.port))
        self.lbl_urls.setText(urls or tr("локально: http://localhost:{port}/").format(
            port=self.port))
        self.status.setText(tr("Раздаётся (только чтение). Не закрывайте окно."))

    def _stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        self.btn_toggle.setText(tr("Раздавать"))
        self.port_edit.setEnabled(True)
        self.lbl_urls.setText("")
        self.status.setText(tr("Не запущено"))

    def closeEvent(self, event):
        self._stop()
        event.accept()


class Plugin(SphaeraPlugin):
    id = "webshare"
    title = "Раздача папки по сети"

    def __init__(self, app):
        super().__init__(app)
        self._dialog = None

    def tools_actions(self):
        return [(tr("Раздать папку по сети…"), self._open, None)]

    def context_actions(self, panel, entry):
        if entry is not None and entry.is_dir:
            return [(tr("Раздать папку по сети…"),
                     lambda d=entry.path: self._open(d))]
        return []

    def _open(self, directory=None):
        panel = self.app.active
        if directory is None:
            cur = panel.current_entry()
            directory = (cur.path if cur is not None and cur.is_dir
                         else panel.current_path())
        if self._dialog is None or not self._dialog.isVisible():
            self._dialog = ShareDialog(self.app, directory)
            self._dialog.show()
        else:
            self._dialog.raise_()

    def on_shutdown(self):
        if self._dialog is not None:
            self._dialog._stop()


def create(app):
    return Plugin(app)
