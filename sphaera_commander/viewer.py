"""Встроенный просмотр (F3) и правка (F4).

Редактор: моноширинный шрифт, номера строк, подсветка текущей строки, поиск
с заменой (Ctrl+H), переход на строку (Ctrl+G), позиция курсора, перенос строк.
Текст: автоопределение кодировки (utf-8 → utf-16 по BOM → cp1251 → latin-1),
файлы больше лимита показываются частично с явной пометкой.
Markdown: рендер предпросмотра (QTextDocument.setMarkdown); таблицы GFM
реконструируются с рамками и выделенной шапкой.
JSON: подсветка синтаксиса, форматирование (Ctrl+Shift+F), валидация с позицией
ошибки, сворачиваемое дерево (Ctrl+T); при сохранении некорректного JSON —
явный вопрос. Ctrl+E — открыть файл во внешнем приложении.
Бинарные: hex-обзор (первые 2 МиБ). Изображения: масштабируемый просмотр.
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
    QScrollArea,
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

TEXT_LIMIT = 16 * 1024 * 1024   # показываем не больше 16 МиБ текста
HEX_LIMIT = 2 * 1024 * 1024     # и 2 МиБ hex-обзора
HEX_ROW = 16
LIVE_VALIDATE_LIMIT = 2 * 1024 * 1024
TREE_NODE_LIMIT = 20000

MD_EXTS = (".md", ".markdown")
JSON_EXTS = (".json", ".jsonc")


def looks_binary(raw: bytes) -> bool:
    chunk = raw[:8192]
    if not chunk:
        return False
    # NUL и управляющие байты (кроме \t \n \f \r) — признак бинарных данных
    suspicious = sum(1 for b in chunk if b < 9 or 14 <= b <= 31 or b == 11 or b == 12)
    return suspicious / len(chunk) > 0.05


def detect_decode(raw: bytes) -> tuple[str, str]:
    """(текст, имя кодировки)."""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16"), "utf-16"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp1251"), "cp1251"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def hexdump(raw: bytes) -> str:
    lines = []
    for off in range(0, len(raw), HEX_ROW):
        chunk = raw[off:off + HEX_ROW]
        hex_part = " ".join(f"{b:02x}" for b in chunk).ljust(HEX_ROW * 3 - 1)
        text = "".join(chr(b) if 32 <= b < 127 else "·" for b in chunk)
        lines.append(f"{off:08x}  {hex_part}  |{text}|")
    return "\n".join(lines)


def text_preview(path: str) -> tuple[str, str, bool]:
    """(содержимое для показа, кодировка, обрезано ли)."""
    with open(path, "rb") as f:
        raw = f.read(TEXT_LIMIT + 1)
    truncated = len(raw) > TEXT_LIMIT
    raw = raw[:TEXT_LIMIT]
    text, enc = detect_decode(raw)
    if truncated:
        text += (f"\n\n[… показаны первые {TEXT_LIMIT // (1024 * 1024)} МиБ "
                 f"файла — остальное скрыто …]")
    return text, enc, truncated


def json_error_position(text: str) -> str | None:
    """Описание ошибки JSON с позицией (строка/столбец) или None, если всё хорошо."""
    try:
        json.loads(text)
        return None
    except json.JSONDecodeError as exc:
        return f"строка {exc.lineno}, столбец {exc.colno}: {exc.msg}"
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
            item = QTreeWidgetItem([str(key), f"объект · {len(value)}"])
        elif isinstance(value, list):
            item = QTreeWidgetItem([str(key), f"массив · {len(value)}"])
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
    сотни тысяч строк, QTableView рисует только видимое."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[list[str]] = []
        self._cols = 0

    def reset_rows(self) -> None:
        self.beginResetModel()
        self._rows = []
        self._cols = 0
        self.endResetModel()

    def append_rows(self, rows: list[list[str]]) -> None:
        if not rows:
            return
        first = len(self._rows)
        self.beginInsertRows(QModelIndex(), first, first + len(rows) - 1)
        self._rows.extend(rows)
        self._cols = max(self._cols, max((len(r) for r in rows), default=0))
        self.endInsertRows()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self._cols

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        row = self._rows[index.row()]
        col = index.column()
        return row[col] if col < len(row) else None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return str(section + 1)
        return None


class FileViewerDialog(QDialog):
    """Просмотр/правка одного файла; files — список для навигации след/пред."""

    gridBatch = Signal(object)

    def __init__(self, parent, files: list[str], index: int, editable: bool):
        super().__init__(parent)
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
        self._deck: dict | None = None
        self._slides_text: list[tuple[int, str]] = []
        self.gridBatch.connect(self._on_grid_batch)
        self._json_timer = QTimer(self)
        self._json_timer.setSingleShot(True)
        self._json_timer.setInterval(500)
        self._json_timer.timeout.connect(self._update_json_status)

        self.setModal(True)
        self.resize(980, 680)

        self.text_edit = LineNumberTextEdit()
        self.text_edit.setReadOnly(not editable)
        self.text_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text_edit.textChanged.connect(self._json_timer.start)
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPointSize(max(9, font.pointSize() or 10))
        self.text_edit.setFont(font)

        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(True)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(("Узел", "Значение"))
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
        self.pdf_label = QLabel()
        self.pdf_label.setAlignment(Qt.AlignCenter)
        self.pdf_scroll = QScrollArea()
        self.pdf_scroll.setWidgetResizable(True)
        self.pdf_scroll.setWidget(self.pdf_label)
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
        self.btn_pdf_text = QPushButton("Текст")
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
        self.btn_pdf_delete = QPushButton("Удалить страницу")
        self.btn_pdf_delete.clicked.connect(self._pdf_delete_page)
        self.btn_pdf_export = QPushButton("Экспорт страниц…")
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
        pdf_page = QWidget()
        pdf_layout = QVBoxLayout(pdf_page)
        pdf_layout.setContentsMargins(0, 0, 0, 0)
        pdf_layout.addLayout(pdf_bar)
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
        grid_page = QWidget()
        grid_layout = QVBoxLayout(grid_page)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.addWidget(self.sheet_combo)
        grid_layout.addWidget(self.grid, 1)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.text_edit)  # 0 — исходник/текст
        self.stack.addWidget(doc_page)        # 1 — рендер (md/html/fb2/epub/pptx)
        self.stack.addWidget(self.tree)       # 2 — дерево json/xml
        self.stack.addWidget(pdf_page)        # 3 — pdf
        self.stack.addWidget(grid_page)       # 4 — csv/xlsx

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)

        # -- строка поиска/замены
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск (Enter — далее)")
        self.search.returnPressed.connect(self._find_next)
        btn_find = QPushButton("Найти")
        btn_find.clicked.connect(self._find_next)

        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("Заменить на")
        btn_replace = QPushButton("Заменить")
        btn_replace.clicked.connect(self._replace_one)
        btn_replace_all = QPushButton("Все")
        btn_replace_all.clicked.connect(self._replace_all)
        self.replace_row = QWidget()
        rrow = QHBoxLayout(self.replace_row)
        rrow.setContentsMargins(0, 0, 0, 0)
        rrow.addWidget(self.replace_edit, 1)
        rrow.addWidget(btn_replace)
        rrow.addWidget(btn_replace_all)
        self.replace_row.hide()

        # -- кнопки режима
        self.btn_replace_toggle = QPushButton("Заменить…")
        self.btn_replace_toggle.setCheckable(True)
        self.btn_replace_toggle.setShortcut(QKeySequence("Ctrl+H"))
        self.btn_replace_toggle.toggled.connect(self.replace_row.setVisible)

        self.btn_wrap = QPushButton("Перенос")
        self.btn_wrap.setCheckable(True)
        self.btn_wrap.setToolTip("Переносить длинные строки")
        self.btn_wrap.toggled.connect(self._toggle_wrap)

        self.btn_format = QPushButton("Форматировать")
        self.btn_format.setShortcut(QKeySequence("Ctrl+Shift+F"))
        self.btn_format.setToolTip("Форматировать JSON (Ctrl+Shift+F)")
        self.btn_format.clicked.connect(self._format_json)

        self.btn_tree = QPushButton("Дерево")
        self.btn_tree.setCheckable(True)
        self.btn_tree.setShortcut(QKeySequence("Ctrl+T"))
        self.btn_tree.setToolTip("Дерево JSON (Ctrl+T)")
        self.btn_tree.toggled.connect(self._toggle_tree)

        self.btn_preview = QPushButton("Предпросмотр")
        self.btn_preview.setCheckable(True)
        self.btn_preview.setShortcut(QKeySequence("Ctrl+Shift+P"))
        self.btn_preview.setToolTip("Исходник/предпросмотр (Ctrl+Shift+P)")
        self.btn_preview.toggled.connect(self._toggle_preview)

        btn_prev = QPushButton("← Пред. (Alt+↑)")
        btn_next = QPushButton("След. (Alt+↓) →")
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
        self.setWindowTitle(f"{'Правка' if self.editable else 'Просмотр'}: {path}")
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
            self.lbl_info.setText(f"изображение {img.width()}×{img.height()}")
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
            self.text_edit.setPlainText(f"<не удалось прочитать файл: {exc}>")
            return
        if looks_binary(head):
            self.kind = "binary"
            self.btn_replace_toggle.hide()
            self.btn_wrap.hide()
            self.text_edit.setPlainText(hexdump(raw[:HEX_LIMIT])
                                        + ("\n[… hex-обзор ограничен 2 МиБ …]"
                                           if len(raw) > HEX_LIMIT else ""))
            self.lbl_info.setText("бинарный файл (hex-обзор)")
            self.text_edit.setReadOnly(True)
            return

        text, enc, truncated = text_preview(path)
        self.encoding = enc
        self.text_edit.setPlainText(text)
        self.text_edit.setReadOnly(not self.editable)
        self._info_base = f"кодировка: {enc}" + (" (обрезано)" if truncated else "")
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

    def _load_document(self, path: str, kind: str) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            loader = {
                "pdf": self._load_pdf,
                "docx": self._load_docx,
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
            self.text_edit.setPlainText(f"<не удалось открыть ({kind}):\n{exc}>")
            self.lbl_info.setText("ошибка открытия")
            return
        QApplication.restoreOverrideCursor()
        self._update_cursor_status()

    # pdf ------------------------------------------------------------------

    def _load_pdf(self, path: str) -> None:
        self.kind = "pdf"
        self._pdf_path = path
        self._pdf_count = pv.pdf_page_count(path)
        if self._pdf_count == 0:
            raise ValueError("в файле нет страниц")
        self._pdf_index = 0
        self._pdf_scale = 2.0
        for b in (self.btn_pdf_prev, self.btn_pdf_next, self.btn_zoom_in,
                  self.btn_zoom_out, self.btn_pdf_text):
            b.show()
        for b in (self.btn_pdf_rot_left, self.btn_pdf_rot_right,
                  self.btn_pdf_delete, self.btn_pdf_export):
            b.setVisible(self.editable)
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
                f"слайд {self._pdf_index + 1} из {self._pdf_count}")
            self.lbl_info.setText(f"pptx: {self._pdf_count} слайд(ов) • "
                                  f"масштаб {self._pdf_scale:.2f}x • свой рендер")
            return
        data, w, h, stride = pv.pdf_render(self._pdf_path, self._pdf_index,
                                           self._pdf_scale)
        img = QImage(data, w, h, stride, QImage.Format.Format_BGR888)
        self.pdf_label.setPixmap(QPixmap.fromImage(img.copy()))
        self.lbl_pdf_page.setText(f"стр. {self._pdf_index + 1} из {self._pdf_count}")
        self.lbl_info.setText(f"PDF: {self._pdf_count} стр. • масштаб {self._pdf_scale:.2f}x"
                              + (" • F4 — правка" if self.editable else ""))

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
            self, "Удаление страницы",
            f"Удалить страницу {self._pdf_index + 1} из {self._pdf_count}?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        pv.pdf_delete_pages(self._pdf_path, [self._pdf_index])
        self._pdf_count -= 1
        if self._pdf_count == 0:
            self.lbl_info.setText("PDF: страниц не осталось")
            self.reject()
            return
        self._pdf_index = min(self._pdf_index, self._pdf_count - 1)
        self._page_show()

    def _pdf_export(self) -> None:
        text, ok = QInputDialog.getText(
            self, "Экспорт страниц",
            f"Страницы (например 1-3,5; всего {self._pdf_count}):",
            text=str(self._pdf_index + 1))
        if not ok or not text.strip():
            return
        try:
            indices = pv.parse_ranges(text, self._pdf_count)
        except ValueError:
            QMessageBox.warning(self, "Экспорт", "Неверный диапазон страниц")
            return
        base = os.path.splitext(self._pdf_path)[0]
        out, _filter = QFileDialog.getSaveFileName(
            self, "Экспорт страниц PDF", base + "-страницы.pdf", "PDF (*.pdf)")
        if not out:
            return
        pv.pdf_export_pages(self._pdf_path, indices, out)
        QMessageBox.information(self, "Экспорт",
                                f"Сохранено страниц: {len(indices)}\n{out}")

    # docx -------------------------------------------------------------------

    def _load_docx(self, path: str) -> None:
        self.kind = "docx"
        self.btn_wrap.show()
        if self.editable:
            lines = pv.docx_paragraphs(path)
            self.text_edit.setPlainText("\n".join(lines))
            self.text_edit.setReadOnly(False)
            self.stack.setCurrentIndex(0)
            self._info_base = ("docx: правка по абзацам (стиль абзаца сохраняется, "
                               "встроенное форматирование меняемых абзацев теряется)")
            self.lbl_info.setText(self._info_base)
        else:
            self._set_document_html(pv.docx_to_html(path))
            self.lbl_info.setText("docx: просмотр • F4 — правка по абзацам")

    def _set_document_html(self, html: str) -> None:
        self.preview.setHtml(html)
        self.stack.setCurrentIndex(1)

    # epub / pptx ------------------------------------------------------------

    def _load_epub(self, path: str) -> None:
        self.kind = "epub"
        extract_dir = self._make_temp_dir()
        self._doc_items = pv.epub_chapters(path, extract_dir)
        if not self._doc_items:
            raise ValueError("в книге нет текстовых глав")
        self.doc_combo.blockSignals(True)
        self.doc_combo.clear()
        for title, _xhtml, _base in self._doc_items:
            self.doc_combo.addItem(title)
        self.doc_combo.blockSignals(False)
        self.doc_combo.show()
        self.doc_combo.setCurrentIndex(0)
        self._on_doc_combo(0)  # сигналы combo были заблокированы
        self.stack.setCurrentIndex(1)
        self.lbl_info.setText(f"epub: глав {len(self._doc_items)} • "
                              "изображения показываются, CSS упрощён")

    def _load_pptx(self, path: str) -> None:
        images_dir = self._make_temp_dir()
        deck = pv.pptx_slides_rich(path, images_dir)
        if not deck["slides"]:
            raise ValueError("в презентации нет слайдов")
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

    # xlsx / csv ---------------------------------------------------------------

    def _load_xlsx(self, path: str) -> None:
        self.kind = "xlsx"
        names = pv.xlsx_sheet_names(path)
        self._grid_sources = [("xlsx", path, name) for name in names]
        self.sheet_combo.blockSignals(True)
        self.sheet_combo.clear()
        for name in names:
            self.sheet_combo.addItem(name)
        self.sheet_combo.blockSignals(False)
        self.sheet_combo.setVisible(len(names) > 1)
        self._select_sheet(0)
        self.stack.setCurrentIndex(4)

    def _load_csv(self, path: str) -> None:
        self.kind = "csv"
        text, enc, truncated = text_preview(path)
        delim = pv.csv_sniff_delimiter(text)
        self._grid_sources = [("csv", text, delim)]
        self.sheet_combo.hide()
        note = " (обрезано)" if truncated else ""
        self._info_base = f"CSV: кодировка {enc}, разделитель {delim!r}{note}"
        self._select_sheet(0, info_base=self._info_base)
        self.stack.setCurrentIndex(4)

    def _select_sheet(self, index: int, info_base: str = "") -> None:
        if not 0 <= index < len(self._grid_sources):
            return
        kind, a, b = self._grid_sources[index]
        if kind == "xlsx":
            factory = lambda: pv.xlsx_rows_iter(a, b)  # noqa: E731
            title = b
        else:
            factory = lambda: pv.csv_rows_iter(a, b)  # noqa: E731
            title = "CSV"
        self._start_grid_load(factory, title, info_base)

    def _start_grid_load(self, factory, title: str, info_base: str = "") -> None:
        self.sheet_model.reset_rows()
        self._grid_info_base = info_base
        self.lbl_info.setText((info_base + " • " if info_base else "")
                              + f"чтение {title}…")
        self._grid_error = ""

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

    def _on_grid_batch(self, payload: dict) -> None:
        self.sheet_model.append_rows(payload.get("rows", []))
        if payload.get("done"):
            if payload.get("error"):
                self._grid_error = payload["error"]
                self.lbl_info.setText(f"ошибка чтения: {payload['error']}")
            else:
                base = self._grid_info_base
                self.lbl_info.setText((base + " • " if base else "")
                                      + f"строк: {self.sheet_model.rowCount()}")

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
        self.lbl_info.setText(f"HTML • кодировка: {enc}")

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
        self._info_base = f"XML • кодировка: {enc}" + (" (обрезано)" if truncated else "")
        self.lbl_info.setText(self._info_base)
        self.stack.setCurrentIndex(0)

    def _fill_xml_tree(self) -> bool:
        try:
            _root_name, node = pv.xml_tree(self._xml_path())
        except ET.ParseError as exc:
            self.lbl_info.setText(f"{self._info_base} • дерево недоступно: {exc}")
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
        self.btn_preview.setText("Исходник" if on else "Предпросмотр")
        if not force:
            self.btn_preview.setChecked(on)

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
        suffix = (" • JSON: OK" if error is None
                  else f" • JSON: ошибка ({error})")
        self.lbl_info.setText(self._info_base + suffix)

    def _format_json(self) -> None:
        if self.kind != "json":
            return
        text = self.text_edit.toPlainText()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            self.lbl_info.setText(
                f"{self._info_base} • не отформатировано — ошибка "
                f"(строка {exc.lineno}, столбец {exc.colno}): {exc.msg}")
            return
        except (ValueError, RecursionError) as exc:
            self.lbl_info.setText(f"{self._info_base} • не отформатировано: {exc}")
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
            self.lbl_info.setText(f"{self._info_base} • дерева нет — ошибка: {exc}")
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
                self.lbl_info.setText(f"не найдено: {needle}")

    def _replace_one(self) -> None:
        needle, replacement = self.search.text(), self.replace_edit.text()
        if not needle or self.text_edit.isReadOnly():
            return
        cursor = self.text_edit.document().find(needle, self.text_edit.textCursor())
        if not not cursor.isNull():
            self._find_next()  # с начала документа
            cursor = self.text_edit.document().find(needle, self.text_edit.textCursor())
            if not not cursor.isNull():
                self.lbl_info.setText(f"не найдено: {needle}")
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
        self.lbl_info.setText(f"заменено: {count}")

    def _ask_goto_line(self) -> None:
        if self.text_edit.isReadOnly():
            return
        line, ok = QInputDialog.getInt(
            self, "Перейти на строку", "Номер строки:",
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
            f"строка {cursor.blockNumber() + 1}, "
            f"столбец {cursor.positionInBlock() + 1}")

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

    def _save(self) -> None:
        if not self.editable or self.text_edit.isReadOnly():
            return
        if self._image is not None:
            return
        path = self.files[self.index]
        text = self.text_edit.toPlainText()
        if self.kind == "docx":
            try:
                pv.docx_save_paragraphs(path, text.splitlines())
            except Exception as exc:
                QMessageBox.critical(self, "Ошибка",
                                     f"Не удалось сохранить docx:\n{exc}")
                return
            self.text_edit.document().setModified(False)
            self.lbl_info.setText("сохранено (docx)")
            return
        if self.kind == "json":
            error = json_error_position(text)
            if error is not None:
                ret = QMessageBox.question(
                    self, "JSON некорректен",
                    f"В файле ошибка JSON ({error}).\nСохранить как есть?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if ret != QMessageBox.Yes:
                    return
        try:
            data = text.encode(self.encoding)
        except UnicodeEncodeError:
            ret = QMessageBox.question(
                self, "Кодировка",
                f"Текст не сохраняется в {self.encoding} без потерь.\n"
                "Сохранить как UTF-8?")
            if ret == QMessageBox.Yes:
                self.encoding = "utf-8"
                data = text.encode("utf-8")
            else:
                return
        try:
            with open(path, "wb") as f:
                f.write(data)
        except OSError as exc:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить:\n{exc}")
            return
        self.text_edit.document().setModified(False)
        self.lbl_info.setText(f"сохранено ({self.encoding})")
        if self.kind == "md" and self.btn_preview.isChecked():
            self._render_markdown(text)

    def reject(self) -> None:
        if (self.editable and not self.text_edit.isReadOnly()
                and self.text_edit.document().isModified()):
            ret = QMessageBox.question(
                self, "Есть изменения",
                "Файл изменён. Сохранить перед закрытием?",
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
                editable: bool = False) -> FileViewerDialog:
    files = all_files if all_files else [path]
    if path not in files:
        files = [path] + files
    index = files.index(path)
    dlg = FileViewerDialog(parent, files, index, editable)
    return dlg
