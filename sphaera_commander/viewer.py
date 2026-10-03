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

from PySide6.QtCore import QUrl, QSize, Qt, QTimer
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
    QTextTable,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

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


class FileViewerDialog(QDialog):
    """Просмотр/правка одного файла; files — список для навигации след/пред."""

    def __init__(self, parent, files: list[str], index: int, editable: bool):
        super().__init__(parent)
        self.files = [f for f in files if os.path.isfile(f)]
        self.index = max(0, min(index, len(self.files) - 1))
        self.editable = editable
        self.encoding = "utf-8"
        self.saved_on_close = False
        self.kind = "text"  # text | md | json | binary | image
        self._image: QPixmap | None = None
        self._highlighter: JsonHighlighter | None = None
        self._info_base = ""
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
        self.stack = QStackedWidget()
        self.stack.addWidget(self.text_edit)  # 0 — исходник/текст
        self.stack.addWidget(self.preview)    # 1 — рендер markdown
        self.stack.addWidget(self.tree)       # 2 — дерево json

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
        self._set_preview(False, force=True)

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
        if self.kind != "json":
            self.btn_tree.setChecked(False)
            return
        if on:
            if not self._build_json_tree():
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
