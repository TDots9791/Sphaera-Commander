"""Быстрая панель просмотра (Ctrl+Q): вторая панель показывает содержимое
файла под курсором активной панели — как Quick View в Total Commander.

Поддержка: текст (с кодировками), md (рендер), html, pdf (все страницы —
кнопки ‹ › или клик по краю страницы; масштаб — колесо с Ctrl или щипок),
docx (HTML), doc/rtf (текст через antiword/striprtf), pptx (слайды своим
рендером, листается), fb2, epub (глава 1), картинки; остальное — сведения
о файле и подсказка про F3.
"""

from __future__ import annotations

import os
import tempfile
from html import escape

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QFont, QImage, QPixmap, QFontDatabase
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QStackedWidget,
    QTextBrowser,
    QToolButton,
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
MEMBER_CACHE_LIMIT = 100  # извлечённых членов архива в кэше предпросмотра

_member_cache_dir: str | None = None


def _member_temp_dir() -> str:
    """Каталог кэша извлечённых членов архива (переполнился — очищается)."""
    global _member_cache_dir
    import tempfile as _tempfile

    if _member_cache_dir is None or not os.path.isdir(_member_cache_dir):
        _member_cache_dir = _tempfile.mkdtemp(prefix="sphaera-preview-vfs-")
    return _member_cache_dir


def _prune_member_cache() -> None:
    """Больше лимита — вымести весь кэш (члены извлекаются заново по мере
    надобности; извлечение дешевле роста кэша без границ)."""
    if _member_cache_dir is None or not os.path.isdir(_member_cache_dir):
        return
    try:
        if len(os.listdir(_member_cache_dir)) > MEMBER_CACHE_LIMIT:
            import shutil as _shutil

            _shutil.rmtree(_member_cache_dir, ignore_errors=True)
            globals()["_member_cache_dir"] = None
    except OSError:
        pass
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico")
PDF_SCALE_MIN, PDF_SCALE_MAX = 0.25, 6.0


