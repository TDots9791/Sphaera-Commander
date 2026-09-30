"""Встроенный просмотр (F3) и правка (F4).

Текст: автоопределение кодировки (utf-8 → utf-16 по BOM → cp1251 → latin-1),
файлы больше лимита показываются частично с явной пометкой.
Бинарные: hex-обзор (первые 2 МиБ). Изображения: масштабируемый просмотр.
Правка: сохранение в той же кодировке (Ctrl+S), защита правок при закрытии.
"""

from __future__ import annotations

import os

from PySide6.QtGui import QImage, QKeySequence, QPixmap, QTextCursor
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

TEXT_LIMIT = 16 * 1024 * 1024   # показываем не больше 16 МиБ текста
HEX_LIMIT = 2 * 1024 * 1024     # и 2 МиБ hex-обзора
HEX_ROW = 16


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


class FileViewerDialog(QDialog):
    """Просмотр/правка одного файла; files — список для навигации след/пред."""

    def __init__(self, parent, files: list[str], index: int, editable: bool):
        super().__init__(parent)
        self.files = [f for f in files if os.path.isfile(f)]
        self.index = max(0, min(index, len(self.files) - 1))
        self.editable = editable
        self.encoding = "utf-8"
        self._image: QPixmap | None = None
        self.saved_on_close = False

        self.setModal(True)
        self.resize(900, 640)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(not editable)
        self.text_edit.setLineWrapMode(QPlainTextEdit.NoWrap)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск (Enter — далее)")
        self.search.returnPressed.connect(self._find_next)
        btn_find = QPushButton("Найти")
        btn_find.clicked.connect(self._find_next)

        btn_prev = QPushButton("← Пред. (Alt+↑)")
        btn_next = QPushButton("След. (Alt+↓) →")
        btn_prev.clicked.connect(lambda: self.navigate(-1))
        btn_next.clicked.connect(lambda: self.navigate(1))

        srow = QHBoxLayout()
        srow.addWidget(self.search, 1)
        srow.addWidget(btn_find)
        srow.addStretch(1)
        srow.addWidget(btn_prev)
        srow.addWidget(btn_next)
        srow.addStretch(1)
        self.lbl_info = QLabel("")
        srow.addWidget(self.lbl_info)

        layout = QVBoxLayout(self)
        layout.addLayout(srow)
        layout.addWidget(self.text_edit, 1)
        layout.addWidget(self.image_label, 1)

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
        self.text_edit.show()
        self._image = None
        self.text_edit.setPlainText("")

        img = QImage(path)
        if not img.isNull():
            self._image = QPixmap.fromImage(img)
            self.text_edit.hide()
            self.image_label.show()
            self._fit_image()
            self.lbl_info.setText(f"изображение {img.width()}×{img.height()}")
            self.search.setEnabled(False)
            return
        self.search.setEnabled(True)
        try:
            with open(path, "rb") as f:
                head = f.read(8192)
                raw = head + (f.read(TEXT_LIMIT) if len(head) == 8192 else b"")
        except OSError as exc:
            self.text_edit.setPlainText(f"<не удалось прочитать файл: {exc}>")
            return
        if looks_binary(head):
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
        self.lbl_info.setText(f"кодировка: {enc}" + (" (обрезано)" if truncated else ""))

    def _fit_image(self) -> None:
        if self._image is not None:
            scaled = self._image.scaled(self.image_label.size(),
                                        Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.image_label.setPixmap(scaled)

    def resizeEvent(self, event):
        self._fit_image()
        super().resizeEvent(event)

    # -- навигация и действия ------------------------------------------------

    def navigate(self, delta: int) -> None:
        if not self.files:
            return
        self.index = (self.index + delta) % len(self.files)
        self.load_current()

    def _find_next(self) -> None:
        needle = self.search.text()
        if needle and not self.text_edit.find(needle):
            # с начала, если не нашли ниже курсора
            self.text_edit.moveCursor(QTextCursor.Start)
            if not self.text_edit.find(needle):
                self.lbl_info.setText(f"не найдено: {needle}")

    def _save(self) -> None:
        if not self.editable or self.text_edit.isReadOnly():
            return
        path = self.files[self.index]
        if self._image is not None:
            return
        text = self.text_edit.toPlainText()
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
