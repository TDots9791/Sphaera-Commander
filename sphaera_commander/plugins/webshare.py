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


def _dav_safe_path(handler) -> str | None:
    """Путь из URL внутри общего каталога; None — выход за пределы."""
    import urllib.parse

    rel = urllib.parse.unquote(handler.path.split("?")[0])
    rel = rel.lstrip("/")
    root = os.path.realpath(handler.directory)
    full = os.path.realpath(os.path.join(root, rel))
    if full != root and not full.startswith(root + os.sep):
        return None
    return full


def _dav_destination(handler) -> str | None:
    import urllib.parse

    dest = handler.headers.get("Destination", "")
    if not dest:
        return None
    path = urllib.parse.urlparse(dest).path
    rel = urllib.parse.unquote(path).lstrip("/")
    root = os.path.realpath(handler.directory)
    full = os.path.realpath(os.path.join(root, rel))
    if full != root and not full.startswith(root + os.sep):
        return None
    return full


class DAVHandler(_Handler):
    """Минимальный WebDAV (Class 1): PROPFIND/MKCOL/PUT/DELETE/MOVE/COPY +
    фиктивный LOCK — достаточно для gvfs/обычных клиентов в домашней сети.
    Запись разрешена только внутри раздаваемого каталога."""

    server_version = "SphaeraDAV/1.0"

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("DAV", "1,2")
        self.send_header("MS-Author-Via", "DAV")
        self.send_header("Allow",
                         "OPTIONS, GET, HEAD, PUT, DELETE, PROPFIND, "
                         "PROPPATCH, MKCOL, MOVE, COPY, LOCK, UNLOCK")
        self.end_headers()

    def _xml(self, code: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", 'application/xml; charset="utf-8"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _propfind_entry(self, full: str, url: str, depth_part: str) -> str:
        import time as time_m

        is_dir = os.path.isdir(full)
        modified = time_m.strftime(
            "%a, %d %b %Y %H:%M:%S GMT", time_m.gmtime(os.path.getmtime(full)))
        size = "" if is_dir else (
            f"<D:getcontentlength>{os.path.getsize(full)}</D:getcontentlength>")
        resType = "<D:collection/>" if is_dir else ""
        return (f"<D:response><D:href>{url}</D:href>"
                f"<D:propstat><D:prop>{resType}"
                f"<D:displayname>{os.path.basename(full) or '/'}</D:displayname>"
                f"<D:getlastmodified>{modified}</D:getlastmodified>{size}"
                f"</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>"
                f"</D:response>{depth_part}")

    def do_PROPFIND(self):
        full = _dav_safe_path(self)
        if full is None or not os.path.exists(full):
            self.send_response(404)
            self.end_headers()
            return
        depth = self.headers.get("Depth", "1")
        body_parts = [self._propfind_entry(full, self.path, "")]
        if os.path.isdir(full) and depth != "0":
            for name in sorted(os.listdir(full)):
                child = os.path.join(full, name)
                url = (self.path.rstrip("/") + "/" + name)
                body_parts.append(self._propfind_entry(child, url, ""))
        body = ('<?xml version="1.0" encoding="utf-8"?>'
                '<D:multistatus xmlns:D="DAV:">'
                + "".join(body_parts) + "</D:multistatus>")
        self._xml(207, body)

    def do_PROPPATCH(self):
        self._xml(207, '<?xml version="1.0"?><D:multistatus xmlns:D="DAV:">'
                       "<D:response><D:status>HTTP/1.1 200 OK</D:status>"
                       "</D:response></D:multistatus>")

    def do_MKCOL(self):
        full = _dav_safe_path(self)
        if full is None:
            self.send_response(403)
            self.end_headers()
            return
        try:
            os.mkdir(full)
        except FileExistsError:
            self.send_response(405)
            self.end_headers()
            return
        except OSError:
            self.send_response(409)
            self.end_headers()
            return
        self.send_response(201)
        self.end_headers()

    def do_PUT(self):
        full = _dav_safe_path(self)
        if full is None:
            self.send_response(403)
            self.end_headers()
            return
        if os.path.isdir(full):
            self.send_response(409)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            with open(full, "wb") as f:
                remaining = length
                while remaining > 0:
                    block = self.rfile.read(min(1 << 20, remaining))
                    if not block:
                        break
                    f.write(block)
                    remaining -= len(block)
        except OSError:
            self.send_response(500)
            self.end_headers()
            return
        self.send_response(201)
        self.end_headers()

    def do_DELETE(self):
        import shutil as shutil_m

        full = _dav_safe_path(self)
        if full is None:
            self.send_response(403)
            self.end_headers()
            return
        if os.path.isdir(full):
            shutil_m.rmtree(full, ignore_errors=True)
        elif os.path.isfile(full):
            os.remove(full)
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(204)
        self.end_headers()

    def _move_copy(self, move: bool):
        import shutil as shutil_m

        src = _dav_safe_path(self)
        dst = _dav_destination(self)
        if src is None or dst is None or not os.path.exists(src):
            self.send_response(403 if src is None or dst is None else 404)
            self.end_headers()
            return
        if os.path.isdir(dst):
            dst = os.path.join(dst, os.path.basename(src))
        if move:
            shutil_m.move(src, dst)
        else:
            if os.path.isdir(src):
                shutil_m.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil_m.copy2(src, dst)
        self.send_response(201)
        self.end_headers()

    def do_MOVE(self):
        self._move_copy(move=True)

    def do_COPY(self):
        self._move_copy(move=False)

    def do_LOCK(self):
        token = "opaquelocktoken:sphaera-" + self.path.replace("/", "-")
        self._xml(200, '<?xml version="1.0" encoding="utf-8"?>'
                       '<D:prop xmlns:D="DAV:"><D:lockdiscovery>'
                       "<D:activelock><D:locktoken><D:href>"
                       + token + "</D:href></D:locktoken></D:activelock>"
                       "</D:lockdiscovery></D:prop>")

    def do_UNLOCK(self):
        self.send_response(204)
        self.end_headers()


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
        self.writable = QCheckBox(tr("Разрешить запись (WebDAV)"))
        self.writable.setChecked(False)

        self.lbl_dir = QLabel(tr("Папка: {dir}").format(dir=directory))
        self.lbl_urls = QLabel("")
        self.lbl_urls.setWordWrap(True)
        self.lbl_urls.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status = QLabel(tr("Не запущено"))

        layout = QVBoxLayout(self)
        layout.addWidget(self.lbl_dir)
        layout.addLayout(row)
        layout.addWidget(self.writable)
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
        handler_cls = DAVHandler if self.writable.isChecked() else _Handler
        handler = functools.partial(handler_cls, directory=self.directory)
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
        if self.writable.isChecked():
            self.status.setText(tr("Раздаётся с записью (WebDAV). Не закрывайте окно."))
        else:
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
