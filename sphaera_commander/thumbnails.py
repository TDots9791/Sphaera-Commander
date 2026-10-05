"""Миниатюры картинок для панелей: кэш на диске (по пути+mtime+размер)
и фоновая генерация в одном рабочем потоке. UI никогда не ждёт генерацию:
модель получает заглушку, затем сигнал ready(path) и перерисовывается.
"""

from __future__ import annotations

import hashlib
import os
import queue
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QIcon, QImage

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg",
              ".ico", ".tiff", ".tif")
PDF_EXTS = (".pdf",)


def is_image(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXTS


def is_thumbable(path: str) -> bool:
    """Миниатюра возможна: картинка или PDF (первая страница, pdfium)."""
    ext = os.path.splitext(path)[1].lower()
    return ext in IMAGE_EXTS or ext in PDF_EXTS


def cache_path(base_dir: str, path: str, mtime: float, size: int,
               thumb_size: int) -> str:
    key = f"{path}|{mtime:.3f}|{size}|{thumb_size}"
    digest = hashlib.md5(key.encode("utf-8", "replace")).hexdigest()
    return os.path.join(base_dir, f"{digest}.png")


class ThumbnailStore(QObject):
    """Хранилище миниатюр: память → диск → фоновая генерация."""

    ready = Signal(str)  # путь готов — модель обновляет строку

    def __init__(self, thumb_size: int = 96, parent=None):
        super().__init__(parent)
        self.thumb_size = thumb_size
        base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
        self.cache_dir = os.path.join(base, "sphaera-commander", "thumbs",
                                      str(thumb_size))
        self._memory: dict[str, QIcon] = {}
        self._jobs: queue.Queue = queue.Queue()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._loop, daemon=True,
                                        name="thumbnails")
        self._worker.start()

    # -- вызов из GUI-потока -------------------------------------------------

    def get(self, path: str, mtime: float, size: int) -> QIcon | None:
        """Иконка или None (тогда файл ставится в очередь генерации)."""
        disk = cache_path(self.cache_dir, path, mtime, size, self.thumb_size)
        icon = self._memory.get(disk)
        if icon is not None:
            return icon
        if os.path.isfile(disk):
            icon = QIcon(disk)
            self._memory[disk] = icon
            return icon
        self._enqueue(path, mtime, size, disk)
        return None

    # -- фон -------------------------------------------------------------------

    def _enqueue(self, path: str, mtime: float, size: int, disk: str) -> None:
        with self._lock:
            if disk in self._memory:  # уже в очереди/готово
                return
            self._memory[disk] = QIcon()  # маркер «в работе»
        self._jobs.put((path, mtime, size, disk))
        self._wake.set()

    def _loop(self) -> None:
        while True:
            self._wake.wait()
            try:
                while True:
                    path, mtime, size, disk = self._jobs.get_nowait()
                    icon = self._generate(path, disk)
                    if icon is not None:
                        with self._lock:  # заменяем маркер «в работе»
                            self._memory[disk] = icon
                        try:
                            self.ready.emit(path)
                        except RuntimeError:
                            return  # хранилище уже удалено — поток завершится сам
                    else:
                        with self._lock:  # неудача — снимем маркер
                            if self._memory.get(disk) is not None and \
                                    self._memory[disk].isNull():
                                del self._memory[disk]
            except queue.Empty:
                self._wake.clear()
                return

    @staticmethod
    def _generate(path: str, disk: str) -> QIcon | None:
        try:
            img = QImage(path)
            if img.isNull() and os.path.splitext(path)[1].lower() in PDF_EXTS:
                img = ThumbnailStore._render_pdf_first_page(path)
            if img.isNull():
                return None
            thumb = img.scaled(256, 256, Qt.KeepAspectRatio,
                               Qt.SmoothTransformation)
            if thumb.isNull():
                return None
            os.makedirs(os.path.dirname(disk), exist_ok=True)
            thumb.save(disk, "PNG")
            return QIcon(disk)
        except Exception:
            return None

    @staticmethod
    def _render_pdf_first_page(path: str) -> QImage:
        """Первая страница PDF как QImage (BGR888 — грабли pypdfium2)."""
        import pypdfium2 as pdfium
        from PySide6.QtGui import QImage as _QImage

        with pdfium.PdfDocument(path) as pdf:
            page = pdf[0]
            w, h = page.get_size()
            scale = max(0.05, min(4.0, 256.0 / max(w, h)))
            bitmap = page.render(scale=scale)
            img = _QImage(bytes(bitmap.buffer), bitmap.width, bitmap.height,
                          bitmap.stride, _QImage.Format.Format_BGR888)
            return img.copy()  # буфер pdfium умирает вместе с bitmap


_STORE: ThumbnailStore | None = None
_STORE_LOCK = threading.Lock()


def store() -> ThumbnailStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = ThumbnailStore()
        return _STORE
