"""Тесты форматных документов: pdf/docx/xlsx/pptx/csv/html/xml/fb2/epub."""

import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from html import escape

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from sphaera_commander import previewers as pv  # noqa: E402


def make_pdf(path: str, lines: list[str]) -> None:
    """Минимальный одностраничный PDF с текстом (Helvetica)."""
    parts = [b"%PDF-1.4\n"]
    offsets: list[int] = []

    def obj(num: int, body: bytes) -> None:
        offsets.append(len(b"".join(parts)))
        parts.append(f"{num} 0 obj\n".encode() + body + b"\nendobj\n")

    stream = "BT /F1 18 Tf 20 100 Td (" + ") Tj 0 -20 Td (".join(lines) + ") Tj ET\n"
    obj(1, b"<</Type/Catalog/Pages 2 0 R>>")
    obj(2, b"<</Type/Pages/Kids[3 0 R]/Count 1>>")
    obj(3, b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 400 200]/Contents 4 0 R"
           b"/Resources<</Font<</F1 5 0 R>>>>>>")
    obj(4, b"<</Length " + str(len(stream.encode())).encode() + b">>stream\n"
           + stream.encode() + b"endstream")
    obj(5, b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    xref_pos = len(b"".join(parts))
    xref = [b"xref", b"0 6", b"0000000000 65535 f "]
    for off in offsets:
        xref.append(f"{off:010d} 00000 n ".encode())
    parts.append(b"\n".join(xref) +
                 f"\ntrailer<</Root 1 0 R/Size 6>>\nstartxref\n{xref_pos}\n%%EOF".encode())
    with open(path, "wb") as f:
        f.write(b"".join(parts))


def make_docx(path: str, paragraphs: list[str]) -> None:
    from docx import Document

    d = Document()
    for text in paragraphs:
        d.add_paragraph(text)
    d.save(path)


def make_xlsx(path: str) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Товары"
    ws.append(("Имя", "Цена"))
    ws.append(("Хлеб", 50))
    ws2 = wb.create_sheet("Итоги")
    ws2.append(("итог", 140))
    wb.save(path)


def make_pptx(path: str) -> None:
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])  # title+content
    slide.shapes.title.text = "Заголовок"
    slide.placeholders[1].text = "пункт один"
    prs.save(path)


def make_epub(path: str) -> None:
    chapter1 = ("<?xml version='1.0'?><html><head><title>Глава 1</title></head>"
                "<body><h1>Глава 1</h1><p>начало книги</p></body></html>")
    chapter2 = ("<?xml version='1.0'?><html><head><title>Глава 2</title></head>"
                "<body><h1>Глава 2</h1><p>конец книги</p></body></html>")
    container = ("<?xml version='1.0'?>"
                 "<container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
                 "<rootfiles><rootfile full-path='OEBPS/content.opf' "
                 "media-type='application/oebps-package+xml'/></rootfiles></container>")
    opf = ("<?xml version='1.0'?>"
           "<package xmlns='http://www.idpf.org/2007/opf' version='2.0' unique-identifier='id'>"
           "<manifest>"
           "<item id='c1' href='c1.xhtml' media-type='application/xhtml+xml'/>"
           "<item id='c2' href='c2.xhtml' media-type='application/xhtml+xml'/>"
           "</manifest>"
           "<spine><itemref idref='c1'/><itemref idref='c2'/></spine>"
           "</package>")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/c1.xhtml", chapter1)
        zf.writestr("OEBPS/c2.xhtml", chapter2)


