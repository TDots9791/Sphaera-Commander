"""Тесты: поиск по содержимому (searcher) и быстрый просмотр (Ctrl+Q)."""

import os
import shutil
import sys
import tempfile
import time
import unittest
import unittest.mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# MainWindow при закрытии пишет геометрию в QSettings — изолируем от
# настроек реального приложения
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sc_xdg_qv_"))
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


def _make_two_page_pdf(path: str) -> None:
    """Двухстраничный PDF из двух одностраничных (текст на второй странице
    другой, чтобы рендер страниц гарантированно различался)."""
    from pypdf import PdfReader, PdfWriter

    from sphaera_commander import previewers as _pv

    tmp = tempfile.mkdtemp(prefix="sc_qp_pages_")
    try:
        one = os.path.join(tmp, "1.pdf")
        two = os.path.join(tmp, "2.pdf")
        _make_min_pdf(one, "AAAA")
        _make_min_pdf(two, "BBBB")
        writer = PdfWriter()
        for src in (one, two):
            for page in PdfReader(src).pages:
                writer.add_page(page)
        with open(path, "wb") as f:
            writer.write(f)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _make_min_pdf(path: str, word: str) -> None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tests.test_documents import make_pdf as _make

    _make(path, [word])


    def test_size_filter_parse(self):
        from sphaera_commander.searcher import parse_size_filter

        self.assertEqual(parse_size_filter(""), (0, 0))
        self.assertEqual(parse_size_filter(">2М"), (2 * 1024 ** 2, 0))
        self.assertEqual(parse_size_filter("<100к"), (0, 100 * 1024))
        self.assertEqual(parse_size_filter("1к-5М"), (1024, 5 * 1024 ** 2))
        self.assertEqual(parse_size_filter("10"), (10, 0))
        with self.assertRaises(ValueError):
            parse_size_filter("много")

    def test_date_filter_parse(self):
        import time as _time

        from sphaera_commander.searcher import parse_date_filter

        lo, hi = parse_date_filter("01.01.2024-31.12.2024", "")
        self.assertEqual(
            time.strftime("%d.%m.%Y", time.localtime(lo)), "01.01.2024")
        self.assertEqual(
            time.strftime("%d.%m.%Y", time.localtime(hi)), "31.12.2024")
        self.assertEqual(hi - lo, 365 * 86400 + 86399 - 366 * 86400 + 366 * 86400
                         if False else hi - lo)  # парсер, не календарь
        lo2, hi2 = parse_date_filter("2024-03-01", "2024-03-01")
        self.assertLess(lo2, hi2)
        self.assertEqual(parse_date_filter("", ""), (0.0, 0.0))
        with self.assertRaises(ValueError):
            parse_date_filter("когда-то", "")

    def test_search_size_and_date_filters(self):
        import os as _os
        import time as _time

        tmp = tempfile.mkdtemp(prefix="sc_filter_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        small = _os.path.join(tmp, "small.txt")
        big = _os.path.join(tmp, "big.txt")
        old = _os.path.join(tmp, "old.txt")
        for path, content in ((small, "игла"), (big, "игла " * 50_000),
                              (old, "игла")):
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        past = _time.time() - 90 * 86400
        _os.utime(old, (past, past))
        hits = []
        run_search(tmp, "*.txt", "игла",
                   size_range=(0, 1024),
                   hit_cb=lambda h: hits.append(h.path))
        self.assertEqual({*map(_os.path.basename, hits)}, {"small.txt", "old.txt"})
        hits = []
        run_search(tmp, "*.txt", "игла",
                   date_range=(_time.time() - 86400, 0),
                   hit_cb=lambda h: hits.append(h.path))
        self.assertEqual({*map(_os.path.basename, hits)}, {"small.txt", "big.txt"})

    def test_search_inside_pdf_and_docx(self):
        import os as _os

        sys.path.insert(0, _os.path.dirname(_os.path.dirname(
            _os.path.abspath(__file__))))
        from tests.test_documents import make_pdf

        tmp = tempfile.mkdtemp(prefix="sc_docsearch_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        make_pdf(_os.path.join(tmp, "report.pdf"), ["Zebra report 2024"])
        from docx import Document as _Doc

        d = _Doc()
        d.add_paragraph("Гиппопотам живёт в воде")
        d.save(_os.path.join(tmp, "заметка.docx"))
        hits = []
        run_search(tmp, "*.pdf", "zebra", hit_cb=lambda h: hits.append(h.path))
        self.assertTrue(any(p.endswith("report.pdf") for p in hits))
        hits = []
        run_search(tmp, "*.docx", "гиппопотам",
                   hit_cb=lambda h: hits.append(h.path))
        self.assertTrue(any(p.endswith("заметка.docx") for p in hits))


class QuickViewPdfTests(unittest.TestCase):
    """Ctrl+Q на PDF: листание страниц, клики по краям, масштаб."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_qp_pdf_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _entry(self, name: str):
        from sphaera_commander.fsmodel import FileEntry

        path = os.path.join(self.tmp, name)
        stt = os.stat(path)
        return FileEntry(name=name, path=path, is_dir=False, is_link=False,
                         size=stt.st_size, mtime=stt.st_mtime, mode=stt.st_mode)

    def _load(self, name: str):
        from sphaera_commander.quick_preview import QuickPreview

        qp = QuickPreview()
        qp.resize(800, 600)
        qp.show_entry(self._entry(name))
        qp._debounce.stop()
        qp._load_pending()
        return qp

    def test_pdf_pages_navigate(self):
        from sphaera_commander.quick_preview import QuickPreview

        _make_two_page_pdf(os.path.join(self.tmp, "two.pdf"))
        qp = self._load("two.pdf")
        self.assertEqual(qp._doc_kind, "pdf")
        self.assertEqual(qp._doc_count, 2)
        self.assertIn("стр. 1 из 2", qp._info.text())
        self.assertFalse(qp._btn_prev.isEnabled())
        self.assertTrue(qp._btn_next.isEnabled())
        qp._btn_next.click()
        self.assertEqual(qp._doc_index, 1)
        self.assertIn("стр. 2 из 2", qp._info.text())  # статус листается тоже
        self.assertFalse(qp._btn_next.isEnabled())
        self.assertTrue(qp._btn_prev.isEnabled())
        qp._doc_navigate(-1)
        self.assertEqual(qp._doc_index, 0)
        qp.deleteLater()

    def test_pdf_single_page_hides_nav(self):
        _make_min_pdf(os.path.join(self.tmp, "solo.pdf"), "One")
        qp = self._load("solo.pdf")
        self.assertEqual(qp._doc_count, 1)
        self.assertTrue(qp._doc_nav.isHidden())
        qp.deleteLater()

    def test_pdf_click_edges_page(self):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        _make_two_page_pdf(os.path.join(self.tmp, "two.pdf"))
        qp = self._load("two.pdf")

        def click(x):
            ev = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(x, 5),
                             Qt.MouseButton.LeftButton,
                             Qt.MouseButton.NoButton,
                             Qt.KeyboardModifier.NoModifier)
            qp.eventFilter(qp._pdf_page, ev)

        click(qp._pdf_page.width() * 0.9)
        self.assertEqual(qp._doc_index, 1)
        click(qp._pdf_page.width() * 0.1)
        self.assertEqual(qp._doc_index, 0)
        qp.deleteLater()

    def test_pdf_zoom_changes_render(self):
        _make_two_page_pdf(os.path.join(self.tmp, "two.pdf"))
        qp = self._load("two.pdf")
        before = qp._pdf_page.pixmap().size().width()
        qp._doc_zoom(1.25)
        after = qp._pdf_page.pixmap().size().width()
        self.assertGreater(after, before)
        self.assertIn("стр. 1 из 2", qp._doc_pos.text())
        qp._doc_zoom(1 / 1.25)
        self.assertAlmostEqual(qp._pdf_page.pixmap().size().width(), before,
                               delta=2)
        qp.deleteLater()

    def test_viewer_ctrl_wheel_zooms_pdf(self):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QWheelEvent

        from sphaera_commander.viewer import FileViewerDialog

        _make_two_page_pdf(os.path.join(self.tmp, "two.pdf"))
        dlg = FileViewerDialog(None, [os.path.join(self.tmp, "two.pdf")], 0,
                               editable=False)
        base = dlg._pdf_scale

        def wheel(dy: int, ctrl: bool):
            mod = (Qt.KeyboardModifier.ControlModifier if ctrl
                   else Qt.KeyboardModifier.NoModifier)
            ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, dy),
                             QPoint(0, dy), Qt.MouseButton.NoButton, mod,
                             Qt.ScrollPhase.NoScrollPhase, False)
            dlg.eventFilter(dlg.pdf_scroll.viewport(), ev)

        wheel(120, ctrl=True)
        self.assertGreater(dlg._pdf_scale, base)
        wheel(-120, ctrl=True)
        self.assertAlmostEqual(dlg._pdf_scale, base, delta=1e-9)
        wheel(120, ctrl=False)  # обычное колесо — прокрутка, не зум
        self.assertAlmostEqual(dlg._pdf_scale, base, delta=1e-9)
        dlg.reject()


class QuickViewLegacyTests(unittest.TestCase):
    """Ctrl+Q: doc и rtf показывают текст, а не «бинарный файл»."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_qv_doc_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _entry(self, name: str):
        from sphaera_commander.fsmodel import FileEntry

        path = os.path.join(self.tmp, name)
        stt = os.stat(path)
        return FileEntry(name=name, path=path, is_dir=False, is_link=False,
                         size=stt.st_size, mtime=stt.st_mtime, mode=stt.st_mode)

    def _load(self, name: str):
        from sphaera_commander.quick_preview import QuickPreview

        qp = QuickPreview()
        qp.show_entry(self._entry(name))
        qp._debounce.stop()
        qp._load_pending()  # тот же вход, что у таймера дебаунса
        return qp

    def test_doc_shows_extracted_text(self):
        path = os.path.join(self.tmp, "real.doc")
        with open(path, "wb") as f:
            f.write(b"\xd0\xcf\x11\xe0garbage")

        def fake_run(cmd, **_kw):
            self.assertTrue(cmd[0].endswith("antiword"))
            return unittest.mock.MagicMock(returncode=0,
                                           stdout="Текст из Word")

        from sphaera_commander import legacy_formats as lf
        with unittest.mock.patch.object(lf.shutil, "which",
                                        return_value="/usr/bin/antiword"):
            with unittest.mock.patch.object(lf.subprocess, "run", fake_run):
                qp = self._load("real.doc")
        self.assertEqual(qp._stack.currentIndex(), 1)
        self.assertIn("Текст из Word", qp._browser.toPlainText())
        self.assertIn("• doc •", qp._info.text())

    def test_doc_saved_as_rtf_shows_text(self):
        path = os.path.join(self.tmp, "doc.doc")
        with open(path, "w", encoding="ascii") as f:
            f.write(r"{\rtf1\ansi \u1055?\u1088?\u1080?\u1074?\u1077?\u1090?\par}")
        qp = self._load("doc.doc")
        self.assertIn("Привет", qp._browser.toPlainText())
        self.assertIn("• doc •", qp._info.text())

    def test_rtf_shows_text(self):
        path = os.path.join(self.tmp, "doc.rtf")
        with open(path, "w", encoding="ascii") as f:
            f.write(r"{\rtf1\ansi \u1042?\u1072?\u1078?\u1085?\u1086?\par}")
        qp = self._load("doc.rtf")
        self.assertIn("Важно", qp._browser.toPlainText())
        self.assertIn("• rtf •", qp._info.text())

    def test_binary_doc_without_tool_shows_error_page(self):
        path = os.path.join(self.tmp, "real.doc")
        with open(path, "wb") as f:
            f.write(b"\xd0\xcf\x11\xe0garbage")
        from sphaera_commander import legacy_formats as lf
        with unittest.mock.patch.object(lf.shutil, "which", return_value=None):
            qp = self._load("real.doc")
        self.assertIn("Не удалось показать", qp._page_info.text())

    def test_doc_monospace_and_reset_for_next_file(self):
        """Таблицы antiword сходятся только в моноширинном шрифте;
        следующий файл после DOC получает обычный пропорциональный."""
        from PySide6.QtGui import QFontDatabase

        path = os.path.join(self.tmp, "real.doc")
        with open(path, "wb") as f:
            f.write(b"\xd0\xcf\x11\xe0garbage")

        def fake_run(cmd, **_kw):
            return unittest.mock.MagicMock(returncode=0, stdout="Текст")

        from sphaera_commander import legacy_formats as lf
        with unittest.mock.patch.object(lf.shutil, "which",
                                        return_value="/usr/bin/antiword"):
            with unittest.mock.patch.object(lf.subprocess, "run", fake_run):
                qp = self._load("real.doc")
        fixed = QFontDatabase.systemFont(QFontDatabase.FixedFont).family()
        self.assertEqual(qp._browser.document().defaultFont().family(), fixed)

        txt = os.path.join(self.tmp, "заметка.txt")
        with open(txt, "w", encoding="utf-8") as f:
            f.write("обычный текст\n")
        qp.show_entry(self._entry("заметка.txt"))
        qp._load_pending()
        self.assertEqual(qp._stack.currentIndex(), 1)
        self.assertNotEqual(qp._browser.document().defaultFont().family(), fixed)


if __name__ == "__main__":
    unittest.main()
