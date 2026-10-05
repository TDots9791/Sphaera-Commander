"""Тесты: миниатюры, drag-and-drop, монтирование."""

import os
import shutil
import sys
import tempfile
import time
import unittest
import unittest.mock
import json as json_m

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QBuffer, QModelIndex, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from sphaera_commander import mounts, thumbnails  # noqa: E402
from sphaera_commander.fsmodel import FileTableModel  # noqa: E402


def make_png(path: str, color=(30, 120, 200)) -> None:
    pm = QPixmap(60, 40)
    pm.fill(__import__("PySide6.QtGui", fromlist=["QColor"]).QColor(*color))
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.ReadWrite)
    pm.save(buf, "PNG")
    with open(path, "wb") as f:
        f.write(bytes(buf.data()))


class ThumbnailStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_thumb_")
        cache = os.path.join(self.tmp, "cache")
        os.environ["XDG_CACHE_HOME"] = cache
        self.store = thumbnails.ThumbnailStore(thumb_size=96)
        self.png = os.path.join(self.tmp, "img.png")
        make_png(self.png)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _wait_ready(self, timeout_s: float = 5.0) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
            st = os.stat(self.png)
            if os.path.isfile(thumbnails.cache_path(
                    self.store.cache_dir, self.png, st.st_mtime, st.st_size, 96)):
                return

    def test_generate_creates_disk_cache(self):
        self.assertIsNone(self.store.get(self.png, *self._stat()))
        self._wait_ready()
        st = self._stat()
        cache = thumbnails.cache_path(self.store.cache_dir, self.png,
                                      st[0], st[1], 96)
        self.assertTrue(os.path.isfile(cache))

    def test_second_get_from_cache(self):
        st = self._stat()
        self.assertIsNone(self.store.get(self.png, st[0], st[1]))  # ставит в очередь
        self._wait_ready()  # фоновая генерация + кэш на диске
        icon = self.store.get(self.png, *self._stat())
        self.assertIsNotNone(icon)
        self.assertFalse(icon.isNull())

    def test_mtime_invalidates(self):
        self._wait_ready()
        st = self._stat()
        old = thumbnails.cache_path(self.store.cache_dir, self.png,
                                    st[0], st[1], 96)
        time.sleep(0.02)
        os.utime(self.png, (time.time() + 5, time.time() + 5))
        st2 = self._stat()
        new = thumbnails.cache_path(self.store.cache_dir, self.png,
                                    st2[0], st2[1], 96)
        self.assertNotEqual(old, new)

    def test_not_image_ext(self):
        self.assertFalse(thumbnails.is_image("/x/file.txt"))
        self.assertTrue(thumbnails.is_image("/x/photo.JPG"))

    def _stat(self):
        st = os.stat(self.png)
        return (st.st_mtime, st.st_size)


class ThumbnailModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_model_returns_thumbnail_icon(self):
        tmp = tempfile.mkdtemp(prefix="sc_thumb_m_")
        try:
            png = os.path.join(tmp, "pic.png")
            make_png(png)
            from sphaera_commander.fsmodel import FileEntry

            store = thumbnails.ThumbnailStore(thumb_size=96)
            model = FileTableModel()
            model.set_thumbnails(True, store)
            model.reload(tmp)
            # ждём фоновой генерации
            deadline = time.time() + 5
            while time.time() < deadline:
                cls_app = QApplication.instance()
                cls_app.processEvents()
                time.sleep(0.01)
                icon = model.data(model.index(1, 0), Qt.DecorationRole)
                if icon is not None and not icon.isNull():
                    break
            icon = model.data(model.index(1, 0), Qt.DecorationRole)
            self.assertIsNotNone(icon)
            self.assertFalse(icon.isNull())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class DragAndDropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_dnd_")
        write_a = os.path.join(self.tmp, "src")
        os.makedirs(write_a)
        open(os.path.join(write_a, "один.txt"), "w").write("1")
        open(os.path.join(write_a, "два.txt"), "w").write("2")
        self.src = write_a

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_model_flags_drag_enabled(self):
        from sphaera_commander.panel import FilePanel

        panel = FilePanel()
        panel.cd(self.src)
        panel.wait_loaded()
        idx = panel.model.index(1, 0)
        self.assertTrue(bool(panel.model.flags(idx) & Qt.ItemIsDragEnabled))
        dotdot = panel.model.index(0, 0)
        self.assertFalse(bool(panel.model.flags(dotdot) & Qt.ItemIsDragEnabled))

    def test_mime_data_carries_urls_preferring_marks(self):
        from sphaera_commander.panel import FilePanel

        panel = FilePanel()
        panel.cd(self.src)
        panel.wait_loaded()
        mime = panel.model.mimeData([panel.model.index(1, 0)])
        self.assertEqual(len(mime.urls()), 1)
        self.assertTrue(mime.urls()[0].isLocalFile())
        # отметки приоритетнее переданных индексов
        panel.model.set_mark("один.txt", True)
        panel.model.set_mark("два.txt", True)
        mime = panel.model.mimeData([panel.model.index(1, 0)])
        self.assertEqual(len(mime.urls()), 2)

    def test_drop_handler_copies_via_engine(self):
        from sphaera_commander.app import MainWindow
        from sphaera_commander.fsmodel import entry_for

        dest = os.path.join(self.tmp, "dest")
        os.makedirs(dest)
        win = MainWindow()
        win.show()
        win._on_drop([os.path.join(self.src, "один.txt"),
                      os.path.join(self.src, "два.txt")], dest, win.left)
        deadline = time.time() + 10
        while time.time() < deadline and win._thread is not None:
            QApplication.processEvents()
            time.sleep(0.02)
        self.assertTrue(os.path.isfile(os.path.join(dest, "один.txt")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "два.txt")))
        win.close()

    def test_drop_into_same_dir_ignored(self):
        from sphaera_commander.app import MainWindow

        win = MainWindow()
        win.show()
        win._on_drop([os.path.join(self.src, "один.txt")], self.src, win.left)
        self.assertIn("уже в этой папке", win.statusBar().currentMessage())
        win.close()


class DropMoveTests(unittest.TestCase):
    """DnD: с Shift — перенос, без — копирование (как в TC)."""

    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.tmp = tempfile.mkdtemp(prefix="sc_drop_")
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(self.src)
        for name in ("один.txt", "два.txt"):
            with open(os.path.join(self.src, name), "w") as f:
                f.write(name)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_op(self, win):
        deadline = time.time() + 10
        while time.time() < deadline and win._thread is not None:
            QApplication.processEvents()
            time.sleep(0.02)

    def test_drop_with_move_moves_files(self):
        from sphaera_commander.app import MainWindow

        dest = os.path.join(self.tmp, "dest")
        os.makedirs(dest)
        win = MainWindow()
        win.show()
        win._on_drop([os.path.join(self.src, "один.txt"),
                      os.path.join(self.src, "два.txt")], dest, win.left,
                     move=True)
        self._run_op(win)
        self.assertTrue(os.path.isfile(os.path.join(dest, "один.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.src, "один.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.src, "два.txt")))
        win.close()

    def test_drop_without_move_copies(self):
        from sphaera_commander.app import MainWindow

        dest = os.path.join(self.tmp, "dest2")
        os.makedirs(dest)
        win = MainWindow()
        win.show()
        win._on_drop([os.path.join(self.src, "один.txt")], dest, win.left,
                     move=False)
        self._run_op(win)
        self.assertTrue(os.path.isfile(os.path.join(dest, "один.txt")))
        self.assertTrue(os.path.isfile(os.path.join(self.src, "один.txt")))
        win.close()



