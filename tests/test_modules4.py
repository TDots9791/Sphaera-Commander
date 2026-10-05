"""Тесты партии 4: WebDAV-режим раздачи, age-шифрование, дубликаты
между двумя панелями."""

import http.client
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.parse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sc_m4_"))
os.environ["GNUPGHOME"] = tempfile.mkdtemp(prefix="sc_gnupg4_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

from sphaera_commander.plugins import duplicates, encrypt, webshare  # noqa: E402


def write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def free_port() -> int:
    probe = socket.socket()
    probe.bind(("", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


import socket  # noqa: E402


class WebDavTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.tmp = tempfile.mkdtemp(prefix="sc_dav_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        write(os.path.join(self.tmp, "существующий.txt"), "старое")
        self.port = free_port()
        self.dlg = webshare.ShareDialog(None, self.tmp)
        self.dlg.writable.setChecked(True)
        self.dlg.port_edit.setText(str(self.port))
        self.dlg._toggle()
        self.addCleanup(self.dlg._stop)
        deadline = time.monotonic() + 5
        while self.dlg.server is None and time.monotonic() < deadline:
            time.sleep(0.02)
        self.conn = http.client.HTTPConnection("127.0.0.1", self.port,
                                               timeout=5)
        self.addCleanup(self.conn.close)

    def _request(self, method, path, body=None, headers=None):
        quoted = urllib.parse.quote(path)
        self.conn.request(method, quoted, body=body, headers=headers or {})
        return self.conn.getresponse()

    def test_options_declares_dav(self):
        resp = self._request("OPTIONS", "/")
        self.assertEqual(resp.status, 200)
        self.assertIn("1", resp.getheader("DAV"))

    def test_put_get_delete_roundtrip(self):
        resp = self._request("PUT", "/новый.txt", body="загружено по dav".encode("utf-8"))
        self.assertEqual(resp.status, 201)
        self.assertEqual(
            open(os.path.join(self.tmp, "новый.txt"), encoding="utf-8").read(),
            "загружено по dav")
        resp = self._request("GET", "/новый.txt")
        self.assertEqual(resp.read().decode("utf-8"), "загружено по dav")
        resp = self._request("DELETE", "/новый.txt")
        self.assertEqual(resp.status, 204)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "новый.txt")))

    def test_propfind_lists_entries(self):
        resp = self._request("PROPFIND", "/", headers={"Depth": "1"})
        self.assertEqual(resp.status, 207)
        body = resp.read().decode("utf-8")
        self.assertIn("существующий.txt", body)
        self.assertIn("multistatus", body)

    def test_mkcol_and_move(self):
        resp = self._request("MKCOL", "/папка")
        self.assertEqual(resp.status, 201)
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "папка")))
        resp = self._request(
            "MOVE", "/существующий.txt",
            headers={"Destination": urllib.parse.quote("/папка/переименовано.txt")})
        self.assertEqual(resp.status, 201)
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmp, "папка", "переименовано.txt")))
        self.assertFalse(os.path.isfile(
            os.path.join(self.tmp, "существующий.txt")))

    def test_traversal_rejected(self):
        resp = self._request("PUT", "/../выход.txt", body="зло".encode("utf-8"))
        self.assertEqual(resp.status, 403)
        self.assertFalse(os.path.exists(
            os.path.join(os.path.dirname(self.tmp), "выход.txt")))


class AgeTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.tmp = tempfile.mkdtemp(prefix="sc_age_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.keyfile = os.path.join(self.tmp, "age.key")
        self._old_key = encrypt.AGE_KEY_FILE
        encrypt.AGE_KEY_FILE = self.keyfile

    def tearDown(self):
        encrypt.AGE_KEY_FILE = self._old

    def _old(self):
        return self._old_key

    def test_public_key_parse(self):
        with open(self.keyfile, "w", encoding="utf-8") as f:
            f.write("# created: 2026\nAGE-SECRET-KEY-XXX\nage1qwe123\n")
        self.assertEqual(encrypt.age_public_key(), "age1qwe123")
        self.assertEqual(encrypt.age_public_key(), "age1qwe123")

    def test_encrypt_uses_recipient_arg(self):
        with open(self.keyfile, "w", encoding="utf-8") as f:
            f.write("AGE-SECRET-KEY-XXX\nage1pub\n")
        calls = []

        def fake_run(args, **_kw):
            calls.append(args)
            return 0, ""

        target = write(os.path.join(self.tmp, "док.txt"), "тайна")
        with unittest.mock.patch.object(encrypt, "age_bin",
                                        return_value="/usr/bin/age"), \
             unittest.mock.patch.object(encrypt, "_run_age",
                                        side_effect=fake_run):
            dlg = encrypt.AgeDialog(None, [target], decrypt=False)
            dlg.app = unittest.mock.MagicMock()
            dlg.app.gui_call = __import__("PySide6.QtCore",
                                          fromlist=["Signal"]).QObject()
            from PySide6.QtCore import QObject, Signal as Sig

            class Holder(QObject):
                gui_call = Sig(object)

            holder = Holder()
            holder.gui_call.connect(lambda fn: fn())
            dlg.app = holder
            dlg._run()
        self.assertEqual(calls, [["-r", "age1pub", "-o", target + ".age",
                                  target]])

    def test_absent_age_reports(self):
        with unittest.mock.patch.object(encrypt, "age_bin",
                                        return_value=None):
            dlg = encrypt.AgeDialog(None, [], decrypt=False)
            dlg._run()
        self.assertIn("age не установлен", dlg.status.text())


class TwoPanelDuplicatesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_only_cross_panel_groups(self):
        tmp_a = tempfile.mkdtemp(prefix="sc_dup_a_")
        tmp_b = tempfile.mkdtemp(prefix="sc_dup_b_")
        self.addCleanup(shutil.rmtree, tmp_a, ignore_errors=True)
        self.addCleanup(shutil.rmtree, tmp_b, ignore_errors=True)
        same = "общее содержимое" * 50
        write(os.path.join(tmp_a, "общий.bin"), same)
        write(os.path.join(tmp_b, "копия.bin"), same)
        write(os.path.join(tmp_a, "только_у_A.bin"), "уникальное")  # нет пары в B
        dlg = duplicates.DuplicatesDialog(None, tmp_a, tmp_b)
        deadline = time.monotonic() + 10
        while "Дубликаты" not in dlg.windowTitle() or dlg.table.rowCount() < 2:
            if time.monotonic() > deadline and dlg.table.rowCount() >= 2:
                break
            self.app.processEvents()
            time.sleep(0.02)
        self.assertEqual(dlg.table.rowCount(), 2)
        tags = {dlg.table.item(r, 3).text()
                for r in range(dlg.table.rowCount())}
        self.assertEqual(tags, {"A", "B"})


if __name__ == "__main__":
    unittest.main()
