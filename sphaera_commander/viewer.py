"""Встроенный просмотр (F3) и правка (F4).

Текст: автоопределение кодировки (utf-8 → utf-16 по BOM → cp1251 → latin-1),
файлы больше лимита показываются частично с явной пометкой.
Markdown: рендер предпросмотра (QTextDocument.setMarkdown), переключение
исходник/предпросмотр.
JSON: подсветка синтаксиса, форматирование (Ctrl+Shift+F), валидация с позицией
ошибки; при сохранении некорректного JSON — явный вопрос.
Бинарные: hex-обзор (первые 2 МиБ). Изображения: масштабируемый просмотр.
Правка: сохранение в той же кодировке (Ctrl+S), защита правок при закрытии.
"""

from __future__ import annotations

import json
import os
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QImage,
    QKeySequence,
    QPixmap,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
)

TEXT_LIMIT = 16 * 1024 * 1024   # показываем не больше 16 МиБ текста
HEX_LIMIT = 2 * 1024 * 1024     # и 2 МиБ hex-обзора
HEX_ROW = 16
LIVE_VALIDATE_LIMIT = 2 * 1024 * 1024

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
        self.resize(900, 640)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(not editable)
        self.text_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text_edit.textChanged.connect(self._json_timer.start)

        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(True)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.text_edit)  # 0 — исходник/обычный текст
        self.stack.addWidget(self.preview)    # 1 — рендер markdown

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск (Enter — далее)")
        self.search.returnPressed.connect(self._find_next)
        btn_find = QPushButton("Найти")
        btn_find.clicked.connect(self._find_next)

        self.btn_wrap = QPushButton("Перенос")
        self.btn_wrap.setCheckable(True)
        self.btn_wrap.setToolTip("Переносить длинные строки")
        self.btn_wrap.toggled.connect(self._toggle_wrap)

        self.btn_format = QPushButton("Форматировать")
        self.btn_format.setShortcut(QKeySequence("Ctrl+Shift+F"))
        self.btn_format.setToolTip("Форматировать JSON (Ctrl+Shift+F)")
        self.btn_format.clicked.connect(self._format_json)

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
        srow.addWidget(self.btn_wrap)
        srow.addWidget(self.btn_format)
        srow.addWidget(self.btn_preview)
        srow.addStretch(1)
        srow.addWidget(btn_prev)
        srow.addWidget(btn_next)

        self.lbl_info = QLabel("")

        layout = QVBoxLayout(self)
        layout.addLayout(srow)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.image_label, 1)
        layout.addWidget(self.lbl_info)

        self._shortcut("Ctrl+S", self._save)
        self._shortcut("Alt+Up", lambda: self.navigate(-1))
        self._shortcut("Alt+Down", lambda: self.navigate(1))
        self._shortcut("Escape", self.reject)

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
        self.lbl_info.setText("")
        self.btn_wrap.setChecked(False)
        self.btn_wrap.show()
        self.btn_format.hide()
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
            self.btn_wrap.hide()
            return
        self.search.setEnabled(True)
        try:
            with open(path, "rb") as f:
                head = f.read(8192)
                raw = head + (f.read(TEXT_LIMIT) if len(head) == 8192 else b"")
        except OSError as exc:
            self.kind = "binary"
            self.text_edit.setPlainText(f"<не удалось прочитать файл: {exc}>")
            return
        if looks_binary(head):
            self.kind = "binary"
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
            self._update_json_status()
        elif ext in MD_EXTS:
            self.kind = "md"
            self.btn_preview.show()
            self._set_preview(not self.editable)  # F3 — рендер, F4 — исходник
        else:
            self.kind = "text"
            self.lbl_info.setText(self._info_base)

    @staticmethod
    def _palette_is_dark() -> bool:
        app = QApplication.instance()
        if app is None:
            return False
        return app.palette().color(app.palette().ColorRole.Window).lightness() < 128

    def _render_markdown(self, text: str) -> None:
        self.preview.document().setMarkdown(text)

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

    # -- навигация и действия ------------------------------------------------

    def navigate(self, delta: int) -> None:
        if not self.files:
            return
        self.index = (self.index + delta) % len(self.files)
        self.load_current()

    def _find_next(self) -> None:
        needle = self.search.text()
        if needle and not self.text_edit.find(needle):
            self.text_edit.moveCursor(QTextCursor.Start)
            if not self.text_edit.find(needle):
                self.lbl_info.setText(f"не найдено: {needle}")

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
