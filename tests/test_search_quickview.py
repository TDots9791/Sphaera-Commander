"""Тесты: поиск по содержимому (searcher) и быстрый просмотр (Ctrl+Q)."""

import os
import shutil
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

from sphaera_commander.searcher import Hit, parse_masks, run_search  # noqa: E402


def write(path: str, text: str, encoding: str = "utf-8") -> None:
    with open(path, "w", encoding=encoding) as f:
        f.write(text)


class SearcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_search_")
        self.root = os.path.join(self.tmp, "docs")
        os.makedirs(os.path.join(self.root, "sub"))
        write(os.path.join(self.root, "a.txt"), "привет мир\nещё строка\nПРИВЕТ верх")
        write(os.path.join(self.root, "b.log"), "ничего интересного")
        write(os.path.join(self.root, "c.py"), "def hello(): pass\n# привет\n")
        write(os.path.join(self.root, "sub", "d.md"), "# заголовок привет\n")
        write(os.path.join(self.root, "cp.txt"), "привет из cp1251", "cp1251")
        write(os.path.join(self.root, "bin.dat"), b"\x00\x01\x02binary\x00".decode("latin-1"))
        write(os.path.join(self.root, "sub", "skipme.log"), "привет но не по маске")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hits(self, **kwargs) -> list[Hit]:
        collected = []
        run_search(hit_cb=collected.append, **kwargs)
        return collected

    def test_masks_parse(self):
        self.assertEqual(parse_masks(""), ["*"])
        self.assertEqual(parse_masks("*.py;*.txt"), ["*.py", "*.txt"])
        self.assertEqual(parse_masks("*.py *.sh"), ["*.py", "*.sh"])

    def test_content_search_with_masks(self):
        hits = self.hits(root=self.root, mask_text="*.txt", needle="привет")
        files = {os.path.basename(h.path) for h in hits}
        self.assertEqual(files, {"a.txt", "cp.txt"})  # остальное мимо маски

    def test_recursive_and_multiple_exts(self):
        hits = self.hits(root=self.root, mask_text="*.py;*.md",
                         needle="привет", recursive=True)
        self.assertEqual({os.path.basename(h.path) for h in hits}, {"c.py", "d.md"})

    def test_case_sensitivity(self):
        hits_ci = self.hits(root=self.root, mask_text="a.txt", needle="ПРИВЕТ")
        self.assertEqual(len(hits_ci), 2)  # обе строки
        hits_cs = self.hits(root=self.root, mask_text="a.txt", needle="ПРИВЕТ",
                            case_sensitive=True)
        self.assertEqual(len(hits_cs), 1)  # только "ПРИВЕТ верх"
        self.assertEqual(hits_cs[0].line_no, 3)

    def test_line_numbers_and_text(self):
        hits = self.hits(root=self.root, mask_text="a.txt", needle="мир")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].line_no, 1)
        self.assertEqual(hits[0].text, "привет мир")

    def test_regex(self):
        hits = self.hits(root=self.root, mask_text="c.py", needle=r"def \w+\(",
                         use_regex=True)
        self.assertEqual(len(hits), 1)
        self.assertIn("def hello", hits[0].text)

    def test_binary_skipped(self):
        stats = run_search(root=self.root, mask_text="*.dat", needle="binary")
        self.assertEqual(stats.skipped_binary, 1)
        self.assertEqual(stats.hits, 0)

    def test_mask_only_mode(self):
        stats = run_search(root=self.root, mask_text="*.log", needle="",
                           recursive=False)
        self.assertEqual(stats.files_matched, 1)
        self.assertEqual(stats.hits, 1)

    def test_no_recursive(self):
        hits = self.hits(root=self.root, mask_text="*.md", needle="привет",
                         recursive=False)
        self.assertEqual(hits, [])

    def test_cancel(self):
        stats = run_search(root=self.root, mask_text="*", needle="привет",
                           is_cancelled=lambda: True)
        self.assertTrue(stats.cancelled)
        self.assertEqual(stats.files_seen, 0)

    def test_missing_root_no_crash(self):
        stats = run_search(root=os.path.join(self.tmp, "нет-такого"),
                           mask_text="*", needle="x")
        self.assertEqual(stats.files_seen, 0)


class QuickViewTests(unittest.TestCase):
    """Ctrl+Q: неактивная панель показывает содержимое файла под курсором."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_qv_")
        write(os.path.join(self.tmp, "заметка.txt"),
              "строка для предпросмотра\n" * 20)
        from sphaera_commander import config
        from sphaera_commander.app import MainWindow

        config.qsettings().remove("view/quick_view")  # тесты не зависят от памяти
        self.win = MainWindow()
        self.win.left.cd(self.tmp)
        self.win.left.wait_loaded()
        self.win.show()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.win.close()

    def _cursor_to(self, name: str) -> None:
        w = self.win
        w._set_active(w.left)
        w.left.view.set_current_row(w.left.model.row_of_name(name))

    def _wait_preview(self, dlg_widget, timeout_s: float = 3.0) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
            if dlg_widget._stack.currentIndex() != 0 or "чтение" not in "":
                break

    def test_toggle_places_preview_on_inactive_side(self):
        w = self.win
        self.assertFalse(w.quick_view)
        w.toggle_quick_view()
        self.assertTrue(w.quick_view)
        self.assertIs(w.left_stack.currentWidget(), w.left)   # активная — панель
        self.assertIs(w.right_stack.currentWidget(), w.quick_preview)
        w._set_active(w.right)
        self.assertIs(w.right_stack.currentWidget(), w.right)  # стороны поменялись
        self.assertIs(w.left_stack.currentWidget(), w.quick_preview)
        w.toggle_quick_view()
        self.assertIs(w.left_stack.currentWidget(), w.left)
        self.assertIs(w.right_stack.currentWidget(), w.right)

    def test_preview_shows_text_under_cursor(self):
        w = self.win
        w.toggle_quick_view()
        self._cursor_to("заметка.txt")
        deadline = time.time() + 3
        preview = w.quick_preview
        while time.time() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
            if preview._stack.currentIndex() == 1:
                break
        self.assertEqual(preview._stack.currentIndex(), 1)
        self.assertIn("строка для предпросмотра", preview._browser.toPlainText())
        self.assertIn("utf-8", preview._info.text())

    def test_cursor_move_updates_preview(self):
        w = self.win
        write(os.path.join(self.tmp, "другой.txt"), "совсем другой текст\n")
        w.left.refresh()
        w.left.wait_loaded()
        w.toggle_quick_view()
        self._cursor_to("заметка.txt")
        self._wait_preview_text(w, "строка для предпросмотра")
        self._cursor_to("другой.txt")
        self._wait_preview_text(w, "совсем другой текст")

    @staticmethod
    def _wait_preview_text(w, needle: str, timeout_s: float = 3.0) -> None:
        """Дебаунс 200 мс + очередь событий: ждём появления текста в предпросмотре."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            QApplication.processEvents()
            time.sleep(0.02)
            if needle in w.quick_preview._browser.toPlainText():
                return
        raise AssertionError(f"предпросмотр не показал {needle!r}")


if __name__ == "__main__":
    unittest.main()
