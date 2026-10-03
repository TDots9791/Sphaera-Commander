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
