"""Форматные документы: pdf, docx, xlsx, pptx, csv, html, xml, fb2, epub.

Чистая логика без Qt — тестируется отдельно. Тяжёлые библиотеки импортируются
лениво, чтобы не замедлять старт приложения.
Лицензии зависимостей: pypdfium2 (Apache-2.0, движок Pdfium), pypdf (BSD),
python-docx (MIT), mammoth (BSD-2), openpyxl (MIT), python-pptx (MIT) —
свободное ПО без компонентов Adobe и без GPL/AGPL.
"""

from __future__ import annotations

import csv
import io
import os
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from html import escape

DOC_KINDS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".pptx": "pptx",
    ".csv": "csv",
    ".tsv": "csv",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
    ".xml": "xml",
    ".fb2": "fb2",
    ".epub": "epub",
}

XLSX_MAX_ROWS = 5000
XLSX_MAX_COLS = 64
GRID_MAX_ROWS = 20000
CSV_DELIMS = ",;\t|"


def document_kind(path: str) -> str | None:
    return DOC_KINDS.get(os.path.splitext(path)[1].lower())


def _atomic_replace_write(out_path: str, writer) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".sc_pdf_", suffix=".pdf",
                               dir=os.path.dirname(out_path) or ".")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            writer.write(f)
        os.replace(tmp, out_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- pdf

def pdf_page_count(path: str) -> int:
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(path) as pdf:
        return len(pdf)


def pdf_render(path: str, index: int, scale: float = 2.0) -> tuple[bytes, int, int, int]:
    """Отрисовать страницу: (BGR-данные, ширина, высота, stride)."""
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(path) as pdf:
        page = pdf[index]
        bitmap = page.render(scale=scale)
        return bytes(bitmap.buffer), bitmap.width, bitmap.height, bitmap.stride


def pdf_page_text(path: str, index: int) -> str:
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(path) as pdf:
        page = pdf[index]
        textpage = page.get_textpage()
        return textpage.get_text_range()


def pdf_rotate_pages(path: str, indices: list[int], delta: int) -> None:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(path)
    writer = PdfWriter()
    wanted = set(indices)
    for i, page in enumerate(reader.pages):
        page_obj = page
        if i in wanted:
            page_obj = page.rotate(delta)
        writer.add_page(page_obj)
    _atomic_replace_write(path, writer)


def pdf_delete_pages(path: str, indices: list[int]) -> None:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(path)
    writer = PdfWriter()
    skip = set(indices)
    for i, page in enumerate(reader.pages):
        if i not in skip:
            writer.add_page(page)
    _atomic_replace_write(path, writer)


def pdf_export_pages(path: str, indices: list[int], out_path: str) -> None:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(path)
    writer = PdfWriter()
    for i in indices:
        writer.add_page(reader.pages[i])
    with open(out_path, "wb") as f:
        writer.write(f)


def parse_ranges(text: str, maximum: int) -> list[int]:
    """«1-3,5» → [0, 1, 2, 4]; ValueError при мусоре."""
    result: list[int] = []
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            start, end = int(a) - 1, int(b) - 1
            if start < 0 or end < start:
                raise ValueError(part)
            result.extend(range(start, end + 1))
        else:
            n = int(part) - 1
            if n < 0:
                raise ValueError(part)
            result.append(n)
    if not result:
        raise ValueError("пусто")
    return sorted({n for n in result if 0 <= n < maximum})


# ---------------------------------------------------------------- docx

def docx_paragraphs(path: str) -> list[str]:
    from docx import Document

    return [p.text for p in Document(path).paragraphs]


def docx_save_paragraphs(path: str, lines: list[str]) -> None:
    """Правка по абзацам: текст абзаца заменяется, его стиль сохраняется
    (встроенное форматирование внутри изменённого абзаца теряется)."""
    from docx import Document

    document = Document(path)
    paragraphs = document.paragraphs
    for i, paragraph in enumerate(paragraphs):
        new_text = lines[i] if i < len(lines) else None
        if new_text is None:
            paragraph._element.getparent().remove(paragraph._element)
        elif paragraph.text != new_text:
            paragraph.text = new_text
    if len(lines) > len(paragraphs):
        for line in lines[len(paragraphs):]:
            document.add_paragraph(line)
    fd, tmp = tempfile.mkstemp(prefix=".sc_docx_", suffix=".docx",
                               dir=os.path.dirname(path) or ".")
    os.close(fd)
    try:
        document.save(tmp)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def docx_to_html(path: str) -> str:
    import mammoth

    with open(path, "rb") as f:
        return mammoth.convert_to_html(f).value


# ---------------------------------------------------------------- xlsx

def xlsx_sheets(path: str) -> list[tuple[str, list[list[str]]]]:
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        sheets = []
        for name in wb.sheetnames:
            ws = wb[name]
            rows: list[list[str]] = []
            for row in ws.iter_rows(values_only=True):
                if len(rows) >= XLSX_MAX_ROWS:
                    break
                rows.append(["" if v is None else str(v)
                             for v in row[:XLSX_MAX_COLS]])
            sheets.append((name, rows))
        return sheets
    finally:
        wb.close()


# ---------------------------------------------------------------- pptx

def pptx_slides(path: str) -> list[tuple[int, str]]:
    from pptx import Presentation

    prs = Presentation(path)
    slides = []
    for i, slide in enumerate(prs.slides, start=1):
        parts: list[str] = []
        title = ""
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            text = shape.text_frame.text.strip()
            if not text:
                continue
            if shape == slide.shapes.title:
                title = text
            parts.append(escape(text).replace("\n", "<br>"))
        body = "<hr>".join(parts) if parts else "<i>(пусто)</i>"
        slides.append((i, f"<b>{escape(title)}</b><hr>{body}"))
    return slides


# ---------------------------------------------------------------- csv

def csv_grid(text: str) -> tuple[str, list[list[str]]]:
    """Определить разделитель и разобрать; (разделитель, строки)."""
    head = "\n".join(text.splitlines()[:10])
    best, best_score = ",", -1
    for d in CSV_DELIMS:
        score = head.count(d)
        if score > best_score:
            best, best_score = d, score
    if best_score <= 0:
        best = ","
    rows = []
    for row in csv.reader(io.StringIO(text), delimiter=best):
        if len(rows) >= GRID_MAX_ROWS:
            break
        rows.append(row)
    return best, rows


# ---------------------------------------------------------------- fb2

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def fb2_html(path: str) -> tuple[str, str]:
    """(название книги, html основных разделов)."""
    tree = ET.parse(path)
    root = tree.getroot()
    title = ""
    for el in root.iter():
        if _local(el.tag) == "book-title" and el.text:
            title = el.text.strip()
            break
    body = next((el for el in root.iter() if _local(el.tag) == "body"), None)
    parts: list[str] = []

    def walk(node) -> None:
        for child in node:
            name = _local(child.tag)
            if name == "title":
                text = " ".join(t.strip() for t in child.itertext() if t.strip())
                if text:
                    parts.append(f"<h3>{escape(text)}</h3>")
            elif name == "p":
                inner = "".join(_fb2_inline(c) for c in child)
                parts.append(f"<p>{escape(child.text or '')}{inner}</p>")
            elif name in ("poem", "epigraph"):
                text = escape(" ".join(t.strip() for t in child.itertext() if t.strip()))
                parts.append(f"<p><i>{text}</i></p>")
            elif name == "section":
                walk(child)
            elif name == "empty-line":
                parts.append("<br>")

    if body is not None:
        walk(body)
    return title, "\n".join(parts) or "<i>(пусто)</i>"


def _fb2_inline(node) -> str:
    name = _local(node.tag)
    text = escape(node.text or "")
    tail = escape(node.tail or "")
    inner = "".join(_fb2_inline(c) for c in node)
    if name in ("emphasis", "style", "strong"):
        return f"<i>{text}{inner}</i>{tail}"
    if name == "a":
        return f"<u>{text}{inner}</u>{tail}"
    if name == "subtitle":
        return f"<b>{text}{inner}</b>{tail}"
    return f"{text}{inner}{tail}"


# ---------------------------------------------------------------- epub

def epub_chapters(path: str) -> list[tuple[str, str]]:
    """Список глав по spine: (заголовок, исходный xhtml)."""
    with zipfile.ZipFile(path) as zf:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        rootfile = next(el for el in container.iter()
                        if _local(el.tag) == "rootfile")
        opf_path = rootfile.attrib["full-path"]
        base = os.path.dirname(opf_path)
        opf = ET.fromstring(zf.read(opf_path))

        manifest: dict[str, tuple[str, str]] = {}
        for item in opf.iter():
            if _local(item.tag) == "item":
                manifest[item.attrib["id"]] = (item.attrib["href"],
                                               item.attrib.get("media-type", ""))
        spine = [el.attrib["idref"] for el in opf.iter()
                 if _local(el.tag) == "itemref"]

        chapters = []
        for idref in spine:
            href, media = manifest.get(idref, ("", ""))
            if media and "html" not in media:
                continue
            full = f"{base}/{href}" if base else href
            try:
                raw = zf.read(full)
            except KeyError:
                continue
            xhtml = raw.decode("utf-8", errors="replace")
            title = _html_title(xhtml) or f"Глава {len(chapters) + 1}"
            chapters.append((title, xhtml))
        return chapters


def _html_title(xhtml: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", xhtml, re.S | re.I)
    if match:
        return " ".join(match.group(1).split())
    match = re.search(r"<h[1-3][^>]*>(.*?)</h[1-3]>", xhtml, re.S | re.I)
    if match:
        return " ".join(re.sub(r"<[^>]+>", "", match.group(1)).split())
    return ""


# ---------------------------------------------------------------- xml

def xml_tree(path: str) -> tuple[str, tuple]:
    """(имя корня, узел) где узел = (тег, текст, [дети])."""
    root = ET.parse(path).getroot()

    def build(el) -> tuple:
        return (_local(el.tag), " ".join((el.text or "").split()),
                [build(child) for child in el])

    return _local(root.tag), build(root)
