"""Модуль «Конвертировать картинки»: пакетное преобразование выбранных
файлов (png/jpg/jpeg/webp/bmp/tiff) в png/jpg/webp через QImage —
без внешних зависимостей и без новых пакетов.

Опции: формат, качество (для jpg/webp), суффикс нового имени; результат
идёт в очередь операций приложения (фон, прогресс, отмена).
"""

from __future__ import annotations

import os

from PySide6.QtGui import QImage

from sphaera_commander.i18n import tr
from sphaera_commander.plugin_api import SphaeraPlugin

SOURCE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif")
TARGETS = {"png": "PNG", "jpg": "JPG", "webp": "WEBP"}


def convert_image(src: str, dst: str, fmt: str, quality: int) -> int:
    """Конвертация одного файла; возвращает размер результата в байтах.
    OSError/ValueError при неудаче (QImage молчит — проверяем результат)."""
    img = QImage(src)
    if img.isNull():
        raise OSError(tr("не удалось прочитать изображение"))
    tmp = dst + ".sc-tmp"
    if not img.save(tmp, TARGETS[fmt], quality):
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise OSError(tr("не удалось записать {fmt}").format(fmt=fmt))
    os.replace(tmp, dst)
    return os.path.getsize(dst)


def convert_many(jobs: list[tuple[str, str, str, int]], progress_cb=None,
                 is_cancelled=lambda: False):
    """(источник, назначение, формат, качество)[] → OpResult."""
    from sphaera_commander.ops import FileError, OpResult, Progress

    result = OpResult()
    total = len(jobs)
    for i, (src, dst, fmt, quality) in enumerate(jobs):
        if is_cancelled():
            result.cancelled = True
            break
        try:
            size = convert_image(src, dst, fmt, quality)
            result.done_files += 1
            result.done_bytes += size
        except (OSError, ValueError) as exc:
            result.errors.append(FileError(src, str(exc)))
        if progress_cb is not None:
            progress_cb(Progress(phase="run", current=src, done_files=i + 1,
                                 total_files=total, done_bytes=result.done_bytes,
                                 total_bytes=0))
    return result


def build_jobs(paths: list[str], target_dir: str, fmt: str, quality: int,
               suffix: str) -> list[tuple[str, str, str, int]]:
    """План конвертации: имя.ext → имя{суффикс}.фмт; источники, чей формат
    и суффикс дают тот же файл, исключаются."""
    jobs = []
    for src in paths:
        base = os.path.splitext(os.path.basename(src))[0]
        dst = os.path.join(target_dir, f"{base}{suffix}.{fmt}")
        if os.path.abspath(dst) == os.path.abspath(src):
            continue  # конвертация в саму себя (тот же формат и суффикс)
        jobs.append((src, dst, fmt, quality))
    return jobs


class Plugin(SphaeraPlugin):
    id = "imgconvert"
    title = "Конвертация картинок"

    def _run_dialog(self):
        from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog,
                                       QHBoxLayout, QLabel, QPushButton,
                                       QSpinBox, QVBoxLayout)

        panel = self.app.active
        if panel.is_vfs:
            self.app._status(tr("Конвертация в архиве не поддерживается"))
            return
        sources = [e.path for e in panel.selected_entries()
                   if not e.is_dir
                   and os.path.splitext(e.path)[1].lower() in SOURCE_EXTS]
        if not sources:
            self.app._status(tr("Выберите картинки (png/jpg/webp/bmp/tiff)"))
            return

        dlg = QDialog(self.app)
        dlg.setWindowTitle(tr("Конвертировать картинки"))
        layout = QVBoxLayout(dlg)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel(tr("Формат:")))
        fmt_box = QComboBox()
        fmt_box.addItems(list(TARGETS))
        fmt_row.addWidget(fmt_box)
        fmt_row.addWidget(QLabel(tr("Качество:")))
        quality = QSpinBox()
        quality.setRange(10, 100)
        quality.setValue(90)
        fmt_row.addWidget(quality)
        fmt_row.addStretch(1)
        layout.addLayout(fmt_row)

        suffix_row = QHBoxLayout()
        suffix_row.addWidget(QLabel(tr("Суффикс имени:")))
        suffix_edit = QComboBox()
        suffix_edit.setEditable(True)
        suffix_edit.addItems(["-новый", "_small", ""])
        suffix_edit.setCurrentText("-новый")
        suffix_row.addWidget(suffix_edit, 1)
        layout.addLayout(suffix_row)

        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel(tr("Куда:")))
        dest_edit = QComboBox()
        dest_edit.setEditable(True)
        dest_edit.addItem(panel.current_path())
        dest_edit.addItem(self.app._other().current_path())
        dest_row.addWidget(dest_edit, 1)
        btn_browse = QPushButton(tr("Обзор…"))
        dest_row.addWidget(btn_browse)
        layout.addLayout(dest_row)

        def browse():
            path = QFileDialog.getExistingDirectory(
                dlg, tr("Куда:"), dest_edit.currentText())
            if path:
                dest_edit.insertItem(0, path)
                dest_edit.setCurrentIndex(0)

        btn_browse.clicked.connect(browse)

        info = QLabel(tr("файлов к конвертации: {n}").format(n=len(sources)))
        row = QHBoxLayout()
        row.addStretch(1)
        btn_ok = QPushButton(tr("Конвертировать"))
        btn_cancel = QPushButton(tr("Отмена"))
        row.addWidget(btn_ok)
        row.addWidget(btn_cancel)
        layout.addLayout(row)

        def run():
            target = dest_edit.currentText()
            if not os.path.isdir(target):
                self.app._status(tr("Каталог назначения не найден: {path}").format(
                    path=target))
                return
            jobs = build_jobs(sources, target, fmt_box.currentText(),
                              quality.value(), suffix_edit.currentText())
            if not jobs:
                self.app._status(tr("Конвертировать нечего (цели совпадают с источниками)"))
                return
            dlg.accept()

            def fn(progress_cb, is_cancelled):
                return convert_many(jobs, progress_cb, is_cancelled)

            self.app._enqueue_op(
                tr("Конвертация картинок: {n}").format(n=len(jobs)), fn,
                after=self.app.refresh_all)

        btn_ok.clicked.connect(run)
        btn_cancel.clicked.connect(dlg.reject)
        self.app._plugin_dialogs.append(dlg)
        dlg.show()

    def tools_actions(self):
        return [(tr("Конвертировать картинки…"), self._run_dialog, None)]

    def context_actions(self, panel, entry):
        if panel.is_vfs or entry is None or entry.is_dir:
            return []
        if os.path.splitext(entry.path)[1].lower() not in SOURCE_EXTS:
            return []
        return [(tr("Конвертировать картинки…"), self._run_dialog)]


def create(app):
    return Plugin(app)