class DirSizesTests(unittest.TestCase):
    """Размеры каталогов в колонке «Размер» (фон, по включению)."""

    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.tmp = tempfile.mkdtemp(prefix="sc_dirsizes_")
        self.root = os.path.join(self.tmp, "root")
        sub = os.path.join(self.root, "папка")
        deep = os.path.join(sub, "глубоко")
        os.makedirs(deep)
        with open(os.path.join(sub, "a.bin"), "wb") as f:
            f.write(b"x" * 2048)
        with open(os.path.join(deep, "b.bin"), "wb") as f:
            f.write(b"y" * 1024)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_model_set_dir_size_updates_size_column(self):
        from sphaera_commander.fsmodel import SIZE_COL, scan_directory

        model = FileTableModel()
        entries = scan_directory(self.root, False)
        model.set_entries(self.root, entries)
        row = model.row_of_name("папка")
        self.assertEqual(model.index(row, SIZE_COL).data(), "<КАТ>")
        self.assertTrue(model.set_dir_size(os.path.join(self.root, "папка"),
                                           3072))
        self.assertEqual(model.index(row, SIZE_COL).data(), "3 КиБ")
        # отсутствующий путь запоминается, но строки нет
        self.assertFalse(model.set_dir_size(os.path.join(self.root, "нет"), 1))

    def test_panel_fills_sizes_in_background(self):
        from sphaera_commander.panel import FilePanel

        panel = FilePanel()
        try:
            panel.cd(self.root)
            panel.wait_loaded()
            panel.set_dirsizes(True)
            row = panel.model.row_of_name("папка")
            deadline = time.time() + 10
            while time.time() < deadline:
                data = panel.model.index(row, 2).data()
                if data != "<КАТ>":
                    break
                QApplication.processEvents()
                time.sleep(0.02)
            self.assertEqual(panel.model.index(row, 2).data(), "3 КиБ")
            # выключение возвращает <КАТ>
            panel.set_dirsizes(False)
            self.assertEqual(panel.model.index(row, 2).data(), "<КАТ>")
        finally:
            panel.set_dirsizes(False)
            panel.deleteLater()
            QApplication.processEvents()


class ThemeTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        from sphaera_commander import theme

        theme.apply_brand_theme(self.app)

    def test_light_theme_is_light_and_styled(self):
        from sphaera_commander import theme

        theme.apply_light_theme(self.app)
        window_color = self.app.palette().color(
            QPalette.ColorRole.Window)
        self.assertGreater(window_color.lightness(), 200)
        self.assertEqual(window_color.name().upper(),
                         theme.PERGAMENT_WINDOW.upper())
        self.assertTrue(theme.LIGHT_STYLESHEET.strip()
                        in (self.app.styleSheet() or ""))

    def test_apply_theme_modes(self):
        from sphaera_commander import theme

        theme.apply_theme(self.app, "light")
        self.assertGreater(self.app.palette().color(
            QPalette.ColorRole.Window).lightness(), 200)
        theme.apply_theme(self.app, "dark")
        self.assertLess(self.app.palette().color(
            QPalette.ColorRole.Window).lightness(), 128)
        theme.apply_theme(self.app, "system")
        self.assertEqual(self.app.styleSheet(), "")

    def test_current_mode_reads_config_with_legacy(self):
        import unittest.mock

        from sphaera_commander import config, theme

        with unittest.mock.patch.object(config, "qsettings") as fake:
            s = fake.return_value
            s.value = lambda key, _d="": {"view/theme": "light"}.get(key, _d)
            self.assertEqual(theme.current_mode(), "light")
            s.value = lambda key, _d="": {"view/theme": "",
                                          "view/brand_theme": "false"}.get(key, _d)
            self.assertEqual(theme.current_mode(), "system")
            s.value = lambda key, _d="": {"view/theme": "",
                                          "view/brand_theme": "true"}.get(key, _d)
            self.assertEqual(theme.current_mode(), "dark")


class PdfThumbnailTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.tmp = tempfile.mkdtemp(prefix="sc_pdfthumb_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_is_thumbable_includes_pdf(self):
        self.assertTrue(thumbnails.is_thumbable("/x/doc.pdf"))
        self.assertTrue(thumbnails.is_thumbable("/x/img.png"))
        self.assertFalse(thumbnails.is_thumbable("/x/notes.txt"))

    def test_pdf_thumbnail_generated(self):
        import time as _time

        sys.path.insert(0, os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        from tests.test_documents import make_pdf

        pdf = os.path.join(self.tmp, "doc.pdf")
        make_pdf(pdf, ["Hello"])
        store = thumbnails.store()
        icon = store.get(pdf, 1.0, 1000)
        deadline = _time.time() + 10
        while _time.time() < deadline and (icon is None or icon.isNull()):
            QApplication.processEvents()
            _time.sleep(0.05)
            icon = store.get(pdf, 1.0, 1000)
        self.assertIsNotNone(icon)
        self.assertFalse(icon.isNull())


class PanelHistoryTests(unittest.TestCase):
    """История навигации панели: назад/вперёд, ветвление, список."""

    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.tmp = tempfile.mkdtemp(prefix="sc_hist_")
        self.a = os.path.join(self.tmp, "а")
        self.b = os.path.join(self.tmp, "б")
        self.c = os.path.join(self.tmp, "в")
        for p in (self.a, self.b, self.c):
            os.makedirs(p)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_back_forward_and_branching(self):
        from sphaera_commander.panel import FilePanel

        panel = FilePanel()
        try:
            panel.cd(self.a)
            panel.wait_loaded()
            panel.cd(self.b)
            panel.wait_loaded()
            panel.cd(self.c)
            panel.wait_loaded()
            self.assertEqual(panel.current_path(), self.c)
            self.assertTrue(panel.navigate_history(-1))
            panel.wait_loaded()
            self.assertEqual(panel.current_path(), self.b)
            self.assertTrue(panel.navigate_history(-1))
            panel.wait_loaded()
            self.assertEqual(panel.current_path(), self.a)
            # перед «а» в истории — домашний каталог из инициализации панели
            self.assertTrue(panel.navigate_history(-1))
            panel.wait_loaded()
            self.assertEqual(panel.current_path(), os.path.expanduser("~"))
            self.assertFalse(panel.navigate_history(-1))  # начало
            self.assertTrue(panel.navigate_history(1))
            panel.wait_loaded()
            self.assertTrue(panel.navigate_history(1))
            panel.wait_loaded()
            self.assertTrue(panel.navigate_history(1))
            panel.wait_loaded()
            self.assertFalse(panel.navigate_history(1))  # конец
            # ветвление: из прошлого уходим в новый каталог — хвост отрезается
            panel.navigate_history(-1)
            panel.wait_loaded()
            panel.cd(self.c)
            self.assertEqual(panel.history().count(self.b), 1)
            self.assertFalse(panel.navigate_history(1))
        finally:
            panel.set_dirsizes(False)
            panel.deleteLater()
            QApplication.processEvents()

    def test_history_records_vfs(self):
        import subprocess as _sp

        from sphaera_commander.panel import FilePanel

        if not shutil.which("zip"):
            self.skipTest("zip не установлен")
        arc = os.path.join(self.tmp, "book.zip")
        with open(os.path.join(self.tmp, "x.txt"), "w") as f:
            f.write("x")
        _sp.run(["zip", "-j", "-q", arc,
                 os.path.join(self.tmp, "x.txt")], check=True)
        panel = FilePanel()
        try:
            self.assertTrue(panel.cd(self.tmp))
            panel.wait_loaded()
            self.assertTrue(panel.cd(arc))  # вход в архив
            panel.wait_loaded()
            self.assertIn("::", panel.current_path())
            self.assertTrue(panel.navigate_history(-1))
            panel.wait_loaded()
            self.assertEqual(panel.current_path(), self.tmp)
        finally:
            panel.set_dirsizes(False)
            panel.deleteLater()
            QApplication.processEvents()


class PropertiesTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.tmp = tempfile.mkdtemp(prefix="sc_props_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_details_and_permission_roundtrip(self):
        from sphaera_commander import fsmodel

        path = os.path.join(self.tmp, "script.sh")
        with open(path, "w") as f:
            f.write("#!/bin/sh\necho ok\n")
        os.chmod(path, 0o640)
        details = fsmodel.entry_details(path)
        self.assertFalse(details["is_dir"])
        self.assertEqual(details["size"], len(b"#!/bin/sh\necho ok\n"))
        self.assertTrue(details["owner"])
        self.assertFalse(details["is_link"])

        bits = fsmodel.permission_bits(0o640)
        self.assertEqual(bits, (True, True, False,
                                True, False, False,
                                False, False, False))
        mode = fsmodel.bits_to_mode((True, True, True,
                                     True, False, True,
                                     True, False, True), 0o100640)
        self.assertEqual(mode & 0o777, 0o755)
        fsmodel.set_permissions(path, mode)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o755)

    def test_properties_dialog_applies_chmod(self):
        import unittest.mock

        from sphaera_commander.dialogs import PropertiesDialog

        path = os.path.join(self.tmp, "run.sh")
        with open(path, "w") as f:
            f.write("x")
        os.chmod(path, 0o600)
        dlg = PropertiesDialog(None, path)
        # включить «исполнение» владельца и применить
        for idx, box in dlg._boxes:
            if idx == 2:  # owner execute
                box.setChecked(True)
        dlg._apply()
        self.assertTrue(os.stat(path).st_mode & 0o100)

    def test_details_dir_counts(self):
        from sphaera_commander import fsmodel

        sub = os.path.join(self.tmp, "под")
        os.makedirs(sub)
        with open(os.path.join(sub, "f.bin"), "wb") as f:
            f.write(b"x" * 100)
        details = fsmodel.entry_details(self.tmp)
        self.assertTrue(details["is_dir"])
        self.assertEqual(details["files"], 1)
        self.assertEqual(details["dirs"], 1)


class LinkTests(unittest.TestCase):
    """Символьные и жёсткие ссылки (меню Файл)."""

    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.tmp = tempfile.mkdtemp(prefix="sc_link_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_symlink_creation(self):
        import unittest.mock

        from sphaera_commander.app import MainWindow

        target = os.path.join(self.tmp, "цель.txt")
        with open(target, "w") as f:
            f.write("data")
        win = MainWindow()
        try:
            win.show()
            win.left.cd(self.tmp)
            win.left.wait_loaded()
            win.left.reveal("цель.txt")
            win.left.wait_loaded()
            with unittest.mock.patch(
                    "sphaera_commander.app.QInputDialog.getText",
                    return_value=("моя ссылка", True)):
                win.do_symlink()
            link = os.path.join(self.tmp, "моя ссылка")
            self.assertTrue(os.path.islink(link))
            self.assertEqual(os.readlink(link), target)
        finally:
            win.close()

    def test_hardlink_to_other_panel(self):
        from sphaera_commander.app import MainWindow

        src_dir = os.path.join(self.tmp, "источник")
        dst_dir = os.path.join(self.tmp, "приёмник")
        os.makedirs(src_dir)
        os.makedirs(dst_dir)
        target = os.path.join(src_dir, "файл.bin")
        with open(target, "w") as f:
            f.write("same")
        win = MainWindow()
        try:
            win.show()
            win.left.cd(src_dir)
            win.right.cd(dst_dir)
            win.left.wait_loaded()
            win.right.wait_loaded()
            win.left.reveal("файл.bin")
            win.left.wait_loaded()
            win.do_hardlink()
            self.assertTrue(os.path.exists(os.path.join(dst_dir, "файл.bin")))
            self.assertEqual(
                os.stat(target).st_ino,
                os.stat(os.path.join(dst_dir, "файл.bin")).st_ino)
        finally:
            win.close()

    def test_create_file_shift_f4(self):
        import unittest.mock

        from sphaera_commander.app import MainWindow

        win = MainWindow()
        try:
            win.show()
            win.left.cd(self.tmp)
            win.left.wait_loaded()
            captured = {}
            with unittest.mock.patch(
                    "sphaera_commander.app.QInputDialog.getText",
                    return_value=("новый-файл.txt", True)), \
                    unittest.mock.patch(
                        "sphaera_commander.app.open_viewer",
                        side_effect=lambda parent, path, files,
                        editable=False, modal=True, goto_line=0:
                        captured.update(path=path, editable=editable)
                        or type("Dlg", (), {"exec": lambda self: None})()):
                win.do_create_file()
            self.assertTrue(os.path.isfile(
                os.path.join(self.tmp, "новый-файл.txt")))
            self.assertEqual(captured.get("editable"), True)
        finally:
            win.close()


class MountsTests(unittest.TestCase):
    FIXTURE = {
        "blockdevices": [
            {"name": "nvme0n1", "path": "/dev/nvme0n1", "size": "500G",
             "rm": False, "mountpoint": None, "label": None, "type": "disk",
             "children": [
                 {"name": "nvme0n1p2", "path": "/dev/nvme0n1p2", "size": "499G",
                  "rm": False, "mountpoint": "/", "label": "fedora", "type": "part"},
             ]},
            {"name": "sdb", "path": "/dev/sdb", "size": "14G",
             "rm": True, "mountpoint": None, "label": None, "type": "disk",
             "children": [
                 {"name": "sdb1", "path": "/dev/sdb1", "size": "14G",
                  "rm": True, "mountpoint": ["/media/mike/USB"], "label": "USB",
                  "type": "part"},
             ]},
            {"name": "loop0", "path": "/dev/loop0", "size": "0",
             "rm": False, "mountpoint": None, "label": None, "type": "loop"},
        ]
    }

    def test_parse_and_filter(self):
        orig = mounts.subprocess.run

        def fake_run(*_a, **_kw):
            return unittest.mock.MagicMock(
                returncode=0, stdout=json_m.dumps(self.FIXTURE))

        with unittest.mock.patch.object(mounts.subprocess, "run", fake_run):
            devices = mounts.block_devices()
        self.assertEqual([d["name"] for d in devices], ["nvme0n1p2", "sdb1"])
        usb = next(d for d in devices if d["name"] == "sdb1")
        self.assertTrue(usb["removable"])
        self.assertEqual(usb["mountpoint"], "/media/mike/USB")
        self.assertEqual(usb["label"], "USB")

    def test_mount_missing_tool(self):
        with unittest.mock.patch.object(mounts.shutil, "which", return_value=None):
            ok, message = mounts.mount("/dev/sdX")
        self.assertFalse(ok)
        self.assertIn("udisksctl", message)


if __name__ == "__main__":
    unittest.main()


class BrandThemeTests(unittest.TestCase):
    """Фирменная тема Iustitia: применение и откат."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_apply_and_revert(self):
        from sphaera_commander import theme

        default = self.app.style().standardPalette()
        base_before = self.app.palette().color(
            QPalette.ColorRole.Window).name()
        theme.apply_brand_theme(self.app)
        window = self.app.palette().color(QPalette.ColorRole.Window).name()
        self.assertEqual(window, theme.GRAPHITE_925)
        self.assertEqual(
            self.app.palette().color(QPalette.ColorRole.Highlight).name(),
            theme.TEAL)
        self.assertIn("QMenuBar", self.app.styleSheet())
        theme.revert_theme(self.app)
        self.assertEqual(self.app.styleSheet(), "")
        # палитра вернулась к системной (может совпадать с исходной — тогда
        # сравниваем с системной, а не с сохранённой до применения)
        self.assertEqual(
            self.app.palette().color(QPalette.ColorRole.Window).name(),
            default.color(QPalette.ColorRole.Window).name())
        _ = base_before

    def test_icon_assets_present_and_valid(self):
        from pathlib import Path

        from PySide6.QtGui import QImage

        base = Path(__file__).parent.parent / "sphaera_commander" / "assets"
        for name in ("icon-128.png", "icon-256.png", "logo.png"):
            img = QImage(str(base / name))
            self.assertFalse(img.isNull(), name)
        self.assertTrue((base / "icon.svg").exists())  # векторный источник

    def test_brand_constants_match_identity(self):
        from sphaera_commander import theme

        self.assertEqual(theme.TEAL, "#2f8f8b")
        self.assertEqual(theme.BRONZE, "#a6784f")
        self.assertEqual(theme.PARCHMENT, "#f4efe6")
        self.assertEqual(theme.GRAPHITE_950, "#090b0c")


if __name__ == "__main__":
    unittest.main()
