"""Тесты плагинов: менеджер (поиск/включение/ошибки), модули checksums,
splitcombine, compare_content, terminal_here, меню «Инструменты»."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import unittest.mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sc_plg_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

from sphaera_commander import pluginmgr as plugins  # noqa: E402
from sphaera_commander.plugins import (  # noqa: E402
    checksums,
    compare_content,
    splitcombine,
    terminal_here,
)


class ManagerTests(unittest.TestCase):
    def test_builtin_plugins_discovered(self):
        names = {m["name"] for m in plugins.available()
                 if m["source"] == "builtin"}
        self.assertTrue({"checksums", "splitcombine", "terminal_here",
                         "compare_content"} <= names)

    def test_load_all_instantiates_enabled(self):
        app = unittest.mock.MagicMock()
        instances, errors = plugins.load_all(app)
        self.assertEqual(errors, [])
        names = {p.id for p in instances}
        self.assertTrue({"checksums", "splitcombine", "terminal_here",
                         "compare_content"} <= names)

    def test_disable_excludes_plugin(self):
        plugins.set_enabled({"checksums"})
        try:
            app = unittest.mock.MagicMock()
            instances, _errors = plugins.load_all(app)
            self.assertEqual({p.id for p in instances}, {"checksums"})
        finally:
            plugins.set_enabled({m["name"] for m in plugins.available()})

    def test_broken_user_plugin_isolated(self):
        user_dir = tempfile.mkdtemp(prefix="sc_plg_user_")
        old_dir = plugins.USER_DIR
        plugins.USER_DIR = user_dir
        try:
            with open(os.path.join(user_dir, "broken.py"), "w") as f:
                f.write("def create(app):\n    raise RuntimeError('бум')\n")
            plugins.set_enabled(plugins.enabled_names() | {"broken"})
            app = unittest.mock.MagicMock()
            _instances, errors = plugins.load_all(app)
            self.assertEqual([n for n, _e in errors], ["broken"])
            self.assertIn("бум", errors[0][1])
        finally:
            plugins.USER_DIR = old_dir
            shutil.rmtree(user_dir, ignore_errors=True)


class ChecksumsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_sum_")
        self.file = os.path.join(self.tmp, "док.txt")
        with open(self.file, "w", encoding="utf-8") as f:
            f.write("данные для суммы\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hash_file(self):
        self.assertEqual(len(checksums._hash_file(self.file, "MD5")), 32)
        self.assertEqual(len(checksums._hash_file(self.file, "SHA-256")), 64)

    def test_compute_save_verify(self):
        app = QApplication.instance() or QApplication([])
        dlg = checksums.ChecksumsDialog(None, [self.file], self.tmp)
        dlg.algo.setCurrentText("SHA-256")
        dlg._calc()
        deadline = time.monotonic() + 5
        while dlg.table.rowCount() < 1 and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        digest = dlg.table.item(0, 1).text()
        dlg._save()
        sums_files = [n for n in os.listdir(self.tmp) if n.endswith(".sha256")]
        self.assertEqual(len(sums_files), 1)
        sums = os.path.join(self.tmp, sums_files[0])
        with open(sums, encoding="utf-8") as f:
            self.assertIn(digest, f.read())
        # проверка: всё совпадает
        dlg._verify()
        self.assertIn("совпали", dlg.status.text())
        # порча файла → не совпало
        with open(self.file, "a", encoding="utf-8") as f:
            f.write("ещё")
        dlg._verify()
        self.assertIn("не совпало", dlg.status.text())


class SplitCombineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_split_")
        self.src = os.path.join(self.tmp, "данные.bin")
        with open(self.src, "wb") as f:
            f.write(os.urandom(1500 * 1024))  # 1.5 МиБ → 2 части по 1.44

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _wait_dialog(self, app, dlg):
        deadline = time.monotonic() + 10
        while (not dlg.btn_split.isEnabled()
               or not dlg.btn_combine.isEnabled()) \
                and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)

    def test_split_and_combine_roundtrip(self):
        app = QApplication.instance() or QApplication([])
        dlg = splitcombine.SplitCombineDialog(None, self.tmp, [self.src])
        dlg._split()
        self._wait_dialog(app, dlg)
        parts = sorted(n for n in os.listdir(self.tmp)
                       if n.startswith("данные.bin.") and n != "данные.bin.crc")
        self.assertEqual(parts, ["данные.bin.001", "данные.bin.002"])
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "данные.bin.crc")))
        with open(os.path.join(self.tmp, "данные.bin.crc"),
                  encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], "данные.bin")
        self.assertEqual(int(lines[1]), 1500 * 1024)
        # пересобрать: убрать оригинал, собрать, сравнить байты
        original = open(self.src, "rb").read()
        os.remove(self.src)
        dlg._combine()
        self._wait_dialog(app, dlg)
        self.assertTrue(os.path.isfile(self.src))
        with open(self.src, "rb") as f:
            self.assertEqual(f.read(), original)
        self.assertIn("CRC32 OK", dlg.status.text())

    def test_combine_without_parts_reports(self):
        app = QApplication.instance() or QApplication([])
        dlg = splitcombine.SplitCombineDialog(None, self.tmp, [])
        dlg._combine()
        self._wait_dialog(app, dlg)
        self.assertIn("нет частей", dlg.status.text())


class CompareContentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_cmp_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_identical(self):
        from PySide6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])
        a = self._write("a.txt", "одинаковый текст\n")
        b = self._write("b.txt", "одинаковый текст\n")
        dlg = compare_content.DiffDialog(None, a, b)
        self.assertIn("идентичны", dlg.text.toPlainText())

    def test_text_diff(self):
        from PySide6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])
        a = self._write("a.txt", "строка 1\nстрока 2\n")
        b = self._write("b.txt", "строка 1\nстрока X\n")
        dlg = compare_content.DiffDialog(None, a, b)
        text = dlg.text.toPlainText()
        self.assertIn("Первое отличие", text)
        self.assertIn("строка X", text)

    def test_size_mismatch(self):
        from PySide6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])
        a = self._write("a.txt", "короткий")
        b = self._write("b.txt", "заметно более длинный текст")
        dlg = compare_content.DiffDialog(None, a, b)
        self.assertIn("Размеры различаются", dlg.text.toPlainText())


class TerminalTests(unittest.TestCase):
    def test_find_with_mock(self):
        with unittest.mock.patch.object(terminal_here.shutil, "which",
                                        return_value="/usr/bin/xterm"):
            found = terminal_here.find_terminal()
        self.assertIsNotNone(found)
        self.assertEqual(found[0], "/usr/bin/xterm")

    @unittest.skipUnless(os.name == "posix",
                         "на Windows список терминалов платформенный "
                         "(покрыт тестами репо Win64)")
    def test_open_in_records_process(self):
        from PySide6.QtCore import QProcess

        with unittest.mock.patch.object(terminal_here.shutil, "which",
                                        return_value="/usr/bin/xterm"), \
             unittest.mock.patch.object(
                 QProcess, "startDetached") as mock_start:
            err = terminal_here.open_in("/tmp")
        self.assertIsNone(err)
        self.assertTrue(mock_start.called)
        args = mock_start.call_args
        self.assertEqual(args[0][1][0], "--working-directory")


class MainWindowIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_tools_menu_has_plugins(self):
        from sphaera_commander import app as app_mod

        w = app_mod.MainWindow()
        try:
            titles = [a.text() for a in w.menuBar().actions()]
            self.assertIn("&Инструменты", titles)
            tools = w.menuBar().actions()[titles.index("&Инструменты")].menu()
            texts = [a.text() for a in tools.actions()]
            self.assertIn("Контрольные суммы…", texts)
            self.assertIn("Плагины…", texts)
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
