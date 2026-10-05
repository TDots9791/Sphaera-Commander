"""Встроенный просмотр (F3) и правка (F4).

Редактор: моноширинный шрифт, номера строк, подсветка текущей строки, поиск
с заменой (Ctrl+H), переход на строку (Ctrl+G), позиция курсора, перенос строк.
Текст: автоопределение кодировки (utf-8 → utf-16 по BOM → cp1251 → latin-1),
файлы больше лимита показываются частично с явной пометкой.
Markdown: рендер предпросмотра (QTextDocument.setMarkdown); таблицы GFM
реконструируются с рамками и выделенной шапкой; в правке — живой предпросмотр
рядом с исходником (дебаунс 400 мс, таблицы рендерятся по мере ввода).
JSON: подсветка синтаксиса, форматирование (Ctrl+Shift+F), валидация с позицией
ошибки, сворачиваемое дерево (Ctrl+T); при сохранении некорректного JSON —
явный вопрос. Ctrl+E — открыть файл во внешнем приложении.
Бинарные: hex-обзор (первые 2 МиБ). Изображения: масштабируемый просмотр.
PDF в правке: аннотации (подсветка, заметка, текст на странице) + список
с удалением; рендер с аннотациями (draw_annots); масштаб страницы —
колесо с Ctrl или щипок на тачпаде (и для pptx).
XLSX в правке: редактируемая сетка с формулами — свой движок пересчёта
(formulas.py, ~50 функций, межлистовые ссылки, детекция циклов); Ctrl+S
сохраняет формулы и вписывает кэшированные значения (openpyxl их не пишет),
нетронутые ячейки не переписываются (типы и стили живут). fx — показать формулы.
Правка: сохранение в той же кодировке (Ctrl+S), защита правок при закрытии.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import xml.etree.ElementTree as ET
from html import escape

from PySide6.QtCore import (
    QEvent,
    QRect,
    QAbstractTableModel,
    QModelIndex,
    QUrl,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QImage,
    QKeySequence,
    QPainter,
    QPixmap,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextFrameFormat,
    QTextFormat,
    QTextDocument,
    QTextTable,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRubberBand,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableView,
    QTextBrowser,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import previewers as pv
from . import slide_render
from . import legacy_formats as lf
from .i18n import tr

TEXT_LIMIT = 16 * 1024 * 1024   # показываем не больше 16 МиБ текста
HEX_LIMIT = 2 * 1024 * 1024     # и 2 МиБ hex-обзора
HEX_ROW = 16
LIVE_VALIDATE_LIMIT = 2 * 1024 * 1024
TREE_NODE_LIMIT = 20000

MD_EXTS = (".md", ".markdown")
JSON_EXTS = (".json", ".jsonc")

# общие текстовые утилиты вынесены в textutil; имена оставлены для совместимости
from .textutil import (  # noqa: E402
    HEX_LIMIT,
    HEX_ROW,
    TEXT_LIMIT,
    detect_decode,
    hexdump,
    looks_binary,
    text_preview,
)


def json_error_position(text: str) -> str | None:
    """Описание ошибки JSON с позицией (строка/столбец) или None, если всё хорошо."""
    try:
        json.loads(text)
        return None
    except json.JSONDecodeError as exc:
        return tr("строка {line}, столбец {col}: {msg}").format(line=exc.lineno, col=exc.colno, msg=exc.msg)
    except (ValueError, RecursionError) as exc:
        return str(exc)


class JsonHighlighter(QSyntaxHighlighter):
    """Подсветка JSON: ключи, строки, числа, литералы; цвета по теме."""

    RULES = (  # порядок важен: правило может перекрыть предыдущее
        ("string", re.compile(r'"(?:[^"\\]|\\.)*"')),
        ("key", re.compile(r'"(?:[^"\\]|\\.)*"(?=\s*:)', re.M)),
        ("number", re.compile(r"-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b")),
        ("literal", re.compile(r"\b(?:true|false|null)\b")),
    )

    def __init__(self, document, dark: bool):
        super().__init__(document)
        palette = {
            "key": ((11, 83, 148), (111, 168, 220)),
            "string": ((56, 118, 29), (147, 196, 125)),
            "number": ((180, 83, 9), (246, 178, 107)),
            "literal": ((136, 32, 12), (213, 166, 189)),
        }
        self._formats = {
            name: self._fmt(rgb[1] if dark else rgb[0])
            for name, rgb in palette.items()
        }

    @staticmethod
    def _fmt(rgb: tuple) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(*rgb))
        return fmt

    def highlightBlock(self, text: str) -> None:
        for name, pattern in self.RULES:
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(),
                               self._formats[name])


# ---------------------------------------------------------------- редактор


class PdfCanvas(QLabel):
    """Отрисованная страница PDF с режимом аннотаций: рамка выделения,
    сигналы прямоугольника и клика в координатах виджета."""

    annot_drawn = Signal(object)   # QRect — прямоугольник выделения
    annot_clicked = Signal(object)  # QPoint — клик (заметка)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.annot_mode: str | None = None
        self._origin = None
        self._band = QRubberBand(QRubberBand.Shape.Rectangle, self)

    def set_annot_mode(self, mode: str | None) -> None:
        self.annot_mode = mode
        if mode:
            self.setCursor(Qt.CrossCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, event) -> None:
        if self.annot_mode and event.button() == Qt.LeftButton:
            self._origin = event.position().toPoint()
            self._band.setGeometry(QRect(self._origin, QSize()))
            self._band.show()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._origin is not None:
            self._band.setGeometry(
                QRect(self._origin, event.position().toPoint()).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._origin is not None and event.button() == Qt.LeftButton:
            rect = QRect(self._origin, event.position().toPoint()).normalized()
            self._origin = None
            self._band.hide()
            if rect.width() < 6 and rect.height() < 6:
                self.annot_clicked.emit(rect.center())
            else:
                self.annot_drawn.emit(rect)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class AnnotListModel(QAbstractTableModel):
    """Таблица аннотаций PDF: страница, тип, содержимое."""

    COLUMNS = (tr("Страница"), tr("Тип"), tr("Текст"))

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._items: list[dict] = []
        self.reload(path)

    def reload(self, path: str) -> None:
        from sphaera_commander import previewers as pv

        self.beginResetModel()
        try:
            self._items = pv.pdf_annot_list(path)
        except Exception:
            self._items = []
        self.endResetModel()

    def annot_at(self, row: int) -> dict | None:
        return self._items[row] if 0 <= row < len(self._items) else None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent=QModelIndex()):
        return 3

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        item = self._items[index.row()]
        if index.column() == 0:
            return str(item["page"] + 1)
        if index.column() == 1:
            return item["subtype"]
        return item["contents"]

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.COLUMNS[section]
        return None


class LineNumberArea(QWidget):
    def __init__(self, editor: "LineNumberTextEdit"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_width(), 0)

    def paintEvent(self, event) -> None:
        self._editor.paint_line_numbers(event)


class LineNumberTextEdit(QPlainTextEdit):
    """QPlainTextEdit с колонкой номеров строк и подсветкой текущей строки."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._area = LineNumberArea(self)
        self.blockCountChanged.connect(self._update_area_width)
        self.updateRequest.connect(self._update_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_area_width(0)
        self._highlight_current_line()

    def line_number_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_area_width(self, _new_block_count: int) -> None:
        self.setViewportMargins(self.line_number_width(), 0, 0, 0)

    def _update_area(self, rect, dy: int) -> None:
        if dy:
            self._area.scroll(0, dy)
        else:
            self._area.update(0, rect.y(), self._area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_area_width(0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._area.setGeometry(cr.left(), cr.top(),
                               self.line_number_width(), cr.height())

    def _is_dark(self) -> bool:
        pal = self.palette()
        return pal.color(pal.ColorRole.Window).lightness() < 128

    def paint_line_numbers(self, event) -> None:
        painter = QPainter(self._area)
        dark = self._is_dark()
        painter.fillRect(event.rect(), QColor(45, 45, 45) if dark else QColor(240, 240, 240))
        painter.setPen(QColor(150, 150, 150) if dark else QColor(110, 110, 110))
        block = self.firstVisibleBlock()
        num = block.blockNumber()
        top = round(self.blockBoundingGeometry(block)
                    .translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(0, top, self._area.width() - 5,
                                 self.fontMetrics().height(),
                                 Qt.AlignRight, str(num + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            num += 1
        painter.end()

    def _highlight_current_line(self) -> None:
        if not self.isReadOnly():
            dark = self._is_dark()
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(QColor(52, 52, 52) if dark else QColor(255, 246, 205))
            sel.format.setProperty(QTextFormat.FullWidthSelection, True)
            sel.cursor = self.textCursor()
            sel.cursor.clearSelection()
            self.setExtraSelections([sel])
        else:
            self.setExtraSelections([])


def find_markdown_tables(document) -> list:
    tables: list[QTextTable] = []

    def walk(frame) -> None:
        for child in frame.childFrames():
            if isinstance(child, QTextTable):
                tables.append(child)
            walk(child)

    walk(document.rootFrame())
    return tables


def collect_json_tree(data, root: "QTreeWidgetItem", _depth: int = 0) -> int:
    """Наполнить QTreeWidget значением JSON; возвращает число созданных узлов."""
    if _depth > 32:
        return 0
    count = 0

    def make(key, value) -> QTreeWidgetItem:
        nonlocal count
        count += 1
        if isinstance(value, dict):
            item = QTreeWidgetItem([str(key), tr("объект · {n}").format(n=len(value))])
        elif isinstance(value, list):
            item = QTreeWidgetItem([str(key), tr("массив · {n}").format(n=len(value))])
        else:
            item = QTreeWidgetItem([str(key),
                                    json.dumps(value, ensure_ascii=False)])
        return item

    def add(parent_item, key, value, depth):
        if count >= TREE_NODE_LIMIT:
            return
        item = make(key, value)
        parent_item.addChild(item)
        if isinstance(value, dict):
            for k, v in value.items():
                add(item, k, v, depth + 1)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                add(item, i, v, depth + 1)

    if isinstance(data, dict):
        for k, v in data.items():
            add(root, k, v, 0)
    elif isinstance(data, list):
        for i, v in enumerate(data):
            add(root, i, v, 0)
    else:
        add(root, "", data, 0)
    return count


class SheetModel(QAbstractTableModel):
    """Таблица поверх списков строк: без QTableWidgetItem — держит
    сотни тысяч строк, QTableView рисует только видимое.

    Для xlsx хранит параллельно сырые ячейки (формулы строками '=…'),
    координаты формул и вычисленные значения: правка ячейки запускает
    пересчёт листа через recalc_hook (движок formulas.py), показ
    переключается между значениями и формулами (show_raw)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[list[str]] = []
        self._cols = 0
        self.editable = False
        self.dirty = False
        self.raw: list[list[str]] | None = None
        self.formulas: dict[tuple, str] = {}
        self.computed: dict[tuple, object] = {}
        self.show_raw = False
        self.recalc = None  # hook: пересчитать лист после правки (диалог)

    def rows(self) -> list[list[str]]:
        return self._rows

    def start_xlsx(self, raw: list[list[str]], formulas: dict,
                   computed: dict, display: list[list[str]]) -> None:
        """Загрузить лист xlsx: сырые ячейки + что показать."""
        self.beginResetModel()
        self._rows = display
        self._cols = max((len(r) for r in raw), default=0)
        self.raw = [list(r) for r in raw]
        self.formulas = dict(formulas)
        self.computed = dict(computed)
        self.dirty = False
        self.endResetModel()

    def start_plain(self, rows: list[list[str]]) -> None:
        """Загрузить обычные строки (csv/xls) — без формульного слоя."""
        self.beginResetModel()
        self._rows = rows
        self._cols = max((len(r) for r in rows), default=0)
        self.raw = None
        self.formulas = {}
        self.computed = {}
        self.dirty = False
        self.endResetModel()

    def flags(self, index):
        base = super().flags(index)
        if self.editable and index.isValid():
            return base | Qt.ItemIsEditable
        return base

    def setData(self, index, value, role=Qt.EditRole):
        if not (self.editable and index.isValid()
                and role == Qt.EditRole):
            return False
        row, col = index.row(), index.column()
        raw = str(value)
        if self.raw is not None:
            while len(self.raw) <= row:
                self.raw.append([])
            target = self.raw[row]
            while len(target) <= col:
                target.append("")
            target[col] = raw
            if raw.startswith("="):
                self.formulas[(row, col)] = raw
            else:
                self.formulas.pop((row, col), None)
                self.computed.pop((row, col), None)
            if self.recalc is not None:
                self.recalc()
            self._cols = max(self._cols, col + 1)
        else:
            while len(self._rows) <= row:
                self._rows.append([])
            target = self._rows[row]
            while len(target) <= col:
                target.append("")
            target[col] = raw
            self._cols = max(self._cols, col + 1)
        self.dirty = True
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(max(0, len(self._rows) - 1), max(0, self._cols - 1)))
        return True

    def reset_rows(self) -> None:
        self.beginResetModel()
        self._rows = []
        self._cols = 0
        self.raw = None
        self.formulas = {}
        self.computed = {}
        self.endResetModel()

    def append_rows(self, rows: list[list[str]]) -> None:
        if not rows:
            return
        first = len(self._rows)
        self.beginInsertRows(QModelIndex(), first, first + len(rows) - 1)
        self._rows.extend(rows)
        self._cols = max(self._cols, max((len(r) for r in rows), default=0))
        self.endInsertRows()

    def apply_computed(self, computed: dict, display: list[list[str]]) -> None:
        """Обновить показ после пересчёта (без сброса модели)."""
        self.computed = dict(computed)
        self._rows = display
        self._cols = max((len(r) for r in display), default=0)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self._cols

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        row = self._rows[index.row()]
        col = index.column()
        if (self.show_raw and self.raw is not None
                and (index.row(), col) in self.formulas):
            return self.formulas[(index.row(), col)]
        return row[col] if col < len(row) else None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return str(section + 1)
        return None


class FileViewerDialog(QDialog):
    """Просмотр/правка одного файла; files — список для навигации след/пред."""

    gridBatch = Signal(object)

    def __init__(self, parent, files: list[str], index: int, editable: bool,
                 goto_line: int = 0, modal: bool = True):
        super().__init__(parent)
        self._goto_on_load = goto_line
        self.files = [f for f in files if os.path.isfile(f)]
        self.index = max(0, min(index, len(self.files) - 1))
        self.editable = editable
        self.encoding = "utf-8"
        self.saved_on_close = False
        self.kind = "text"  # text | md | json | xml | binary | image | pdf | docx | xlsx | pptx | csv | html | fb2 | epub
        self._image: QPixmap | None = None
        self._highlighter: JsonHighlighter | None = None
        self._info_base = ""
        self._pdf_path = ""
        self._pdf_index = 0
        self._pdf_count = 0
        self._pdf_scale = 2.0
        self._doc_items: list[tuple[str, str]] = []
        self._sheets: list[tuple[str, list[list[str]]]] = []
        self._temp_dirs: list[str] = []
        self._grid_sources: list[tuple] = []
        self._xls_sheets_cache: list[tuple[str, list[list[str]]]] = []
        self._deck: dict | None = None
        self._slides_text: list[tuple[int, str]] = []
        self._xlsx_buffers: dict[str, dict] = {}  # лист → сырые данные/кэши
        self._xlsx_dirty: set[str] = set()
        self._pdf_page_wh = (0.0, 0.0)
        self._md_live_path = ""
        self._grid_current_name: str | None = None
        self.gridBatch.connect(self._on_grid_batch)
        self._json_timer = QTimer(self)
        self._json_timer.setSingleShot(True)
        self._json_timer.setInterval(500)
        self._json_timer.timeout.connect(self._update_json_status)
        self._md_timer = QTimer(self)
        self._md_timer.setSingleShot(True)
        self._md_timer.setInterval(400)
        self._md_timer.timeout.connect(self._render_live_md)

        self.setModal(modal)
        self.resize(980, 680)

        self.text_edit = LineNumberTextEdit()
        self.text_edit.setReadOnly(not editable)
        self.text_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text_edit.textChanged.connect(self._json_timer.start)
        self.text_edit.textChanged.connect(self._md_timer.start)
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPointSize(max(9, font.pointSize() or 10))
        self.text_edit.setFont(font)

        # живой предпросмотр markdown (правая половина страницы исходника)
        self.md_live = QTextBrowser()
        self.md_live.setOpenExternalLinks(False)
        self.md_live.hide()

        self.edit_split = QSplitter(Qt.Horizontal)
        self.edit_split.setChildrenCollapsible(False)
        self.edit_split.addWidget(self.text_edit)
        self.edit_split.addWidget(self.md_live)

        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(True)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels((tr("Узел"), tr("Значение")))
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)

        # -- страница документа (html/fb2/epub/pptx) с выбором глав/слайдов
        self.doc_combo = QComboBox()
        self.doc_combo.hide()
        self.doc_combo.currentIndexChanged.connect(self._on_doc_combo)
        doc_page = QWidget()
        doc_layout = QVBoxLayout(doc_page)
        doc_layout.setContentsMargins(0, 0, 0, 0)
        doc_layout.addWidget(self.doc_combo)
        doc_layout.addWidget(self.preview, 1)

        # -- страница PDF: рендер страницы + текстовый слой + правка
        self.pdf_label = PdfCanvas()
        self.pdf_label.setAlignment(Qt.AlignCenter)
        self.pdf_scroll = QScrollArea()
        self.pdf_scroll.setWidgetResizable(True)
        self.pdf_scroll.setWidget(self.pdf_label)
        self.pdf_scroll.viewport().installEventFilter(self)
        self.pdf_text = QPlainTextEdit()
        self.pdf_text.setReadOnly(True)
        self.pdf_inner = QStackedWidget()
        self.pdf_inner.addWidget(self.pdf_scroll)  # 0 — картинка
        self.pdf_inner.addWidget(self.pdf_text)    # 1 — текстовый слой
        self.lbl_pdf_page = QLabel("")
        self.btn_pdf_prev = QPushButton("←")
        self.btn_pdf_next = QPushButton("→")
        self.btn_pdf_prev.clicked.connect(lambda: self._pdf_navigate(-1))
        self.btn_pdf_next.clicked.connect(lambda: self._pdf_navigate(1))
        self.btn_pdf_text = QPushButton(tr("Текст"))
        self.btn_pdf_text.setCheckable(True)
        self.btn_pdf_text.toggled.connect(self._pdf_show_text)
        self.btn_zoom_out = QPushButton("−")
        self.btn_zoom_in = QPushButton("+")
        self.btn_zoom_out.clicked.connect(lambda: self._pdf_zoom(1 / 1.25))
        self.btn_zoom_in.clicked.connect(lambda: self._pdf_zoom(1.25))
        self.btn_pdf_rot_left = QPushButton("↺")
        self.btn_pdf_rot_right = QPushButton("↻")
        self.btn_pdf_rot_left.clicked.connect(lambda: self._pdf_rotate(-90))
        self.btn_pdf_rot_right.clicked.connect(lambda: self._pdf_rotate(90))
        self.btn_pdf_delete = QPushButton(tr("Удалить страницу"))
        self.btn_pdf_delete.clicked.connect(self._pdf_delete_page)
        self.btn_pdf_export = QPushButton(tr("Экспорт страниц…"))
        self.btn_pdf_export.clicked.connect(self._pdf_export)
        pdf_bar = QHBoxLayout()
        pdf_bar.addWidget(self.lbl_pdf_page)
        pdf_bar.addWidget(self.btn_pdf_prev)
        pdf_bar.addWidget(self.btn_pdf_next)
        pdf_bar.addWidget(self.btn_zoom_out)
        pdf_bar.addWidget(self.btn_zoom_in)
        pdf_bar.addWidget(self.btn_pdf_text)
        pdf_bar.addStretch(1)
        pdf_bar.addWidget(self.btn_pdf_rot_left)
        pdf_bar.addWidget(self.btn_pdf_rot_right)
        pdf_bar.addWidget(self.btn_pdf_delete)
        pdf_bar.addWidget(self.btn_pdf_export)
        # -- панель аннотаций (только в правке)
        self.btn_annot_highlight = QPushButton(tr("Выделение"))
        self.btn_annot_highlight.setCheckable(True)
        self.btn_annot_highlight.setToolTip(
            tr("Выделить фрагмент: обведите его мышью"))
        self.btn_annot_highlight.toggled.connect(
            lambda on: self._annot_mode("highlight", on))
        self.btn_annot_note = QPushButton(tr("Заметка"))
        self.btn_annot_note.setCheckable(True)
        self.btn_annot_note.setToolTip(tr("Прикрепить заметку: клик по странице"))
        self.btn_annot_note.toggled.connect(
            lambda on: self._annot_mode("text", on))
        self.btn_annot_freetext = QPushButton(tr("Текст на странице"))
        self.btn_annot_freetext.setCheckable(True)
        self.btn_annot_freetext.setToolTip(
            tr("Добавить видимый текст: обведите область"))
        self.btn_annot_freetext.toggled.connect(
            lambda on: self._annot_mode("freetext", on))
        self.btn_annot_list = QPushButton(tr("Аннотации…"))
        self.btn_annot_list.clicked.connect(self._annot_list_dialog)
        annot_bar = QHBoxLayout()
        annot_bar.addWidget(QLabel(tr("Аннотации:")))
        annot_bar.addWidget(self.btn_annot_highlight)
        annot_bar.addWidget(self.btn_annot_note)
        annot_bar.addWidget(self.btn_annot_freetext)
        annot_bar.addStretch(1)
        annot_bar.addWidget(self.btn_annot_list)
        self.annot_bar_widget = QWidget()
        self.annot_bar_widget.setLayout(annot_bar)
        self.annot_bar_widget.hide()
        self.pdf_label.annot_drawn.connect(self._annot_from_rect)
        self.pdf_label.annot_clicked.connect(self._annot_from_click)
        self._ANNOT_BUTTONS = {
            "highlight": self.btn_annot_highlight,
            "text": self.btn_annot_note,
            "freetext": self.btn_annot_freetext,
        }
        pdf_page = QWidget()
        pdf_layout = QVBoxLayout(pdf_page)
        pdf_layout.setContentsMargins(0, 0, 0, 0)
        pdf_layout.addLayout(pdf_bar)
        pdf_layout.addWidget(self.annot_bar_widget)
        pdf_layout.addWidget(self.pdf_inner, 1)

        # -- страница таблиц (csv/xlsx): модель + фоновая загрузка
        self.sheet_model = SheetModel(self)
        self.sheet_combo = QComboBox()
        self.sheet_combo.hide()
        self.sheet_combo.currentIndexChanged.connect(self._on_sheet_changed)
        self.grid = QTableView()
        self.grid.setModel(self.sheet_model)
        self.grid.setAlternatingRowColors(True)
        self.grid.verticalHeader().hide()
        self.grid.horizontalHeader().setSectionResizeMode(
            QHeaderView.Interactive)
        self.btn_grid_raw = QPushButton("fx")
        self.btn_grid_raw.setCheckable(True)
        self.btn_grid_raw.setToolTip(tr("Показывать формулы вместо значений"))
        self.btn_grid_raw.toggled.connect(self._toggle_grid_raw)
        self.btn_grid_raw.hide()
        grid_bar = QHBoxLayout()
        grid_bar.addWidget(self.sheet_combo, 1)
        grid_bar.addWidget(self.btn_grid_raw)
        grid_page = QWidget()
        grid_layout = QVBoxLayout(grid_page)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.addLayout(grid_bar)
        grid_layout.addWidget(self.grid, 1)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.edit_split)  # 0 — исходник/текст (+md живой)
        self.stack.addWidget(doc_page)        # 1 — рендер (md/html/fb2/epub/pptx)
        self.stack.addWidget(self.tree)       # 2 — дерево json/xml
        self.stack.addWidget(pdf_page)        # 3 — pdf
        self.stack.addWidget(grid_page)       # 4 — csv/xlsx

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)

        # -- строка поиска/замены
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Поиск (Enter — далее)"))
        self.search.returnPressed.connect(self._find_next)
        btn_find = QPushButton(tr("Найти"))
        btn_find.clicked.connect(self._find_next)

        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText(tr("Заменить на"))
        btn_replace = QPushButton(tr("Заменить"))
        btn_replace.clicked.connect(self._replace_one)
        btn_replace_all = QPushButton(tr("Все"))
        btn_replace_all.clicked.connect(self._replace_all)
        self.replace_row = QWidget()
        rrow = QHBoxLayout(self.replace_row)
        rrow.setContentsMargins(0, 0, 0, 0)
        rrow.addWidget(self.replace_edit, 1)
        rrow.addWidget(btn_replace)
        rrow.addWidget(btn_replace_all)
        self.replace_row.hide()

        # -- кнопки режима
        self.btn_replace_toggle = QPushButton(tr("Заменить…"))
        self.btn_replace_toggle.setCheckable(True)
        self.btn_replace_toggle.setShortcut(QKeySequence("Ctrl+H"))
        self.btn_replace_toggle.toggled.connect(self.replace_row.setVisible)

        self.btn_wrap = QPushButton(tr("Перенос строк"))
        self.btn_wrap.setCheckable(True)
        self.btn_wrap.setToolTip(tr("Переносить длинные строки"))
        self.btn_wrap.toggled.connect(self._toggle_wrap)

        self.btn_format = QPushButton(tr("Форматировать"))
        self.btn_format.setShortcut(QKeySequence("Ctrl+Shift+F"))
        self.btn_format.setToolTip(tr("Форматировать JSON (Ctrl+Shift+F)"))
        self.btn_format.clicked.connect(self._format_json)

        self.btn_tree = QPushButton(tr("Дерево"))
        self.btn_tree.setCheckable(True)
        self.btn_tree.setShortcut(QKeySequence("Ctrl+T"))
        self.btn_tree.setToolTip(tr("Дерево JSON (Ctrl+T)"))
        self.btn_tree.toggled.connect(self._toggle_tree)

        self.btn_preview = QPushButton(tr("Предпросмотр"))
        self.btn_preview.setCheckable(True)
        self.btn_preview.setShortcut(QKeySequence("Ctrl+Shift+P"))
        self.btn_preview.setToolTip(tr("Исходник/предпросмотр (Ctrl+Shift+P)"))
        self.btn_preview.toggled.connect(self._toggle_preview)

        btn_prev = QPushButton(tr("← Пред. (Alt+↑)"))
        btn_next = QPushButton(tr("След. (Alt+↓) →"))
        btn_prev.clicked.connect(lambda: self.navigate(-1))
        btn_next.clicked.connect(lambda: self.navigate(1))

        srow = QHBoxLayout()
        srow.addWidget(self.search, 1)
        srow.addWidget(btn_find)
        srow.addWidget(self.btn_replace_toggle)
        srow.addWidget(self.btn_wrap)
        srow.addWidget(self.btn_format)
        srow.addWidget(self.btn_tree)
        srow.addWidget(self.btn_preview)
        srow.addStretch(1)
        srow.addWidget(btn_prev)
        srow.addWidget(btn_next)

        self.lbl_info = QLabel("")
        self.lbl_pos = QLabel("")

        layout = QVBoxLayout(self)
        layout.addLayout(srow)
        layout.addWidget(self.replace_row)
        bottom = QHBoxLayout()
        bottom.addWidget(self.lbl_info, 1)
        bottom.addWidget(self.lbl_pos)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.image_label, 1)
        layout.addLayout(bottom)

        self._shortcut("Ctrl+S", self._save)
        self._shortcut("Ctrl+G", self._ask_goto_line)
        self._shortcut("Ctrl+E", self._open_external)
        self._shortcut("Alt+Up", lambda: self.navigate(-1))
        self._shortcut("Alt+Down", lambda: self.navigate(1))
        self._shortcut("Escape", self.reject)
        self.text_edit.cursorPositionChanged.connect(self._update_cursor_status)

        self.load_current()
        if self._goto_on_load > 1:
            self._goto_line(self._goto_on_load)
            self._goto_on_load = 0

    def eventFilter(self, obj, ev):
        # масштаб страницы pdf/pptx: колесо с Ctrl или щипок на тачпаде
        if obj is self.pdf_scroll.viewport():
            t = ev.type()
            if t == QEvent.Wheel and self.kind in ("pdf", "pptx") \
                    and (ev.modifiers() & Qt.ControlModifier):
                self._pdf_zoom(1.1 if ev.angleDelta().y() > 0 else 1 / 1.1)
                return True
            if t == QEvent.NativeGesture and self.kind in ("pdf", "pptx"):
                try:
                    if ev.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                        self._pdf_zoom(1.0 + ev.value())
                        return True
                except AttributeError:
                    pass
        return super().eventFilter(obj, ev)

    def _shortcut(self, keys: str, slot):
        from PySide6.QtGui import QShortcut

        sc = QShortcut(QKeySequence(keys), self)
        sc.activated.connect(slot)
        return sc

    # -- загрузка ----------------------------------------------------------

    def load_current(self) -> None:
        if not self.files:
            self.reject()
            return
        path = self.files[self.index]
        self.setWindowTitle(tr("{mode}: {path}").format(
            mode=tr("Правка") if self.editable else tr("Просмотр"), path=path))
        self.image_label.hide()
        self.stack.show()
        self.stack.setCurrentIndex(0)
        self._image = None
        self._highlighter = None
        self._info_base = ""
        self.text_edit.setPlainText("")
        self.preview.clear()
        self.tree.clear()
        self.lbl_info.setText("")
        self.btn_replace_toggle.setChecked(False)
        self.btn_wrap.setChecked(False)
        self.btn_wrap.show()
        self.btn_format.hide()
        self.btn_tree.hide()
        self.btn_preview.hide()
        self._hide_doc_widgets()
        self._set_preview(False, force=True)
        if self._md_live_path != path:
            self._xlsx_buffers.clear()
            self._xlsx_dirty.clear()
            self._grid_current_name = None
            self._md_live_path = path

        ext_kind = pv.document_kind(path)
        if ext_kind is not None:
            self._load_document(path, ext_kind)
            return

        img = QImage(path)
        if not img.isNull():
            self.kind = "image"
            self._image = QPixmap.fromImage(img)
            self.stack.hide()
            self.image_label.show()
            self._fit_image()
            self.lbl_info.setText(tr("изображение {w}×{h}").format(w=img.width(), h=img.height()))
            self.search.setEnabled(False)
            self.btn_replace_toggle.hide()
            self.btn_wrap.hide()
            return
        self.search.setEnabled(True)
        self.btn_replace_toggle.show()
        try:
            with open(path, "rb") as f:
                head = f.read(8192)
                raw = head + (f.read(TEXT_LIMIT) if len(head) == 8192 else b"")
        except OSError as exc:
            self.kind = "binary"
            self.btn_replace_toggle.hide()
            self.text_edit.setPlainText(f"<" + tr("не удалось прочитать файл: {exc}").format(exc=exc) + ">")
            return
        if looks_binary(head):
            self.kind = "binary"
            self.btn_replace_toggle.hide()
            self.btn_wrap.hide()
            self.text_edit.setPlainText(hexdump(raw[:HEX_LIMIT])
                                        + (tr("\n[… hex-обзор ограничен 2 МиБ …]")
                                           if len(raw) > HEX_LIMIT else ""))
            self.lbl_info.setText(tr("бинарный файл (hex-обзор)"))
            self.text_edit.setReadOnly(True)
            return

        text, enc, truncated = text_preview(path)
        self.encoding = enc
        self.text_edit.setPlainText(text)
        self.text_edit.setReadOnly(not self.editable)
        self._info_base = tr("кодировка: {enc}").format(enc=enc) \
            + (tr(" (обрезано)") if truncated else "")
        ext = os.path.splitext(path)[1].lower()
        if ext in JSON_EXTS:
            self.kind = "json"
            self._highlighter = JsonHighlighter(
                self.text_edit.document(), self._palette_is_dark())
            self._highlighter.rehighlight()  # сразу, без ожидания цикла событий
            self.btn_format.show()
            self.btn_tree.show()
            self._update_json_status()
        elif ext in MD_EXTS:
            self.kind = "md"
            self.btn_preview.show()
            self._set_preview(not self.editable)  # F3 — рендер, F4 — исходник
            if self.editable:
                # живой рендер (таблиц в том числе) рядом с исходником
                total = max(1, self.edit_split.width())
                self.md_live.show()
                self.edit_split.setSizes([int(total * 0.58), int(total * 0.42)])
                self._render_live_md()
        else:
            self.kind = "text"
            self.lbl_info.setText(self._info_base)
        self._update_cursor_status()

    @staticmethod
    def _palette_is_dark() -> bool:
        app = QApplication.instance()
        if app is None:
            return False
        return app.palette().color(app.palette().ColorRole.Window).lightness() < 128

    # -- форматные документы -----------------------------------------------

    def _hide_doc_widgets(self):
        for b in (self.btn_pdf_prev, self.btn_pdf_next, self.btn_zoom_in,
                  self.btn_zoom_out, self.btn_pdf_text, self.btn_pdf_rot_left,
                  self.btn_pdf_rot_right, self.btn_pdf_delete,
                  self.btn_pdf_export):
            b.hide()
        self.lbl_pdf_page.setText("")
        self.doc_combo.hide()
        self.sheet_combo.hide()
        self.btn_pdf_text.setChecked(False)
        self.btn_annot_highlight.setChecked(False)
        self.btn_annot_note.setChecked(False)
        self.btn_annot_freetext.setChecked(False)
        self.annot_bar_widget.hide()
        self.btn_grid_raw.hide()
        self.btn_grid_raw.setChecked(False)
        self.md_live.hide()

    def _load_document(self, path: str, kind: str) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            loader = {
                "pdf": self._load_pdf,
                "docx": self._load_docx,
                "doc": self._load_doc,
                "rtf": self._load_rtf,
                "xls": self._load_xls,
                "xlsx": self._load_xlsx,
                "pptx": self._load_pptx,
                "csv": self._load_csv,
                "html": self._load_html,
                "fb2": self._load_fb2,
                "epub": self._load_epub,
                "xml": self._load_xml,
            }[kind]
            loader(path)
        except Exception as exc:  # битые файлы не должны ронять приложение
            QApplication.restoreOverrideCursor()
            self.kind = "binary"
            self._hide_doc_widgets()
            self.btn_wrap.hide()
            self.stack.setCurrentIndex(0)
            self.stack.show()
            self.text_edit.setPlainText(f"<" + tr("не удалось открыть ({kind}):\n{exc}").format(kind=kind, exc=exc) + ">")
            self.lbl_info.setText(tr("ошибка открытия"))
            return
        QApplication.restoreOverrideCursor()
        self._update_cursor_status()

    # pdf ------------------------------------------------------------------

    def _load_pdf(self, path: str) -> None:
        self.kind = "pdf"
        self._pdf_path = path
        self._pdf_count = pv.pdf_page_count(path)
        if self._pdf_count == 0:
            raise ValueError(tr("в файле нет страниц"))
        self._pdf_index = 0
        self._pdf_scale = 2.0
        for b in (self.btn_pdf_prev, self.btn_pdf_next, self.btn_zoom_in,
                  self.btn_zoom_out, self.btn_pdf_text):
            b.show()
        for b in (self.btn_pdf_rot_left, self.btn_pdf_rot_right,
                  self.btn_pdf_delete, self.btn_pdf_export):
            b.setVisible(self.editable)
        self.annot_bar_widget.setVisible(self.editable)
        self.stack.setCurrentIndex(3)
        self.search.setEnabled(False)
        self.btn_replace_toggle.hide()
        self._pdf_show()

    def _pdf_show(self) -> None:
        self._page_show()

    def _page_show(self) -> None:
        """Отрисовать текущую страницу: pdf — через pdfium, pptx — свой рендер."""
        if self.kind == "pptx":
            pixmap = slide_render.render_slide(
                self._deck, self._deck["slides"][self._pdf_index],
                self._pdf_scale)
            self.pdf_label.setPixmap(pixmap)
            self.lbl_pdf_page.setText(
                tr("слайд {n} из {total}").format(n=self._pdf_index + 1, total=self._pdf_count))
            self.lbl_info.setText(tr("pptx: {total} слайд(ов) • масштаб {scale:.2f}x • свой рендер").format(
                total=self._pdf_count, scale=self._pdf_scale))
            return
        data, w, h, stride = pv.pdf_render(self._pdf_path, self._pdf_index,
                                           self._pdf_scale)
        try:
            self._pdf_page_wh = pv.pdf_page_size(self._pdf_path, self._pdf_index)
        except Exception:
            self._pdf_page_wh = (w / self._pdf_scale, h / self._pdf_scale)
        img = QImage(data, w, h, stride, QImage.Format.Format_BGR888)
        self.pdf_label.setPixmap(QPixmap.fromImage(img.copy()))
        self.lbl_pdf_page.setText(tr("стр. {n} из {total}").format(n=self._pdf_index + 1, total=self._pdf_count))
        self.lbl_info.setText(tr("PDF: {total} стр. • масштаб {scale:.2f}x").format(
                total=self._pdf_count, scale=self._pdf_scale)
                              + (tr(" • F4 — правка") if self.editable else ""))

    def _pdf_navigate(self, delta: int) -> None:
        new_index = self._pdf_index + delta
        if not 0 <= new_index < self._pdf_count:
            return
        self._pdf_index = new_index
        if self.btn_pdf_text.isChecked():
            self._pdf_fill_text()
        self.pdf_inner.setCurrentIndex(1 if self.btn_pdf_text.isChecked() else 0)
        self._page_show()

    def _pdf_fill_text(self) -> None:
        if self.kind == "pptx":
            if 0 <= self._pdf_index < len(self._slides_text):
                _n, html = self._slides_text[self._pdf_index]
                doc = QTextDocument()
                doc.setHtml(html)
                self.pdf_text.setPlainText(doc.toPlainText())
            return
        self.pdf_text.setPlainText(
            pv.pdf_page_text(self._pdf_path, self._pdf_index))

    def _pdf_show_text(self, on: bool) -> None:
        self.pdf_inner.setCurrentIndex(1 if on else 0)
        if on:
            self._pdf_fill_text()

    def _pdf_zoom(self, factor: float) -> None:
        self._pdf_scale = max(0.25, min(6.0, self._pdf_scale * factor))
        self._page_show()

    def _pdf_rotate(self, delta: int) -> None:
        pv.pdf_rotate_pages(self._pdf_path, [self._pdf_index], delta)
        self._page_show()

    def _pdf_delete_page(self) -> None:
        ret = QMessageBox.question(
            self, tr("Удаление страницы"),
            tr("Удалить страницу {n} из {total}?").format(n=self._pdf_index + 1, total=self._pdf_count),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        pv.pdf_delete_pages(self._pdf_path, [self._pdf_index])
        self._pdf_count -= 1
        if self._pdf_count == 0:
            self.lbl_info.setText(tr("PDF: страниц не осталось"))
            self.reject()
            return
        self._pdf_index = min(self._pdf_index, self._pdf_count - 1)
        self._page_show()

    def _pdf_export(self) -> None:
        text, ok = QInputDialog.getText(
            self, tr("Экспорт страниц"),
            tr("Страницы (например 1-3,5; всего {total}):").format(total=self._pdf_count),
            text=str(self._pdf_index + 1))
        if not ok or not text.strip():
            return
        try:
            indices = pv.parse_ranges(text, self._pdf_count)
        except ValueError:
            QMessageBox.warning(self, tr("Экспорт"), tr("Неверный диапазон страниц"))
            return
        base = os.path.splitext(self._pdf_path)[0]
        out, _filter = QFileDialog.getSaveFileName(
            self, tr("Экспорт страниц PDF"), base + tr("-страницы.pdf"), "PDF (*.pdf)")
        if not out:
            return
        pv.pdf_export_pages(self._pdf_path, indices, out)
        QMessageBox.information(self, tr("Экспорт"),
                                tr("Сохранено страниц: {n}\n{out}").format(n=len(indices), out=out))

    # аннотации pdf ---------------------------------------------------------

    _ANNOT_BUTTONS = None  # заполняется в __init__ (mode → кнопка)

    def _annot_mode(self, mode: str, on: bool) -> None:
        if self._ANNOT_BUTTONS is None:
            return
        if not on:
            if self.pdf_label.annot_mode == mode:
                self.pdf_label.set_annot_mode(None)
            return
        for m, btn in self._ANNOT_BUTTONS.items():
            if m != mode:
                btn.setChecked(False)
        self.pdf_label.set_annot_mode(mode)

    def _pdf_rect_from_widget(self, rect: QRect) -> tuple:
        """Прямоугольник виджета (пиксели) → координаты PDF (пункты, y вверх)."""
        scale = max(0.01, self._pdf_scale)
        _w, h_pt = self._pdf_page_wh
        x0, x1 = sorted((rect.left() / scale, rect.right() / scale))
        y0, y1 = sorted((h_pt - rect.bottom() / scale,
                         h_pt - rect.top() / scale))
        return (round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1))

    def _annot_from_rect(self, rect: QRect) -> None:
        mode = self.pdf_label.annot_mode
        if self.kind != "pdf" or mode not in ("highlight", "freetext"):
            return
        prect = self._pdf_rect_from_widget(rect)
        if mode == "highlight":
            try:
                pv.pdf_add_annotation(self._pdf_path, self._pdf_index,
                                      "highlight", prect)
            except Exception as exc:
                QMessageBox.warning(self, tr("Аннотации"),
                                    tr("Не удалось добавить: {exc}").format(exc=exc))
                return
            self._page_show()
            return
        text, ok = QInputDialog.getMultiLineText(
            self, tr("Текст на странице"), tr("Текст:"))
        if not (ok and text.strip()):
            return
        try:
            pv.pdf_add_annotation(self._pdf_path, self._pdf_index,
                                  "freetext", prect, text.strip())
        except Exception as exc:
            QMessageBox.warning(self, tr("Аннотации"),
                                tr("Не удалось добавить: {exc}").format(exc=exc))
            return
        self._page_show()

    def _annot_from_click(self, pos) -> None:
        if self.kind != "pdf" or self.pdf_label.annot_mode != "text":
            return
        text, ok = QInputDialog.getMultiLineText(
            self, tr("Заметка"), tr("Текст заметки:"))
        if not (ok and text.strip()):
            return
        scale = max(0.01, self._pdf_scale)
        _w, h_pt = self._pdf_page_wh
        x = pos.x() / scale
        y = h_pt - pos.y() / scale
        try:
            pv.pdf_add_annotation(self._pdf_path, self._pdf_index, "text",
                                  (round(x, 1), round(y, 1),
                                   round(x + 18, 1), round(y + 18, 1)),
                                  text.strip())
        except Exception as exc:
            QMessageBox.warning(self, tr("Аннотации"),
                                tr("Не удалось добавить: {exc}").format(exc=exc))
            return
        self._page_show()

    def _annot_list_dialog(self) -> None:
        """Список аннотаций файла с удалением выбранных."""
        if self.kind != "pdf":
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Аннотации PDF"))
        table = QTableView(dlg)
        model = AnnotListModel(self._pdf_path, dlg)
        table.setModel(model)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        btn_del = QPushButton(tr("Удалить выбранные"), dlg)
        btn_close = QPushButton(tr("Закрыть"), dlg)

        def delete_selected():
            rows = sorted({idx.row() for idx in table.selectionModel()
                           .selectedRows()}, reverse=True)
            for row in rows:
                item = model.annot_at(row)
                if item is None:
                    continue
                try:
                    pv.pdf_delete_annotation(self._pdf_path, item["page"],
                                             item["index"])
                except Exception as exc:
                    QMessageBox.warning(self, tr("Аннотации"),
                                        tr("Не удалось удалить: {exc}").format(exc=exc))
                    return
            model.reload(self._pdf_path)
            self._page_show()

        btn_del.clicked.connect(delete_selected)
        btn_close.clicked.connect(dlg.accept)
        layout = QVBoxLayout(dlg)
        layout.addWidget(table, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn_del)
        row.addWidget(btn_close)
        layout.addLayout(row)
        dlg.resize(560, 400)
        dlg.exec()

    # docx -------------------------------------------------------------------

    def _load_docx(self, path: str) -> None:
        self.kind = "docx"
        self.btn_wrap.show()
        if self.editable:
            lines = pv.docx_paragraphs(path)
            self.text_edit.setPlainText("\n".join(lines))
            self.text_edit.setReadOnly(False)
            self.stack.setCurrentIndex(0)
            self._info_base = tr(
                "docx: правка по абзацам (стиль абзаца сохраняется, "
                "встроенное форматирование меняемых абзацев теряется)")
            self.lbl_info.setText(self._info_base)
        else:
            self._set_document_html(pv.docx_to_html(path))
            self.lbl_info.setText(tr("docx: просмотр • F4 — правка по абзацам"))

    def _set_document_html(self, html: str) -> None:
        self.preview.setHtml(html)
        self.stack.setCurrentIndex(1)

    # epub / pptx ------------------------------------------------------------

    def _load_epub(self, path: str) -> None:
        self.kind = "epub"
        extract_dir = self._make_temp_dir()
        self._doc_items = pv.epub_chapters(path, extract_dir)
        if not self._doc_items:
            raise ValueError(tr("в книге нет текстовых глав"))
        self.doc_combo.blockSignals(True)
        self.doc_combo.clear()
        for title, _xhtml, _base in self._doc_items:
            self.doc_combo.addItem(title)
        self.doc_combo.blockSignals(False)
        self.doc_combo.show()
        self.doc_combo.setCurrentIndex(0)
        self._on_doc_combo(0)  # сигналы combo были заблокированы
        self.stack.setCurrentIndex(1)
        self.lbl_info.setText(tr("epub: глав {n} • изображения показываются, CSS упрощён").format(
                n=len(self._doc_items)))

    def _load_pptx(self, path: str) -> None:
        images_dir = self._make_temp_dir()
        deck = pv.pptx_slides_rich(path, images_dir)
        if not deck["slides"]:
            raise ValueError(tr("в презентации нет слайдов"))
        self.kind = "pptx"
        self._deck = deck
        self._slides_text = pv.pptx_slides(path)
        self._pdf_count = len(deck["slides"])
        self._pdf_index = 0
        self._pdf_scale = 1.0
        for b in (self.btn_pdf_prev, self.btn_pdf_next, self.btn_zoom_in,
                  self.btn_zoom_out, self.btn_pdf_text):
            b.show()
        for b in (self.btn_pdf_rot_left, self.btn_pdf_rot_right,
                  self.btn_pdf_delete, self.btn_pdf_export):
            b.hide()
        self.btn_pdf_text.setChecked(False)
        self.pdf_inner.setCurrentIndex(0)
        self.stack.setCurrentIndex(3)
        self.search.setEnabled(False)
        self.btn_replace_toggle.hide()
        self._page_show()

    def _on_doc_combo(self, index: int) -> None:
        if not 0 <= index < len(self._doc_items):
            return
        item = self._doc_items[index]
        if len(item) == 3:
            _title, html, base = item
            if base:
                self.preview.document().setBaseUrl(
                    QUrl.fromLocalFile(base + os.sep))
            self.preview.setHtml(html)
        else:
            _title, html = item
            self.preview.setHtml(html)

    # rtf / doc / xls ---------------------------------------------------------

    def _load_rtf(self, path: str) -> None:
        self.kind = "rtf"
        self.btn_wrap.show()
        text = lf.rtf_to_text(path)
        self.text_edit.setPlainText(text)
        self.text_edit.setReadOnly(not self.editable)
        self.stack.setCurrentIndex(0)
        self._info_base = tr("RTF • правка: абзацы (форматирование упрощается)")
        self.lbl_info.setText(self._info_base if self.editable
                              else tr("RTF • просмотр • F4 — правка"))
        self._update_cursor_status()

    def _load_doc(self, path: str) -> None:
        self.kind = "doc"
        self.btn_wrap.show()
        text = lf.doc_to_text(path)
        self.text_edit.setPlainText(text)
        self.text_edit.setReadOnly(not self.editable)
        self.stack.setCurrentIndex(0)
        self._info_base = tr("DOC • текст")
        self.lbl_info.setText(
            tr("DOC • правка: Ctrl+S предложит сохранить как RTF/DOCX")
            if self.editable else tr("DOC • просмотр (antiword/catdoc) • F4 — правка"))
        self._update_cursor_status()

    def _load_xls(self, path: str) -> None:
        self.kind = "xls"
        sheets = lf.xls_sheets(path)
        if not sheets:
            raise ValueError(tr("в книге нет листов"))
        self._xls_sheets_cache = sheets
        self._grid_sources = [("rows", name, rows) for name, rows in sheets]
        self.sheet_combo.blockSignals(True)
        self.sheet_combo.clear()
        for name, _rows in sheets:
            self.sheet_combo.addItem(name)
        self.sheet_combo.blockSignals(False)
        self.sheet_combo.setVisible(len(sheets) > 1)
        self.sheet_model.editable = self.editable
        self._select_sheet(0, info_base="XLS")
        self.stack.setCurrentIndex(4)

    # xlsx / csv ---------------------------------------------------------------

    def _load_xlsx(self, path: str) -> None:
        self.kind = "xlsx"
        names = pv.xlsx_sheet_names(path)
        if not names:
            raise ValueError(tr("в книге нет листов"))
        self._grid_sources = [("xlsx", path, name) for name in names]
        self.sheet_combo.blockSignals(True)
        self.sheet_combo.clear()
        for name in names:
            self.sheet_combo.addItem(name)
        self.sheet_combo.blockSignals(False)
        self.sheet_combo.setVisible(len(names) > 1)
        self._select_sheet(0)
        self._prefetch_other_sheets(path, names[1:])
        self.stack.setCurrentIndex(4)

    def _prefetch_other_sheets(self, path: str, names: list[str]) -> None:
        """Фоновая сырая загрузка остальных листов: переключение мгновенное,
        кросс-ссылки при пересчёте видят все листы."""
        def worker():
            for name in names:
                if name in self._xlsx_buffers:
                    continue
                try:
                    rows, formulas, cached = pv.xlsx_sheet_raw(path, name)
                except Exception:
                    continue
                self.gridBatch.emit({"prefetch": name, "rows": rows,
                                     "formulas": formulas, "cached": cached})

        threading.Thread(target=worker, daemon=True,
                         name="xlsx-prefetch").start()

    def _load_csv(self, path: str) -> None:
        self.kind = "csv"
        text, enc, truncated = text_preview(path)
        delim = pv.csv_sniff_delimiter(text)
        self._grid_sources = [("csv", text, delim)]
        self.sheet_combo.hide()
        note = tr(" (обрезано)") if truncated else ""
        self._info_base = tr("CSV: кодировка {enc}, разделитель {delim!r}{note}").format(enc=enc, delim=delim, note=note)
        self._select_sheet(0, info_base=self._info_base)
        self.stack.setCurrentIndex(4)

    def _select_sheet(self, index: int, info_base: str = "") -> None:
        if not 0 <= index < len(self._grid_sources):
            return
        kind, a, b = self._grid_sources[index]
        if kind == "rows":
            self._grid_current_name = None
            self.sheet_model.recalc = None
            self.sheet_model.editable = self.editable
            self.sheet_model.start_plain([list(r) for r in b])
            self._grid_info_base = info_base
            self.lbl_info.setText((info_base + " • " if info_base else "")
                                  + tr("строк: {n}").format(n=self.sheet_model.rowCount()))
            return
        if kind == "xlsx":
            self._sync_current_sheet_buffer()  # правки прежнего листа
            factory = lambda: pv.xlsx_sheet_raw(a, b)  # noqa: E731
            title = b
            xlsx_name = b
        else:
            self._grid_current_name = None
            self.sheet_model.recalc = None
            factory = lambda: pv.csv_rows_iter(a, b)  # noqa: E731
            title = "CSV"
            xlsx_name = None
        if xlsx_name is not None and xlsx_name in self._xlsx_buffers:
            self._apply_xlsx_buffer(xlsx_name, info_base)
            return
        self._start_grid_load(factory, title, info_base, sheet_name=xlsx_name)

    def _start_grid_load(self, factory, title: str, info_base: str = "",
                         sheet_name: str | None = None) -> None:
        self.sheet_model.reset_rows()
        self._grid_info_base = info_base
        self.lbl_info.setText((info_base + " • " if info_base else "")
                              + tr("чтение {title}…").format(title=title))
        self._grid_error = ""

        if sheet_name is not None:
            # xlsx: сырые ячейки целиком (формулы + кэши) одним куском
            self._grid_current_name = sheet_name  # батч применится к нему

            def worker():
                try:
                    rows, formulas, cached = factory()
                    self.gridBatch.emit({"sheet": sheet_name, "rows": rows,
                                         "formulas": formulas,
                                         "cached": cached, "done": True})
                except Exception as exc:  # битый файл — сообщим в статус
                    self.gridBatch.emit({"sheet": sheet_name, "rows": [],
                                         "formulas": {}, "cached": {},
                                         "done": True, "error": str(exc)})
        else:
            def worker():
                batch: list[list[str]] = []
                try:
                    for row in factory():
                        batch.append(row)
                        if len(batch) >= pv.GRID_BATCH:
                            self.gridBatch.emit({"rows": batch})
                            batch = []
                    self.gridBatch.emit({"rows": batch, "done": True})
                except Exception as exc:  # битый файл — сообщим в статус
                    self.gridBatch.emit({"rows": batch, "done": True,
                                         "error": str(exc)})

        threading.Thread(target=worker, daemon=True, name="grid-load").start()

    @staticmethod
    def _xlsx_display_rows(rows, formulas, computed, cached) -> list[list[str]]:
        """Что показать: вычисленное/кэшированное значение формулы или сырьё."""
        from sphaera_commander import formulas as fm

        out = []
        for r, row in enumerate(rows):
            disp = []
            for c, raw in enumerate(row):
                if (r, c) in formulas:
                    if (r, c) in computed:
                        disp.append(fm.fmt_value(computed[(r, c)]))
                    elif (r, c) in cached:
                        disp.append(fm.fmt_value(cached[(r, c)]))
                    else:
                        disp.append(raw)  # нет кэша — честно показать формулу
                else:
                    disp.append(fm.fmt_value(fm.cell_value(raw)))
            out.append(disp)
        return out

    def _apply_xlsx_buffer(self, name: str, info_base: str = "") -> None:
        buf = self._xlsx_buffers[name]
        self._grid_current_name = name
        self.sheet_model.recalc = self._recalc_sheet
        self.sheet_model.editable = self.editable
        display = self._xlsx_display_rows(buf["raw"], buf["formulas"],
                                          buf.get("computed") or {},
                                          buf["cached"])
        self.sheet_model.start_xlsx(buf["raw"], buf["formulas"],
                                    buf.get("computed") or {}, display)
        self.btn_grid_raw.setVisible(True)
        base = info_base or self._grid_info_base
        self.lbl_info.setText(
            (base + " • " if base else "")
            + tr("строк: {n} • формул: {f}").format(
                n=self.sheet_model.rowCount(), f=len(buf["formulas"])))

    def _sync_current_sheet_buffer(self) -> None:
        """Правки сетки текущего листа — в буфер (перед переключением/сохранением)."""
        name = getattr(self, "_grid_current_name", None)
        if name is None or self.sheet_model.raw is None:
            return
        buf = self._xlsx_buffers.get(name)
        if buf is None:
            return
        buf["raw"] = [list(r) for r in self.sheet_model.raw]
        buf["formulas"] = dict(self.sheet_model.formulas)
        buf["computed"] = dict(self.sheet_model.computed)

    def _xlsx_context(self):
        from sphaera_commander import formulas as fm

        return fm.Context({name: buf["raw"]
                           for name, buf in self._xlsx_buffers.items()})

    def _recalc_sheet(self) -> None:
        """Пересчёт листа после правки ячейки (hook модели)."""
        from sphaera_commander import formulas as fm

        name = self._grid_current_name
        buf = self._xlsx_buffers.get(name)
        if buf is None or self.sheet_model.raw is None:
            return
        rows = [list(r) for r in self.sheet_model.raw]
        vals, _n, failed, unstable = fm.evaluate_sheet(
            rows, self._xlsx_context(), name, seed=buf["cached"])
        # не пересчитавшееся (цикл, непонятная формула) — прежний кэш файла
        for coord in list(failed) + list(unstable):
            if coord in buf["cached"]:
                vals[coord] = buf["cached"][coord]
        buf["raw"] = rows
        buf["formulas"] = dict(self.sheet_model.formulas)
        buf["computed"] = vals
        self._xlsx_dirty.add(name)
        self.sheet_model.apply_computed(
            vals, self._xlsx_display_rows(rows, buf["formulas"], vals,
                                          buf["cached"]))

    def _toggle_grid_raw(self, on: bool) -> None:
        self.sheet_model.show_raw = on
        if self.sheet_model.raw is not None and self.sheet_model.rowCount():
            self.sheet_model.dataChanged.emit(
                self.sheet_model.index(0, 0),
                self.sheet_model.index(self.sheet_model.rowCount() - 1,
                                       self.sheet_model.columnCount() - 1))

    def _on_grid_batch(self, payload: dict) -> None:
        if "prefetch" in payload:
            name = payload["prefetch"]
            if name in self._xlsx_buffers:
                return
            self._xlsx_buffers[name] = {
                "raw": [list(r) for r in payload["rows"]],
                "formulas": dict(payload["formulas"]),
                "cached": dict(payload["cached"]),
                "orig": [list(r) for r in payload["rows"]],
            }
            return
        if "sheet" in payload:
            name = payload["sheet"]
            if payload.get("error"):
                self._grid_error = payload["error"]
                self.lbl_info.setText(tr("ошибка чтения: {err}").format(err=payload["error"]))
                return
            self._xlsx_buffers[name] = {
                "raw": [list(r) for r in payload["rows"]],
                "formulas": dict(payload["formulas"]),
                "cached": dict(payload["cached"]),
                "orig": [list(r) for r in payload["rows"]],
            }
            if self._grid_current_name == name:
                self._apply_xlsx_buffer(name)
            return
        self.sheet_model.append_rows(payload.get("rows", []))
        if payload.get("done"):
            if payload.get("error"):
                self._grid_error = payload["error"]
                self.lbl_info.setText(tr("ошибка чтения: {err}").format(err=payload["error"]))
            else:
                base = self._grid_info_base
                self.lbl_info.setText((base + " • " if base else "")
                                      + tr("строк: {n}").format(n=self.sheet_model.rowCount()))

    def _on_sheet_changed(self, index: int) -> None:
        self._select_sheet(index)

    # html / fb2 -------------------------------------------------------------

    def _load_html(self, path: str) -> None:
        self.kind = "html"
        text, enc, _trunc = text_preview(path)
        self.preview.document().setBaseUrl(
            QUrl.fromLocalFile(os.path.dirname(path) or "."))
        self.preview.setHtml(text)
        self.stack.setCurrentIndex(1)
        self.lbl_info.setText(tr("HTML • кодировка: {enc}").format(enc=enc))

    def _load_fb2(self, path: str) -> None:
        self.kind = "fb2"
        title, html = pv.fb2_html(path)
        heading = f"<h2>{escape(title)}</h2>" if title else ""
        self.preview.setHtml(heading + html)
        self.stack.setCurrentIndex(1)
        self.lbl_info.setText(f"fb2: {title or os.path.basename(path)}")

    # xml ----------------------------------------------------------------------

    def _load_xml(self, path: str) -> None:
        self.kind = "xml"
        self.btn_wrap.show()
        self.btn_tree.show()
        text, enc, truncated = text_preview(path)
        self.encoding = enc
        self.text_edit.setPlainText(text)
        self.text_edit.setReadOnly(not self.editable)
        self._info_base = tr("XML • кодировка: {enc}").format(enc=enc) + (tr(" (обрезано)") if truncated else "")
        self.lbl_info.setText(self._info_base)
        self.stack.setCurrentIndex(0)

    def _fill_xml_tree(self) -> bool:
        try:
            _root_name, node = pv.xml_tree(self._xml_path())
        except ET.ParseError as exc:
            self.lbl_info.setText(f"{self._info_base} • " + tr("дерево недоступно: {exc}").format(exc=exc))
            return False

        def add(parent, node):
            tag, text, children = node
            item = QTreeWidgetItem([tag, text])
            for child in children:
                add(item, child)
            parent.addChild(item)
            return item

        self.tree.clear()
        add(self.tree.invisibleRootItem(), node)
        self.tree.expandToDepth(0)
        return True

    def _xml_path(self) -> str:
        return self.files[self.index]


    # -- markdown ----------------------------------------------------------

    def _render_markdown(self, text: str) -> None:
        self.preview.document().setMarkdown(text)
        self._style_markdown_tables(self.preview.document())

    @staticmethod
    def _style_markdown_tables(document) -> None:
        """Реконструкция таблиц GFM: рамки, отступы, выделенная шапка."""
        dark = FileViewerDialog._palette_is_dark()
        border = QColor(80, 80, 80) if dark else QColor(160, 160, 160)
        header_bg = QColor(52, 52, 56) if dark else QColor(236, 236, 236)
        for table in find_markdown_tables(document):
            fmt = table.format()
            fmt.setBorder(1)
            fmt.setBorderBrush(border)
            fmt.setBorderStyle(QTextFrameFormat.BorderStyle_Solid)
            fmt.setBorderCollapse(True)
            fmt.setCellPadding(6)
            fmt.setTopMargin(8)
            fmt.setBottomMargin(8)
            table.setFormat(fmt)
            if table.rows() == 0:
                continue
            for col in range(table.columns()):
                cell = table.cellAt(0, col)
                cursor = QTextCursor(cell.firstCursorPosition())
                cursor.setPosition(cell.lastCursorPosition().position(),
                                   QTextCursor.KeepAnchor)
                char_fmt = QTextCharFormat()
                char_fmt.setFontWeight(QFont.Bold)
                char_fmt.setBackground(header_bg)
                cursor.mergeCharFormat(char_fmt)

    def _set_preview(self, on: bool, force: bool = False) -> None:
        if self.kind != "md" and not force:
            return
        if on:
            self._render_markdown(self.text_edit.toPlainText())
        self.stack.setCurrentIndex(1 if on else 0)
        self.btn_preview.setText(tr("Исходник") if on else tr("Предпросмотр"))
        if not force:
            self.btn_preview.setChecked(on)

    def _render_live_md(self) -> None:
        """Живой рендер markdown (таблиц в том числе) рядом с исходником."""
        if self.kind != "md" or self.md_live.isHidden():
            return
        bar = self.text_edit.verticalScrollBar()
        frac = bar.value() / max(1, bar.maximum())
        self.md_live.document().setMarkdown(self.text_edit.toPlainText())
        self._style_markdown_tables(self.md_live.document())
        lbar = self.md_live.verticalScrollBar()
        lbar.setValue(round(frac * max(0, lbar.maximum())))

    def _toggle_preview(self, on: bool) -> None:
        self._set_preview(on)

    def _toggle_wrap(self, on: bool) -> None:
        self.text_edit.setLineWrapMode(
            QPlainTextEdit.WidgetWidth if on else QPlainTextEdit.NoWrap)

    def _fit_image(self) -> None:
        if self._image is not None:
            scaled = self._image.scaled(self.image_label.size(),
                                        Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.image_label.setPixmap(scaled)

    def resizeEvent(self, event):
        self._fit_image()
        super().resizeEvent(event)

    # -- json --------------------------------------------------------------

    def _update_json_status(self) -> None:
        if self.kind != "json":
            return
        text = self.text_edit.toPlainText()
        if len(text) > LIVE_VALIDATE_LIMIT:
            self.lbl_info.setText(self._info_base)
            return
        error = json_error_position(text)
        suffix = (tr(" • JSON: OK") if error is None
                  else tr(" • JSON: ошибка ({err})").format(err=error))
        self.lbl_info.setText(self._info_base + suffix)

    def _format_json(self) -> None:
        if self.kind != "json":
            return
        text = self.text_edit.toPlainText()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            self.lbl_info.setText(
                f"{self._info_base} • "
                + tr("не отформатировано — ошибка (строка {line}, столбец {col}): {msg}").format(
                    line=exc.lineno, col=exc.colno, msg=exc.msg))
            return
        except (ValueError, RecursionError) as exc:
            self.lbl_info.setText(f"{self._info_base} • " + tr("не отформатировано: {exc}").format(exc=exc))
            return
        pretty = json.dumps(data, ensure_ascii=False, indent=4) + "\n"
        self.text_edit.setPlainText(pretty)
        self.text_edit.moveCursor(QTextCursor.Start)
        self._update_json_status()
        if self.btn_tree.isChecked():
            self._build_json_tree()

    def _toggle_tree(self, on: bool) -> None:
        if self.kind == "json":
            if on:
                if not self._build_json_tree():
                    self.btn_tree.setChecked(False)
                    return
                self.stack.setCurrentIndex(2)
            else:
                self.stack.setCurrentIndex(0)
            return
        if self.kind == "xml":
            if on:
                if not self._fill_xml_tree():
                    self.btn_tree.setChecked(False)
                    return
                self.stack.setCurrentIndex(2)
            else:
                self.stack.setCurrentIndex(0)

    def _build_json_tree(self) -> bool:
        """Наполнить дерево; False — JSON не разбирается."""
        try:
            data = json.loads(self.text_edit.toPlainText())
        except (json.JSONDecodeError, ValueError, RecursionError) as exc:
            self.lbl_info.setText(f"{self._info_base} • " + tr("дерева нет — ошибка: {exc}").format(exc=exc))
            self.tree.clear()
            return False
        self.tree.clear()
        collect_json_tree(data, self.tree.invisibleRootItem())
        self.tree.expandToDepth(0)
        return True

    # -- поиск/замена/переход ------------------------------------------------

    def _find_next(self) -> None:
        needle = self.search.text()
        if needle and not self.text_edit.find(needle):
            self.text_edit.moveCursor(QTextCursor.Start)
            if not self.text_edit.find(needle):
                self.lbl_info.setText(tr("не найдено: {needle}").format(needle=needle))

    def _replace_one(self) -> None:
        needle, replacement = self.search.text(), self.replace_edit.text()
        if not needle or self.text_edit.isReadOnly():
            return
        cursor = self.text_edit.document().find(needle, self.text_edit.textCursor())
        if not not cursor.isNull():
            self._find_next()  # с начала документа
            cursor = self.text_edit.document().find(needle, self.text_edit.textCursor())
            if not not cursor.isNull():
                self.lbl_info.setText(tr("не найдено: {needle}").format(needle=needle))
                return
        cursor.insertText(replacement)
        self.text_edit.setTextCursor(cursor)

    def _replace_all(self) -> None:
        needle, replacement = self.search.text(), self.replace_edit.text()
        if not needle or self.text_edit.isReadOnly():
            return
        document = self.text_edit.document()
        cursor = document.find(needle, QTextCursor(document))
        count = 0
        while not cursor.isNull():
            cursor.insertText(replacement)
            count += 1
            cursor = document.find(needle, cursor)
        self.lbl_info.setText(tr("заменено: {n}").format(n=count))

    def _ask_goto_line(self) -> None:
        if self.text_edit.isReadOnly():
            return
        line, ok = QInputDialog.getInt(
            self, tr("Перейти на строку"), tr("Номер строки:"),
            value=self.text_edit.textCursor().blockNumber() + 1,
            min=1, max=max(1, self.text_edit.document().blockCount()))
        if ok:
            self._goto_line(line)

    def _goto_line(self, line: int) -> None:
        block = self.text_edit.document().findBlockByNumber(max(0, line - 1))
        cursor = QTextCursor(block)
        self.text_edit.setTextCursor(cursor)
        self.text_edit.centerCursor()

    def _update_cursor_status(self) -> None:
        cursor = self.text_edit.textCursor()
        self.lbl_pos.setText(
            tr("строка {line}, столбец {col}").format(
                line=cursor.blockNumber() + 1, col=cursor.positionInBlock() + 1))

    def _make_temp_dir(self) -> str:
        d = tempfile.mkdtemp(prefix="sphaera-view-")
        self._temp_dirs.append(d)
        return d

    def done(self, result) -> None:  # accept() и reject() проходят через done()
        for d in self._temp_dirs:
            shutil.rmtree(d, ignore_errors=True)
        self._temp_dirs.clear()
        super().done(result)

    def _open_external(self) -> None:
        """Ctrl+E: открыть файл системным приложением (член архива — через temp)."""
        path = self.files[self.index]
        if self.kind in ("text", "md", "json", "image", "binary"):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # -- навигация -----------------------------------------------------------

    def navigate(self, delta: int) -> None:
        if not self.files:
            return
        self.index = (self.index + delta) % len(self.files)
        self.load_current()

    def _save_xlsx(self) -> None:
        """Сохранить книгу: правленые листы + пересчёт формул всех листов
        с вписыванием кэшированных значений (openpyxl их не пишет)."""
        from sphaera_commander import formulas as fm

        path = self.files[self.index]
        self._sync_current_sheet_buffer()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            names = pv.xlsx_sheet_names(path)
            for name in names:  # не загруженные листы — для кэшей и ссылок
                if name not in self._xlsx_buffers:
                    rows, formulas, cached = pv.xlsx_sheet_raw(path, name)
                    self._xlsx_buffers[name] = {
                        "raw": rows, "formulas": formulas, "cached": cached,
                        "orig": [list(r) for r in rows]}
            edited = {name: self._xlsx_buffers[name]["raw"]
                      for name in self._xlsx_dirty
                      if name in self._xlsx_buffers}
            originals = {name: self._xlsx_buffers[name]["orig"]
                         for name in edited}
            all_values: dict[str, dict] = {}
            failed_total = 0
            ctx = fm.Context({name: [] for name in names})
            for _round in range(2):  # два прохода: сходимость кросс-ссылок
                all_values.clear()
                for name in names:
                    buf = self._xlsx_buffers[name]
                    vals, _n, failed, unstable = fm.evaluate_sheet(
                        buf["raw"], ctx, name, seed=buf["cached"])
                    for coord in list(failed) + list(unstable):
                        if coord in buf["cached"]:
                            vals[coord] = buf["cached"][coord]
                    all_values[name] = vals
                    failed_total = len(failed) + len(unstable)
            if edited:
                pv.xlsx_save_cells(path, edited, originals)
            pv.xlsx_inject_cached(path, all_values)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, tr("Ошибка"),
                                 tr("Не удалось сохранить xlsx:\n{exc}").format(exc=exc))
            return
        QApplication.restoreOverrideCursor()
        for name in self._xlsx_dirty:
            buf = self._xlsx_buffers.get(name)
            if buf is not None:
                buf["orig"] = [list(r) for r in buf["raw"]]
        self._xlsx_dirty.clear()
        self.sheet_model.dirty = False
        note = (tr(" • без пересчёта: {n}").format(n=failed_total)
                if failed_total else "")
        self.lbl_info.setText(tr("сохранено (xlsx{note})").format(note=note))

    def _save(self) -> None:
        if not self.editable or self.text_edit.isReadOnly():
            return
        if self._image is not None:
            return
        path = self.files[self.index]
        text = self.text_edit.toPlainText()
        if self.kind == "xlsx":
            self._save_xlsx()
            return
        if self.kind == "xls":
            ret = QMessageBox.question(
                self, tr("Сохранение XLS"),
                tr("Формулы и стили будут заменены значениями. Продолжить?"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                return
            current = self.sheet_combo.currentIndex()
            sheets_out = []
            for i, (name, data) in enumerate(self._xls_sheets_cache):
                rows = (self.sheet_model.rows()
                        if i == current else data)
                sheets_out.append((name, [list(r) for r in rows]))
            try:
                lf.xls_save(path, sheets_out)
            except Exception as exc:
                QMessageBox.critical(self, tr("Ошибка"),
                                     tr("Не удалось сохранить XLS:\n{exc}").format(exc=exc))
                return
            self.sheet_model.dirty = False
            self.lbl_info.setText(tr("сохранено (XLS, значения)"))
            return
        if self.kind == "rtf":
            try:
                lf.save_rtf(path, text.splitlines())
            except OSError as exc:
                QMessageBox.critical(self, tr("Ошибка"),
                                     tr("Не удалось сохранить RTF:\n{exc}").format(exc=exc))
                return
            self.text_edit.document().setModified(False)
            self.lbl_info.setText(tr("сохранено (RTF)"))
            return
        if self.kind == "doc":
            base = os.path.splitext(path)[0]
            out, _flt = QFileDialog.getSaveFileName(
                self, tr("Двоичный DOC перезаписать нельзя — сохранить как"),
                base + ".rtf", "RTF (*.rtf);;DOCX (*.docx)")
            if not out:
                return
            try:
                if out.lower().endswith(".docx"):
                    from docx import Document

                    d = Document()
                    for line in text.splitlines():
                        d.add_paragraph(line)
                    d.save(out)
                else:
                    lf.save_rtf(out, text.splitlines())
            except Exception as exc:
                QMessageBox.critical(self, tr("Ошибка"),
                                     tr("Не удалось сохранить:\n{exc}").format(exc=exc))
                return
            self.text_edit.document().setModified(False)
            self.lbl_info.setText(tr("сохранено: {name}").format(name=os.path.basename(out)))
            return
        if self.kind == "docx":
            try:
                pv.docx_save_paragraphs(path, text.splitlines())
            except Exception as exc:
                QMessageBox.critical(self, tr("Ошибка"),
                                     tr("Не удалось сохранить docx:\n{exc}").format(exc=exc))
                return
            self.text_edit.document().setModified(False)
            self.lbl_info.setText(tr("сохранено (docx)"))
            return
        if self.kind == "json":
            error = json_error_position(text)
            if error is not None:
                ret = QMessageBox.question(
                    self, tr("JSON некорректен"),
                    tr("В файле ошибка JSON ({err}).\nСохранить как есть?").format(err=error),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if ret != QMessageBox.Yes:
                    return
        try:
            data = text.encode(self.encoding)
        except UnicodeEncodeError:
            ret = QMessageBox.question(
                self, tr("Кодировка"),
                tr("Текст не сохраняется в {enc} без потерь.\nСохранить как UTF-8?").format(
                    enc=self.encoding))
            if ret == QMessageBox.Yes:
                self.encoding = "utf-8"
                data = text.encode("utf-8")
            else:
                return
        try:
            with open(path, "wb") as f:
                f.write(data)
        except OSError as exc:
            QMessageBox.critical(self, tr("Ошибка"), tr("Не удалось сохранить:\n{exc}").format(exc=exc))
            return
        self.text_edit.document().setModified(False)
        self.lbl_info.setText(tr("сохранено ({enc})").format(enc=self.encoding))
        if self.kind == "md" and self.btn_preview.isChecked():
            self._render_markdown(text)

    def reject(self) -> None:
        if self.kind == "xlsx" and self._xlsx_dirty:
            ret = QMessageBox.question(
                self, tr("Есть изменения"),
                tr("Таблица изменена. Сохранить xlsx (формулы сохраняются)?"),
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Cancel)
            if ret == QMessageBox.Cancel:
                return
            if ret == QMessageBox.Yes:
                self._save()
                if self._xlsx_dirty:
                    return
        grid_dirty = self.kind == "xls" and self.sheet_model.dirty
        if grid_dirty:
            ret = QMessageBox.question(
                self, tr("Есть изменения"),
                tr("Таблица изменена. Сохранить XLS (формулы → значения)?"),
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Cancel)
            if ret == QMessageBox.Cancel:
                return
            if ret == QMessageBox.Yes:
                self._save()
                if self.sheet_model.dirty:
                    return
        if (self.editable and not self.text_edit.isReadOnly()
                and self.text_edit.document().isModified()):
            ret = QMessageBox.question(
                self, tr("Есть изменения"),
                tr("Файл изменён. Сохранить перед закрытием?"),
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Cancel)
            if ret == QMessageBox.Cancel:
                return
            if ret == QMessageBox.Yes:
                self._save()
                if self.text_edit.document().isModified():
                    return  # сохранение не удалось — остаёмся
                self.saved_on_close = True
        super().reject()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not self.editable:
            self.accept()
            return
        super().keyPressEvent(event)


def open_viewer(parent, path: str, all_files: list[str] | None = None,
                editable: bool = False, goto_line: int = 0,
                modal: bool = True) -> FileViewerDialog:
    files = all_files if all_files else [path]
    if path not in files:
        files = [path] + files
    index = files.index(path)
    dlg = FileViewerDialog(parent, files, index, editable, goto_line=goto_line,
                           modal=modal)
    return dlg
