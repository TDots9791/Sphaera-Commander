"""Быстрая панель просмотра (Ctrl+Q): вторая панель показывает содержимое
файла под курсором активной панели — как Quick View в Total Commander.

Поддержка: текст (с кодировками), md (рендер), html, pdf (первая страница),
docx (HTML), doc/rtf (текст через antiword/striprtf), pptx (первый слайд
своим рендером), fb2, epub (глава 1), картинки; остальное — сведения о файле
и подсказка про F3.
"""

from __future__ import annotations

import os
import tempfile
from html import escape

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QFont, QImage, QPixmap, QFontDatabase
from PySide6.QtWidgets import (
    QLabel,
    QScrollArea,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .i18n import tr
from . import legacy_formats as lf
from . import previewers as pv
from . import slide_render
from .fsmodel import FileEntry, human_size
from .textutil import text_preview
from .viewer import looks_binary

PREVIEW_TEXT_LIMIT = 1 * 1024 * 1024
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico")


class QuickPreview(QWidget):
    """Встраиваемый предпросмотр; обновление с антидребезгом 200 мс."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending: FileEntry | None = None

        self._info = QLabel(tr("Быстрый просмотр (Ctrl+Q)"))
        self._info.setWordWrap(True)

        self._stack = QStackedWidget()
        self._page_info = QLabel(tr("Каталог.\n(Enter — перейти, F3 — открыть файл)"))
        self._page_info.setAlignment(Qt.AlignCenter)
        self._page_info.setWordWrap(True)
        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(True)
        self._image = QLabel()
        self._image.setAlignment(Qt.AlignCenter)
        self._pdf_page = QLabel()
        self._pdf_page.setAlignment(Qt.AlignCenter)
        pdf_scroll = QScrollArea()
        pdf_scroll.setWidgetResizable(True)
        pdf_scroll.setWidget(self._pdf_page)

        self._stack.addWidget(self._page_info)   # 0
        self._stack.addWidget(self._browser)     # 1
        self._stack.addWidget(self._image)       # 2
        self._stack.addWidget(pdf_scroll)        # 3

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self._stack, 1)
        layout.addWidget(self._info)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(200)
        self._debounce.timeout.connect(self._load_pending)

    def show_entry(self, entry: FileEntry | None) -> None:
        self._pending = entry
        self._debounce.start()

    def _load_pending(self) -> None:
        try:
            self._load(self._pending)
        except Exception as exc:  # любой формат может оказаться битым
            self._page(tr("Не удалось показать:\n{exc}").format(exc=exc))
            self._info.setText("")

    # -- загрузка -----------------------------------------------------------

    def _page(self, text: str) -> None:
        self._page_info.setText(text)
        self._stack.setCurrentIndex(0)

    def _load(self, entry: FileEntry | None) -> None:
        if entry is None or entry.is_dir:
            self._page(tr("Каталог.\n(Enter — перейти, F3 — открыть файл)"))
            self._info.setText("")
            return
        path = entry.path
        ext = os.path.splitext(path)[1].lower()
        kind = pv.document_kind(path)
        # каждая загрузка начинает с чистого (пропорционального) шрифта:
        # моноширинный после DOC не должен жить в docx/html/epub
        self._browser.document().setDefaultFont(QFont())

        if kind == "pdf":
            count = pv.pdf_page_count(path)
            data, w, h, stride = pv.pdf_render(path, 0, scale=1.5)
            img = QImage(data, w, h, stride, QImage.Format.Format_BGR888)
            self._show_pixmap(QPixmap.fromImage(img.copy()))
            self._info.setText(
                tr("{name} • PDF: страница 1 из {count} • F3 — все страницы").format(name=entry.name, count=count))
            return
        if kind == "docx":
            self._set_html(pv.docx_to_html(path))
            self._info.setText(f"{entry.name} • docx • {human_size(entry.size)} • F3/F4")
            return
        if kind == "doc":
            # antiword рисует таблицы позиционно (| по колонкам) —
            # выравнивание сходится только в моноширинном шрифте
            self._browser.document().setDefaultFont(
                QFontDatabase.systemFont(QFontDatabase.FixedFont))
            self._set_text(lf.doc_to_text(path))
            self._info.setText(f"{entry.name} • doc • {human_size(entry.size)} • F3/F4")
            return
        if kind == "rtf":
            self._set_text(lf.rtf_to_text(path))
            self._info.setText(f"{entry.name} • rtf • {human_size(entry.size)} • F3/F4")
            return
        if kind == "pptx":
            images_dir = os.path.join(tempfile.gettempdir(), "sphaera-preview")
            os.makedirs(images_dir, exist_ok=True)
            deck = pv.pptx_slides_rich(path, images_dir)
            if deck["slides"]:
                pixmap = slide_render.render_slide(deck, deck["slides"][0], 1.0)
                self._show_pixmap(pixmap)
                self._info.setText(tr("{name} • слайд 1 из {count} • F3").format(name=entry.name, count=len(deck["slides"])))
            return
        if ext in IMAGE_EXTS:
            img = QImage(path)
            if not img.isNull():
                self._show_image(img)
                self._info.setText(f"{entry.name} • {img.width()}×{img.height()}")
                return

        text, enc, _truncated = text_preview(path, PREVIEW_TEXT_LIMIT)
        try:
            with open(path, "rb") as f:
                head = f.read(8192)
        except OSError:
            head = b""
        if looks_binary(head):
            self._page(f"{entry.name}\n\n" + tr("бинарный файл\n(F3 — hex-обзор)"))
            self._info.setText(f"{human_size(entry.size)}")
            return

        base_info = tr("{name} • кодировка: {enc}").format(name=entry.name, enc=enc)
        self._browser.document().setBaseUrl(
            QUrl.fromLocalFile(os.path.dirname(path) or "."))
        if ext == ".md":
            self._browser.document().setMarkdown(text)
            self._stack.setCurrentIndex(1)
        elif ext in (".html", ".htm", ".xhtml"):
            self._browser.setHtml(text)
            self._stack.setCurrentIndex(1)
        elif ext == ".fb2":
            title, html = pv.fb2_html(path)
            self._browser.setHtml(f"<h2>{escape(title)}</h2>" + html)
            self._stack.setCurrentIndex(1)
        elif ext == ".epub":
            chapters = pv.epub_chapters(path)
            if chapters:
                self._browser.setHtml(chapters[0][1])
                base_info += tr(" • глава 1 из {count} (F3 — по главам)").format(count=len(chapters))
            self._stack.setCurrentIndex(1)
        else:
            self._browser.setPlainText(text)
            self._stack.setCurrentIndex(1)
        self._info.setText(base_info)

    # -- вспомогательные ------------------------------------------------------

    def _set_html(self, html: str) -> None:
        self._browser.setHtml(html)
        self._stack.setCurrentIndex(1)

    def _set_text(self, text: str) -> None:
        self._browser.setPlainText(text)
        self._stack.setCurrentIndex(1)

    def _show_image(self, img: QImage) -> None:
        scaled = img.scaled(self._image.size(), Qt.KeepAspectRatio,
                            Qt.SmoothTransformation)
        self._image.setPixmap(QPixmap.fromImage(scaled))
        self._stack.setCurrentIndex(2)

    def _show_pixmap(self, pixmap: QPixmap) -> None:
        self._pdf_page.setPixmap(pixmap)
        self._pdf_page.adjustSize()
        self._stack.setCurrentIndex(3)
