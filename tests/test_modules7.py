"""Тесты модулей 0.21.0: анализ места, EXIF-переименование, конвертация
картинок; сравнение каталогов по содержимому в syncdirs."""

import os
import shutil
import struct
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sc_m7_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from sphaera_commander import exif  # noqa: E402
from tests.test_exif import build_jpeg  # noqa: E402


def make_png(path: str) -> None:
    from PySide6.QtGui import QColor, QPixmap

    pm = QPixmap(40, 30)
    pm.fill(QColor(10, 200, 90))
    buf = __import__("PySide6.QtCore", fromlist=["QBuffer"]).QBuffer()
    buf.open(__import__("PySide6.QtCore",
                        fromlist=["QBuffer"]).QBuffer.OpenModeFlag.ReadWrite)
    pm.save(buf, "PNG")
    with open(path, "wb") as f:
        f.write(bytes(buf.data()))


class DirAnalyzeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_da_")
        sub = os.path.join(self.tmp, "большой")
        os.makedirs(sub)
        with open(os.path.join(sub, "data.bin"), "wb") as f:
            f.write(b"x" * 4096)
        with open(os.path.join(self.tmp, "root.txt"), "w") as f:
            f.write("y" * 100)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scan_rows_and_share(self):
        from sphaera_commander.plugins.diranalyze import DirAnalyzeDialog

        dlg = DirAnalyzeDialog(None, self.tmp)
        try:
            deadline = time.time() + 10
            while time.time() < deadline and dlg.table.rowCount() < 2:
                QApplication.processEvents()
                time.sleep(0.02)
            self.assertEqual(dlg.table.rowCount(), 2)
            names = {dlg.table.item(r, 0).text()
                     for r in range(dlg.table.rowCount())}
            self.assertEqual(names, {"большой", "root.txt"})
            # доля заполнена после завершения
            deadline = time.time() + 10
            while time.time() < deadline and not dlg.table.item(0, 3):
                QApplication.processEvents()
                time.sleep(0.02)
            share = dlg.table.item(0, 3)
            self.assertIsNotNone(share)
            self.assertTrue(share.text().endswith("%"))
            # сортировка по размеру: «большой» сверху
            self.assertEqual(dlg.table.item(0, 0).text(), "большой")
            # двойной клик по папке — пересчёт внутри
            dlg._on_double(dlg.table.item(0, 0))
            deadline = time.time() + 10
            while time.time() < deadline and dlg.table.rowCount() != 1:
                QApplication.processEvents()
                time.sleep(0.02)
            self.assertEqual(dlg.table.item(0, 0).text(), "data.bin")
        finally:
            dlg.close()
            dlg.deleteLater()
            QApplication.processEvents()


class ExifRenameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_exifr_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_plan_renames_with_exif_and_collisions(self):
        from sphaera_commander.plugins.exifrename import DEFAULT_PATTERN, \
            plan_renames

        a = os.path.join(self.tmp, "IMG_0001.jpg")
        b = os.path.join(self.tmp, "IMG_0002.jpg")
        c = os.path.join(self.tmp, "IMG_0003.jpg")
        build_jpeg(a, "2023:07:14 09:05:33")
        build_jpeg(b, "2023:07:14 09:05:33")  # та же секунда → -2
        build_jpeg(c, "2024:01:01 00:00:00")
        plan = plan_renames([a, b, c], DEFAULT_PATTERN)
        names = {os.path.basename(old): new for old, new in plan}
        self.assertEqual(names["IMG_0001.jpg"], "2023-07-14_090533.jpg")
        self.assertEqual(names["IMG_0002.jpg"], "2023-07-14_090533-2.jpg")
        self.assertEqual(names["IMG_0003.jpg"], "2024-01-01_000000.jpg")

    def test_plan_without_exif_uses_mtime(self):
        from sphaera_commander.plugins.exifrename import plan_renames

        path = os.path.join(self.tmp, "plain.jpg")
        with open(path, "wb") as f:
            f.write(b"\xff\xd8\xff\xd9")
        stamp = time.strftime("%Y-%m-%d_%H%M%S",
                              time.localtime(os.path.getmtime(path)))
        plan = plan_renames([path], "{date}_{time}")
        self.assertEqual(plan[0][1], stamp + ".jpg")

    def test_dialog_renames_files(self):
        import unittest.mock

        from sphaera_commander.app import MainWindow
        from sphaera_commander.fsmodel import entry_for
        from sphaera_commander.plugins import exifrename

        a = os.path.join(self.tmp, "IMG_0001.jpg")
        build_jpeg(a, "2023:07:14 09:05:33")
        win = MainWindow()
        try:
            win.show()
            win.left.cd(self.tmp)
            win.left.wait_loaded()
            win.left.reveal("IMG_0001.jpg")
            win.left.wait_loaded()
            dlg = exifrename.ExifRenameDialog(win, win.left)
            deadline = time.time() + 10
            while time.time() < deadline:
                item = dlg.table.item(0, 1) if dlg.table.rowCount() else None
                if item is not None:
                    break
                QApplication.processEvents()
                time.sleep(0.02)
            self.assertIsNotNone(dlg.table.item(0, 1))
            self.assertEqual(dlg.table.item(0, 1).text(),
                             "2023-07-14_090533.jpg")
            with unittest.mock.patch.object(
                    exifrename.ExifRenameDialog, "_schedule",
                    lambda self: None):
                dlg._run()
            self.assertTrue(os.path.isfile(
                os.path.join(self.tmp, "2023-07-14_090533.jpg")))
            self.assertFalse(os.path.exists(a))
        finally:
            win.close()


class ImgConvertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_imgc_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_jobs_skips_identity(self):
        from sphaera_commander.plugins.imgconvert import build_jobs

        src = os.path.join(self.tmp, "pic.png")
        jobs = build_jobs([src], self.tmp, "png", 90, "")
        self.assertEqual(jobs, [])  # png → png без суффикса: тот же файл
        jobs = build_jobs([src], self.tmp, "jpg", 90, "-new")
        self.assertEqual(jobs, [(src, os.path.join(self.tmp, "pic-new.jpg"),
                                 "jpg", 90)])

    def test_convert_roundtrip(self):
        from sphaera_commander.plugins.imgconvert import convert_image

        src = os.path.join(self.tmp, "pic.png")
        make_png(src)
        dst = os.path.join(self.tmp, "pic.webp")
        size = convert_image(src, dst, "webp", 90)
        self.assertGreater(size, 0)
        from PySide6.QtGui import QImage

        img = QImage(dst)
        self.assertFalse(img.isNull())
        self.assertEqual(img.width(), 40)

    def test_convert_many_reports_errors(self):
        from sphaera_commander.plugins.imgconvert import convert_many

        missing = os.path.join(self.tmp, "нет.png")
        dst = os.path.join(self.tmp, "нет.jpg")
        result = convert_many([(missing, dst, "jpg", 90)])
        self.assertEqual(result.done_files, 0)
        self.assertTrue(result.errors)
        self.assertIn("не удалось прочитать", result.errors[0].message)


class SyncDirsContentTests(unittest.TestCase):
    def test_collect_rows_by_content(self):
        from sphaera_commander.plugins.syncdirs import (ARROW_CONFLICT,
                                                        ARROW_EQ,
                                                        collect_rows)

        tmp = tempfile.mkdtemp(prefix="sc_syncc_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        left = os.path.join(tmp, "L")
        right = os.path.join(tmp, "R")
        os.makedirs(left)
        os.makedirs(right)
        # одинаковый размер, разное содержимое и разное время
        with open(os.path.join(left, "same-size.txt"), "w") as f:
            f.write("AAA")
        with open(os.path.join(right, "same-size.txt"), "w") as f:
            f.write("BBB")
        os.utime(os.path.join(left, "same-size.txt"),
                 (time.time() - 7200, time.time() - 7200))
        # одинаковое содержимое, разные даты — по содержимому это «=»
        with open(os.path.join(left, "twin.txt"), "w") as f:
            f.write("same")
        with open(os.path.join(right, "twin.txt"), "w") as f:
            f.write("same")
        os.utime(os.path.join(left, "twin.txt"),
                 (time.time() - 3600, time.time() - 3600))

        plain = {name: direction for name, direction, _l, _r
                 in collect_rows(left, right)}
        self.assertEqual(plain["same-size.txt"], "←")  # правый новее
        self.assertEqual(plain["twin.txt"], "←")       # по дате — различаются
        by_content = {name: direction for name, direction, _l, _r
                      in collect_rows(left, right, by_content=True)}
        # содержимое различается → направление по дате (правый новее)
        self.assertEqual(by_content["same-size.txt"], "←")
        # содержимое совпадает → «=», хотя даты различаются
        self.assertEqual(by_content["twin.txt"], ARROW_EQ)


if __name__ == "__main__":
    unittest.main()
