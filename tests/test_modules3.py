"""Тесты партии 3: webshare (HTTP-раздача), encrypt (gpg), duplicates."""

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.parse
import urllib.request

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sc_m3_"))
os.environ["GNUPGHOME"] = tempfile.mkdtemp(prefix="sc_gnupg_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from sphaera_commander import dialogs as dialogs_mod  # noqa: E402
from sphaera_commander.plugins import duplicates, encrypt, webshare  # noqa: E402


class FakeApp(QObject):
    gui_call = Signal(object)

    def __init__(self):
        super().__init__()
        self.gui_call.connect(lambda fn: fn())


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


class WebShareTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.tmp = tempfile.mkdtemp(prefix="sc_share_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        write(os.path.join(self.tmp, "страница.txt"), "привет по сети")

    def test_http_share_roundtrip(self):
        port = free_port()
        dlg = webshare.ShareDialog(None, self.tmp)
        dlg.port_edit.setText(str(port))
        dlg._toggle()
        self.assertIsNotNone(dlg.server)
        try:
            body = None
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    quoted = urllib.parse.quote("страница.txt")
                    with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/{quoted}",
                            timeout=2) as resp:
                        body = resp.read().decode("utf-8")
                    break
                except OSError:
                    time.sleep(0.05)
            self.assertEqual(body, "привет по сети")
        finally:
            dlg._stop()
        self.assertIsNone(dlg.server)


class EncryptTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.fake = FakeApp()
        self.tmp = tempfile.mkdtemp(prefix="sc_enc_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.file = write(os.path.join(self.tmp, "секрет.txt"),
                          "секретное содержимое\n")

    def _wait(self, dialog, needle_status=None):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.02)
            if needle_status and needle_status in dialog.status.text():
                return

    def test_encrypt_decrypt_roundtrip(self):
        dlg = encrypt.CryptoDialog(None, [self.file], decrypt=False)
        dlg.app = self.fake
        dlg.pass1.setText("пароль-фраза")
        dlg.pass2.setText("пароль-фраза")
        dlg._run()
        gpg_file = self.file + ".gpg"
        deadline = time.monotonic() + 20
        while not os.path.isfile(gpg_file) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.05)
        self.assertTrue(os.path.isfile(gpg_file))
        self.assertNotEqual(open(gpg_file, "rb").read(),
                            open(self.file, "rb").read())

        dlg2 = encrypt.CryptoDialog(None, [gpg_file], decrypt=True)
        dlg2.app = self.fake
        dlg2.pass1.setText("пароль-фраза")
        dlg2.pass2.setText("пароль-фраза")
        dlg2._run()
        deadline = time.monotonic() + 20
        while not os.path.isfile(self.file) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.05)
        self.assertEqual(open(self.file, encoding="utf-8").read(),
                         "секретное содержимое\n")

    def test_passphrase_mismatch_rejected(self):
        dlg = encrypt.CryptoDialog(None, [self.file], decrypt=False)
        dlg.app = self.fake
        dlg.pass1.setText("одна")
        dlg.pass2.setText("другая")
        dlg._run()
        self.assertIn("не совпадают", dlg.status.text())
        self.assertFalse(os.path.isfile(self.file + ".gpg"))


class DuplicatesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.fake = FakeApp()
        self.tmp = tempfile.mkdtemp(prefix="sc_dup_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        content = "одинаковое содержимое" * 100
        self.a = write(os.path.join(self.tmp, "a.bin"), content)
        self.b = write(os.path.join(self.tmp, "b.bin"), content)
        self.c = write(os.path.join(self.tmp, "уникальный.bin"), "другое")

    def test_groups_found_and_delete(self):
        dlg = duplicates.DuplicatesDialog(None, self.tmp)
        dlg.app = self.fake
        deadline = time.monotonic() + 10
        while "Групп дубликатов" not in dlg.status.text() \
                and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.02)
        self.assertEqual(dlg.table.rowCount(), 2)  # одна группа из двух
        # удалить b.bin: выделить его строку
        for r in range(dlg.table.rowCount()):
            if dlg.table.item(r, 0).text() == self.b:
                dlg.table.selectRow(r)
        import sphaera_commander.ops as ops_mod
        from sphaera_commander.ops import OpResult

        def fake_trash(entries, progress_cb, is_cancelled):
            for e in entries:
                os.remove(e.path)
            return OpResult(done_files=len(entries))

        with unittest.mock.patch.object(dialogs_mod, "confirm_delete",
                                        return_value=True), \
             unittest.mock.patch.object(ops_mod, "execute_trash",
                                        side_effect=fake_trash):
            dlg._delete_selected()
            deadline = time.monotonic() + 10
            while "Удалено в корзину" not in dlg.status.text() \
                    and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.05)
        self.assertFalse(os.path.exists(self.b))
        self.assertTrue(os.path.exists(self.a))
        self.assertIn("Удалено в корзину", dlg.status.text())

    def test_report_saved(self):
        dlg = duplicates.DuplicatesDialog(None, self.tmp)
        dlg.app = self.fake
        deadline = time.monotonic() + 10
        while "Групп дубликатов" not in dlg.status.text() \
                and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.02)
        dlg._save_report()
        report = os.path.join(self.tmp, "duplicates-report.txt")
        self.assertTrue(os.path.isfile(report))
        with open(report, encoding="utf-8") as f:
            text = f.read()
        self.assertIn(self.a, text)
        self.assertIn(self.b, text)


if __name__ == "__main__":
    unittest.main()