class QuickPreview(QWidget):
    """Встраиваемый предпросмотр; обновление с антидребезгом 200 мс."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending: FileEntry | None = None
        # состояние листания/масштаба документа (pdf/pptx)
        self._doc_path = ""
        self._doc_index = 0
        self._doc_count = 0
        self._doc_scale = 1.5
        self._doc_kind = ""  # "" | "pdf" | "pptx"
        self._deck: dict | None = None

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
        self._pdf_scroll = QScrollArea()
        self._pdf_scroll.setWidgetResizable(True)
        self._pdf_scroll.setWidget(self._pdf_page)
        self._pdf_scroll.viewport().installEventFilter(self)
        self._pdf_page.installEventFilter(self)

        self._stack.addWidget(self._page_info)   # 0
        self._stack.addWidget(self._browser)     # 1
        self._stack.addWidget(self._image)       # 2
        self._stack.addWidget(self._pdf_scroll)  # 3

        # -- навигация по страницам/слайдам
        self._btn_prev = QToolButton()
        self._btn_prev.setText("◀")
        self._btn_prev.setAutoRaise(True)
        self._btn_prev.setToolTip(tr("Предыдущая страница"))
        self._btn_prev.clicked.connect(lambda: self._doc_navigate(-1))
        self._btn_next = QToolButton()
        self._btn_next.setText("▶")
        self._btn_next.setAutoRaise(True)
        self._btn_next.setToolTip(tr("Следующая страница"))
        self._btn_next.clicked.connect(lambda: self._doc_navigate(1))
        self._doc_pos = QLabel("")
        self._doc_pos.setAlignment(Qt.AlignCenter)
        nav = QHBoxLayout()
        nav.setContentsMargins(0, 0, 0, 0)
        nav.addWidget(self._btn_prev)
        nav.addWidget(self._doc_pos, 1)
        nav.addWidget(self._btn_next)
        self._doc_nav = QWidget()
        self._doc_nav.setLayout(nav)
        self._doc_nav.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self._stack, 1)
        layout.addWidget(self._doc_nav)
        layout.addWidget(self._info)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(200)
        self._debounce.timeout.connect(self._load_pending)

    # -- события мыши/жестов -------------------------------------------------

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Wheel and obj is self._pdf_scroll.viewport():
            if self._doc_kind and (ev.modifiers() & Qt.ControlModifier):
                factor = 1.1 if ev.angleDelta().y() > 0 else 1 / 1.1
                self._doc_zoom(factor)
                return True
            return False
        if t == QEvent.NativeGesture and self._doc_kind:
            # тачпад: щипок приходит жестом Zoom (Wayland) или колесом с Ctrl
            try:
                if ev.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                    self._doc_zoom(1.0 + ev.value())
                    return True
            except AttributeError:
                pass
            return False
        if t == QEvent.MouseButtonRelease and obj is self._pdf_page:
            # клик по левой/правой трети страницы — листать (как читалки)
            if self._doc_kind and self._doc_count > 1:
                x = ev.position().toPoint().x()
                if x < self._pdf_page.width() / 3:
                    self._doc_navigate(-1)
                elif x > self._pdf_page.width() * 2 / 3:
                    self._doc_navigate(1)
        return super().eventFilter(obj, ev)

    # -- листание/масштаб ----------------------------------------------------

    def _doc_reset(self, path: str, count: int, kind: str) -> None:
        self._doc_path = path
        self._doc_index = 0
        self._doc_count = count
        self._doc_kind = kind
        self._doc_nav.setVisible(count > 1)
        self._btn_prev.setEnabled(False)
        self._btn_next.setEnabled(count > 1)

    def _doc_info(self, name: str) -> None:
        """Строка-статус под документом (синхронно с листанием)."""
        if self._doc_kind == "pdf":
            self._info.setText(
                tr("{name} • стр. {n} из {count} • F3 — все страницы").format(
                    name=name, n=self._doc_index + 1, count=self._doc_count))
        else:
            self._info.setText(
                tr("{name} • слайд {n} из {count} • F3").format(
                    name=name, n=self._doc_index + 1, count=self._doc_count))

    def _doc_navigate(self, delta: int) -> None:
        if not self._doc_kind:
            return
        new_index = self._doc_index + delta
        if not 0 <= new_index < self._doc_count:
            return
        self._doc_index = new_index
        self._doc_show()

    def _doc_zoom(self, factor: float) -> None:
        if not self._doc_kind:
            return
        self._doc_scale = max(PDF_SCALE_MIN,
                              min(PDF_SCALE_MAX, self._doc_scale * factor))
        self._doc_show()

    def _fit_scale(self, width_pt: float) -> float:
        """Масштаб «по ширине панели» (в пунктах PDF)."""
        viewport = max(80, self._pdf_scroll.viewport().width() - 8)
        return max(PDF_SCALE_MIN, min(PDF_SCALE_MAX, viewport / max(1.0, width_pt)))

    def _doc_show(self) -> None:
        """Отрисовать текущую страницу/слайд с текущим масштабом."""
        name = os.path.basename(self._doc_path)
        if self._doc_kind == "pdf":
            data, w, h, stride = pv.pdf_render(self._doc_path, self._doc_index,
                                               self._doc_scale)
            img = QImage(data, w, h, stride, QImage.Format.Format_BGR888)
            self._pdf_page.setPixmap(QPixmap.fromImage(img.copy()))
            try:
                w_pt, _h = pv.pdf_page_size(self._doc_path, self._doc_index)
            except Exception:
                w_pt = w / max(0.01, self._doc_scale)
            self._doc_pos.setText(
                tr("стр. {n} из {total} • {scale:.0f}%").format(
                    n=self._doc_index + 1, total=self._doc_count,
                    scale=self._doc_scale * 100))
            self._btn_prev.setEnabled(self._doc_index > 0)
            self._btn_next.setEnabled(self._doc_index < self._doc_count - 1)
        else:  # pptx
            pixmap = slide_render.render_slide(
                self._deck, self._deck["slides"][self._doc_index],
                self._doc_scale)
            self._pdf_page.setPixmap(pixmap)
            self._doc_pos.setText(
                tr("слайд {n} из {total}").format(
                    n=self._doc_index + 1, total=self._doc_count))
            self._btn_prev.setEnabled(self._doc_index > 0)
            self._btn_next.setEnabled(self._doc_index < self._doc_count - 1)
        self._pdf_page.adjustSize()
        self._doc_info(name)

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
            self._doc_kind = ""
            self._doc_nav.hide()
            self._page(tr("Каталог.\n(Enter — перейти, F3 — открыть файл)"))
            self._info.setText("")
            return
        if "::" in entry.path:
            # член архива: извлечь во временный файл и показать как обычный
            archive, _, member = entry.path.partition("::")
            _prune_member_cache()
            try:
                from .archives import ArchiveBrowser

                browser = ArchiveBrowser(archive)
                temp_path = browser.extract_member_to_temp(
                    member, _member_temp_dir())
            except Exception as exc:
                self._page(tr("Не удалось показать:\n{exc}").format(exc=exc))
                self._info.setText("")
                return
            self._load(FileEntry(name=entry.name, path=temp_path,
                                 is_dir=False, is_link=False,
                                 size=entry.size, mtime=entry.mtime,
                                 mode=entry.mode))
            return
        path = entry.path
        ext = os.path.splitext(path)[1].lower()
        kind = pv.document_kind(path)
        self._doc_kind = ""
        self._doc_nav.hide()
        # каждая загрузка начинает с чистого (пропорционального) шрифта:
        # моноширинный после DOC не должен жить в docx/html/epub
        self._browser.document().setDefaultFont(QFont())

        if kind == "pdf":
            count = pv.pdf_page_count(path)
            self._doc_reset(path, count, "pdf")
            try:
                w_pt, _h = pv.pdf_page_size(path, 0)
                self._doc_scale = self._fit_scale(w_pt)
            except Exception:
                self._doc_scale = 1.5
            self._doc_show()
            self._stack.setCurrentIndex(3)
            self._info.setText(tr("{name} • стр. {n} из {count} • F3 — все страницы").format(
                name=entry.name, n=self._doc_index + 1, count=count))
            self._doc_nav.setToolTip(
                tr("Листание: кнопки или клик по краю страницы. Масштаб: колесо с Ctrl или щипок на тачпаде."))
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
                self._deck = deck
                self._doc_reset(path, len(deck["slides"]), "pptx")
                base = slide_render.render_slide(
                    deck, deck["slides"][0], 1.0)
                self._doc_scale = self._fit_scale(
                    base.width())  # пиксели при 1.0 — пропорция та же
                self._doc_show()
                self._stack.setCurrentIndex(3)
                self._info.setText(tr("{name} • слайд {n} из {count} • F3").format(
                    name=entry.name, n=self._doc_index + 1, count=len(deck["slides"])))
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