def make_fb2(path: str) -> None:
    fb2 = f"""<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><book-title>Моя книга</book-title></title-info></description>
<body>
<section><title><p>Часть первая</p></title>
<p>Обычный абзац с <emphasis>выделением</emphasis>.</p>
<empty-line/>
<p>Второй абзац.</p></section>
</body></FictionBook>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(fb2)


class ViewerBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_docs_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class PdfTests(ViewerBase):
    def setUp(self):
        super().setUp()
        self.pdf = os.path.join(self.tmp, "doc.pdf")
        # PDF-строки в Type1/Helvetica — Latin-1, поэтому ASCII
        make_pdf(self.pdf, ["First line", "Second line"])

    def test_render_and_text_layer(self):
        self.assertEqual(pv.pdf_page_count(self.pdf), 1)
        data, w, h, stride = pv.pdf_render(self.pdf, 0, scale=2.0)
        self.assertGreater(w, 100)
        self.assertEqual(stride, w * 3)
        text = pv.pdf_page_text(self.pdf, 0)
        self.assertIn("First line", text)
        self.assertIn("Second line", text)

    def test_rotate_and_delete(self):
        # двухстраничный документ: из двух одностраничных
        second = os.path.join(self.tmp, "second.pdf")
        make_pdf(second, ["Other page"])
        from pypdf import PdfReader, PdfWriter

        writer = PdfWriter()
        for src in (self.pdf, second):
            for page in PdfReader(src).pages:
                writer.add_page(page)
        two = os.path.join(self.tmp, "two.pdf")
        with open(two, "wb") as f:
            writer.write(f)
        self.assertEqual(pv.pdf_page_count(two), 2)
        pv.pdf_rotate_pages(two, [1], 90)
        self.assertEqual(pv.pdf_page_count(two), 2)
        self.assertIn("First line", pv.pdf_page_text(two, 0))
        pv.pdf_delete_pages(two, [0])
        self.assertEqual(pv.pdf_page_count(two), 1)
        self.assertIn("Other page", pv.pdf_page_text(two, 0))

    def test_export_ranges(self):
        make_pdf(os.path.join(self.tmp, "many.pdf"), ["x"])  # 1 страница
        out = os.path.join(self.tmp, "out.pdf")
        pv.pdf_export_pages(self.pdf, [0], out)
        self.assertEqual(pv.pdf_page_count(out), 1)

    def test_parse_ranges(self):
        self.assertEqual(pv.parse_ranges("1-3,5", 10), [0, 1, 2, 4])
        self.assertEqual(pv.parse_ranges("2", 3), [1])
        with self.assertRaises(ValueError):
            pv.parse_ranges("0", 5)
        with self.assertRaises(ValueError):
            pv.parse_ranges("", 5)
        with self.assertRaises(ValueError):
            pv.parse_ranges("3-1", 5)

    def test_pdf_viewer_shows_page_and_text(self):
        from sphaera_commander.viewer import FileViewerDialog

        dlg = FileViewerDialog(None, [self.pdf], 0, editable=False)
        self.assertEqual(dlg.kind, "pdf")
        self.assertEqual(dlg.stack.currentIndex(), 3)
        self.assertIn("стр. 1 из 1", dlg.lbl_pdf_page.text())
        self.assertFalse(dlg.pdf_label.pixmap() is None
                         or dlg.pdf_label.pixmap().isNull())
        self.assertTrue(dlg.btn_pdf_rot_left.isHidden())  # не-editable
        dlg.btn_pdf_text.setChecked(True)
        self.assertIn("First line", dlg.pdf_text.toPlainText())
        dlg.reject()

    def test_pdf_edit_buttons_in_editor_mode(self):
        from sphaera_commander.viewer import FileViewerDialog

        dlg = FileViewerDialog(None, [self.pdf], 0, editable=True)
        self.assertFalse(dlg.btn_pdf_delete.isHidden())
        dlg._pdf_rotate(90)  # выполняется без ошибок
        dlg.reject()


class DocxTests(ViewerBase):
    def test_paragraph_roundtrip(self):
        path = os.path.join(self.tmp, "d.docx")
        make_docx(path, ["Первый абзац", "Второй абзац"])
        self.assertEqual(pv.docx_paragraphs(path), ["Первый абзац", "Второй абзац"])
        pv.docx_save_paragraphs(path, ["Изменённый", "Второй абзац", "Новый"])
        self.assertEqual(pv.docx_paragraphs(path),
                         ["Изменённый", "Второй абзац", "Новый"])

    def test_html_preview(self):
        path = os.path.join(self.tmp, "d.docx")
        make_docx(path, ["Жирный текст"])
        html = pv.docx_to_html(path)
        self.assertIn("Жирный текст", html)

    def test_viewer_modes(self):
        from sphaera_commander.viewer import FileViewerDialog

        path = os.path.join(self.tmp, "d.docx")
        make_docx(path, ["Абзац один", "Абзац два"])
        dlg = FileViewerDialog(None, [path], 0, editable=False)
        self.assertEqual(dlg.kind, "docx")
        self.assertEqual(dlg.stack.currentIndex(), 1)  # html-просмотр
        dlg.reject()
        dlg = FileViewerDialog(None, [path], 0, editable=True)
        self.assertEqual(dlg.stack.currentIndex(), 0)
        self.assertIn("Абзац один", dlg.text_edit.toPlainText())
        dlg.text_edit.setPlainText("Абзац один")
        dlg._save()
        self.assertEqual(pv.docx_paragraphs(path), ["Абзац один"])
        dlg.reject()


class SheetTests(ViewerBase):
    def test_xlsx_sheets(self):
        path = os.path.join(self.tmp, "d.xlsx")
        make_xlsx(path)
        sheets = pv.xlsx_sheets(path)
        self.assertEqual([name for name, _ in sheets], ["Товары", "Итоги"])
        self.assertEqual(sheets[0][1][1], ["Хлеб", "50"])

    def test_xlsx_viewer_grid(self):
        from sphaera_commander.viewer import FileViewerDialog

        path = os.path.join(self.tmp, "d.xlsx")
        make_xlsx(path)
        dlg = FileViewerDialog(None, [path], 0, editable=False)
        self.assertEqual(dlg.kind, "xlsx")
        self.assertEqual(dlg.stack.currentIndex(), 4)
        self._wait_grid(dlg)
        self.assertEqual(dlg.sheet_model.rowCount(), 2)
        self.assertEqual(dlg.sheet_model.index(1, 0).data(), "Хлеб")
        dlg.sheet_combo.setCurrentIndex(1)
        self._wait_grid(dlg)
        self.assertEqual(dlg.sheet_model.index(0, 0).data(), "итог")
        dlg.reject()

    @staticmethod
    def _wait_grid(dlg, timeout_s: float = 10.0) -> None:
        import time as _time

        deadline = _time.time() + timeout_s
        while _time.time() < deadline and "чтение" in dlg.lbl_info.text():
            QApplication.processEvents()
            _time.sleep(0.01)

    def test_csv_grid_and_viewer(self):
        path = os.path.join(self.tmp, "d.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("a;b\n1;2\n3;4\n")
        delim, rows = pv.csv_grid(open(path, encoding="utf-8").read())
        self.assertEqual(delim, ";")
        self.assertEqual(rows, [["a", "b"], ["1", "2"], ["3", "4"]])
        from sphaera_commander.viewer import FileViewerDialog

        dlg = FileViewerDialog(None, [path], 0, editable=False)
        self.assertEqual(dlg.kind, "csv")
        SheetTests._wait_grid(dlg)
        self.assertEqual(dlg.sheet_model.index(0, 1).data(), "b")
        self.assertIn(";", dlg.lbl_info.text())
        dlg.reject()


class PptxTests(ViewerBase):
    def test_slides_extracted(self):
        path = os.path.join(self.tmp, "d.pptx")
        make_pptx(path)
        slides = pv.pptx_slides(path)
        self.assertEqual(len(slides), 1)
        self.assertEqual(slides[0][0], 1)
        self.assertIn("Заголовок", slides[0][1])

    def test_pptx_viewer_renders_slide(self):
        from sphaera_commander.viewer import FileViewerDialog

        path = os.path.join(self.tmp, "d.pptx")
        make_pptx(path)
        dlg = FileViewerDialog(None, [path], 0, editable=False)
        self.assertEqual(dlg.kind, "pptx")
        self.assertEqual(dlg.stack.currentIndex(), 3)  # страница-просмотрщик
        self.assertIn("слайд 1 из 1", dlg.lbl_pdf_page.text())
        self.assertFalse(dlg.pdf_label.pixmap().isNull())
        # слайд не пустой: заголовок нарисован (есть тёмные пиксели)
        img = dlg.pdf_label.pixmap().toImage()
        dark = sum(1 for x in range(0, img.width(), 4)
                   for y in range(0, img.height(), 4)
                   if img.pixelColor(x, y).lightness() < 100)
        self.assertGreater(dark, 5)
        dlg.btn_pdf_text.setChecked(True)
        self.assertIn("Заголовок", dlg.pdf_text.toPlainText())
        dlg.reject()


class WebBookTests(ViewerBase):
    def test_epub_chapters(self):
        path = os.path.join(self.tmp, "d.epub")
        make_epub(path)
        chapters = pv.epub_chapters(path)
        self.assertEqual([t for t, _, _ in chapters], ["Глава 1", "Глава 2"])
        self.assertIn("начало книги", chapters[0][1])
        self.assertIsNone(chapters[0][2])

        # с распаковкой: base — реальный каталог для картинок
        extract = os.path.join(self.tmp, "ex")
        chapters = pv.epub_chapters(path, extract)
        self.assertTrue(os.path.isdir(chapters[0][2]))
        self.assertTrue(chapters[0][2].startswith(extract))

    def test_epub_viewer(self):
        from sphaera_commander.viewer import FileViewerDialog

        path = os.path.join(self.tmp, "d.epub")
        make_epub(path)
        dlg = FileViewerDialog(None, [path], 0, editable=False)
        self.assertEqual(dlg.kind, "epub")
        self.assertEqual(dlg.doc_combo.count(), 2)
        self.assertIn("начало книги", dlg.preview.document().toPlainText())
        dlg.doc_combo.setCurrentIndex(1)
        self.assertIn("конец книги", dlg.preview.document().toPlainText())
        dlg.reject()

    def test_fb2_html_and_viewer(self):
        path = os.path.join(self.tmp, "d.fb2")
        make_fb2(path)
        title, html = pv.fb2_html(path)
        self.assertEqual(title, "Моя книга")
        self.assertIn("Часть первая", html)
        self.assertIn("выделением", html)
        from sphaera_commander.viewer import FileViewerDialog

        dlg = FileViewerDialog(None, [path], 0, editable=False)
        self.assertEqual(dlg.kind, "fb2")
        self.assertIn("Моя книга", dlg.preview.document().toPlainText())
        dlg.reject()

    def test_html_viewer(self):
        from sphaera_commander.viewer import FileViewerDialog

        path = os.path.join(self.tmp, "d.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write("<html><body><h1>Привет</h1><p>HTML</p></body></html>")
        dlg = FileViewerDialog(None, [path], 0, editable=False)
        self.assertEqual(dlg.kind, "html")
        self.assertIn("Привет", dlg.preview.document().toPlainText())
        dlg.reject()

    def test_xml_tree_and_source(self):
        path = os.path.join(self.tmp, "d.xml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("<корень атр='1'><ребёнок>значение</ребёнок></корень>")
        root_name, node = pv.xml_tree(path)
        self.assertEqual(root_name, "корень")
        self.assertEqual(node[2][0][0], "ребёнок")
        from sphaera_commander.viewer import FileViewerDialog

        dlg = FileViewerDialog(None, [path], 0, editable=False)
        self.assertEqual(dlg.kind, "xml")
        self.assertEqual(dlg.stack.currentIndex(), 0)
        dlg.btn_tree.setChecked(True)
        self.assertEqual(dlg.stack.currentIndex(), 2)
        self.assertEqual(dlg.tree.topLevelItem(0).text(0), "корень")
        self.assertEqual(dlg.tree.topLevelItem(0).child(0).text(1), "значение")
        dlg.reject()

    def test_broken_document_shows_error_not_crash(self):
        from sphaera_commander.viewer import FileViewerDialog

        path = os.path.join(self.tmp, "broken.docx")
        with open(path, "wb") as f:
            f.write(b"not a zip")
        dlg = FileViewerDialog(None, [path], 0, editable=False)
        self.assertIn("не удалось открыть", dlg.text_edit.toPlainText())
        dlg.reject()


if __name__ == "__main__":
    unittest.main()
