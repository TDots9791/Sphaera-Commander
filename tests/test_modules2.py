"""Тесты второй партии: кнопка дисков + Ctrl+F1/F2, вкладки папок,
быстрый поиск, временная панель, веточный просмотр."""

import os
import shutil
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sc_m2_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QAction, QKeyEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from sphaera_commander import app as app_mod  # noqa: E402
from sphaera_commander.plugins import branchview, foldertabs, temppanel  # noqa: E402


def write(path, content="x"):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class DriveSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_drive_button_and_nc_hotkeys(self):
        w = app_mod.MainWindow()
        try:
            w.show()
            self.app.processEvents()
            self.assertEqual(w.left.drive_button.text(), "💾 /")
            shortcuts = {a.shortcut().toString()
                         for a in w.findChildren(QAction)}
            self.assertIn("Ctrl+F1", shortcuts)
            self.assertIn("Ctrl+F2", shortcuts)
        finally:
            w.close()


class FolderTabsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_tabs_sync_with_panel(self):
        w = app_mod.MainWindow()
        try:
            w.show()
            self.app.processEvents()
            plugin = next(p for p in w._plugins if p.id == "foldertabs")
            tabs = plugin._tabs[w.left]
            self.assertEqual(tabs.bar.count(), 1)

            tmp = tempfile.mkdtemp(prefix="sc_tabs_")
            self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
            w.left.cd(tmp)
            w.left.wait_loaded()
            self.app.processEvents()
            self.assertEqual(tabs.bar.tabText(0),
                             os.path.basename(tmp))

            tabs.new_tab()
            self.assertEqual(tabs.bar.count(), 2)
            # переход в другой каталог во второй вкладке — текст обновляется
            sub = os.path.join(tmp, "подкаталог")
            os.makedirs(sub)
            w.left.cd(sub)
            w.left.wait_loaded()
            self.app.processEvents()
            self.assertEqual(tabs.bar.tabText(1), "подкаталог")
            # возврат на первую вкладку — панель следует за вкладкой
            tabs.bar.setCurrentIndex(0)
            w.left.wait_loaded()
            self.app.processEvents()
            self.assertEqual(w.left.current_path(), tmp)
            tabs.close_tab(1)
            self.assertEqual(tabs.bar.count(), 1)
        finally:
            w.close()


class QuickSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_ctrl_alt_letter_jumps(self):
        w = app_mod.MainWindow()
        try:
            w.show()
            self.app.processEvents()
            tmp = tempfile.mkdtemp(prefix="sc_qs_")
            self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
            write(os.path.join(tmp, "альфа.txt"))
            write(os.path.join(tmp, "бета.txt"))
            w.left.cd(tmp)
            w.left.wait_loaded()
            self.app.processEvents()
            event = QKeyEvent(QEvent.KeyPress, Qt.Key_A,
                              Qt.ControlModifier | Qt.AltModifier, text="а")
            QApplication.sendEvent(w.left.view, event)
            self.app.processEvents()
            row = w.left.view.currentIndex().row()
            self.assertEqual(
                w.left.model.entry_at(row).name, "альфа.txt")
        finally:
            w.close()


class TempPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_add_and_copy_to_other_panel(self):
        w = app_mod.MainWindow()
        try:
            w.show()
            self.app.processEvents()
            plugin = next(p for p in w._plugins if p.id == "temppanel")
            src = tempfile.mkdtemp(prefix="sc_tp_src_")
            dst = tempfile.mkdtemp(prefix="sc_tp_dst_")
            self.addCleanup(shutil.rmtree, src, ignore_errors=True)
            self.addCleanup(shutil.rmtree, dst, ignore_errors=True)
            write(os.path.join(src, "один.txt"))
            write(os.path.join(src, "два.txt"))
            w.left.cd(src)
            w.left.wait_loaded()
            w.left.select_all(True)
            plugin._add()
            self.assertEqual(len(plugin.entries), 2)
            w.right.cd(dst)
            w.right.wait_loaded()
            dlg = temppanel.TempPanelDialog(w, plugin)
            dlg._transfer(move=False)
            deadline = time.monotonic() + 5
            while not os.path.isfile(os.path.join(dst, "один.txt")) \
                    and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.02)
            self.assertTrue(os.path.isfile(os.path.join(dst, "один.txt")))
            self.assertTrue(os.path.isfile(os.path.join(dst, "два.txt")))
        finally:
            w.close()


class BranchViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_scan_save_list(self):
        tmp = tempfile.mkdtemp(prefix="sc_br_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        os.makedirs(os.path.join(tmp, "под"))
        write(os.path.join(tmp, "корень.txt"))
        write(os.path.join(tmp, "под", "вложенный.txt"))
        dlg = branchview.BranchDialog(None, tmp)
        deadline = time.monotonic() + 5
        while dlg.btn_save.isEnabled() is False and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.02)
        self.assertEqual(dlg.table.rowCount(), 2)
        dlg._save()
        listing = os.path.join(tmp, "branch-list.txt")
        self.assertTrue(os.path.isfile(listing))
        with open(listing, encoding="utf-8") as f:
            names = f.read()
        self.assertIn("корень.txt", names)
        self.assertIn(os.path.join("под", "вложенный.txt"), names)


if __name__ == "__main__":
    unittest.main()
