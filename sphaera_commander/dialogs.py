"""Диалоги: прогресс операции (с очередью), TC-перезапись, подтверждение удаления,
групповое переименование, сводка ошибок."""

from __future__ import annotations

import os
import threading
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from .i18n import tr
from .ops import (
    ASK_CANCEL,
    ASK_OVERWRITE,
    ASK_OVERWRITE_ALL,
    ASK_SKIP,
    ASK_SKIP_ALL,
    ConflictInfo,
    OpResult,
    Progress,
)


def _fmt_size(n: int) -> str:
    from .fsmodel import human_size

    return human_size(n)


def _fmt_time(ts: float) -> str:
    return time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(ts)) if ts else "—"


class ProgressOpDialog(QDialog):
    """Модальный прогресс операции; закрытие = запрос отмены текущей."""

    def __init__(self, parent, title: str, cancel_callable, abort_callable=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._cancel = cancel_callable
        self._done = False

        self.lbl_counts = QLabel("…")
        self.lbl_queue = QLabel("")
        self.lbl_current = QLabel("")
        self.lbl_current.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.bar = QProgressBar()

        row = QHBoxLayout()
        btn_cancel = QPushButton(tr("Прервать текущую"))
        btn_cancel.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(btn_cancel)
        if abort_callable is not None:
            btn_abort = QPushButton(tr("Прервать все"))
            btn_abort.clicked.connect(abort_callable)
            btn_abort.clicked.connect(self.reject)
            row.addWidget(btn_abort)

        layout = QVBoxLayout(self)
        layout.addWidget(self.lbl_queue)
        layout.addWidget(self.lbl_counts)
        layout.addWidget(self.bar)
        layout.addWidget(self.lbl_current)
        layout.addLayout(row)
        self.resize(560, 160)

    def set_queue(self, queued: int) -> None:
        self.lbl_queue.setText(tr("операций в очереди: {n}").format(n=queued))

    def update_progress(self, p: Progress) -> None:
        self.lbl_counts.setText(tr("Обработано {done} из {total} объект(ов)").format(done=p.done_files, total=p.total_files))
        if p.total_bytes > 0:
            self.bar.setRange(0, p.total_bytes)
            self.bar.setValue(min(p.done_bytes, p.total_bytes))
            pct = int(p.done_bytes * 100 / p.total_bytes)
            self.bar.setFormat("%p%")
            self.lbl_counts.setText(
                tr("Обработано {done} из {total} объект(ов) — {done_size} из {total_size} ({pct}%)").format(
                    done=p.done_files, total=p.total_files,
                    done_size=_fmt_size(p.done_bytes),
                    total_size=_fmt_size(p.total_bytes), pct=pct))
        else:
            self.bar.setRange(0, 0)  # busy
        metrics = QFontMetrics(self.lbl_current.font())
        self.lbl_current.setText(metrics.elidedText(p.current, Qt.ElideMiddle, 540))

    def op_finished(self, _result: OpResult) -> None:
        self._done = True
        self.bar.setRange(0, 1)
        self.bar.setValue(1)

    def reject(self) -> None:
        if not self._done:
            self._cancel()  # первый Esc/крестик — только отмена текущей операции
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if not self._done:
            self._cancel()
            event.ignore()
        else:
            event.accept()


class OverwriteAskDialog(QDialog):
    """Диалог перезаписи в стиле TC: сведения об обоих файлах."""

    ANSWERS = (
        ("Перезаписать", ASK_OVERWRITE),
        ("Пропустить", ASK_SKIP),
        ("Все перезаписать", ASK_OVERWRITE_ALL),
        ("Все пропустить", ASK_SKIP_ALL),
        ("Отмена", ASK_CANCEL),
    )

    def __init__(self, parent, info: ConflictInfo):
        super().__init__(parent)
        self.setWindowTitle(tr("Перезапись"))
        self._answer = ASK_CANCEL

        grid = QGridLayout()
        for col, title in enumerate((tr("Заменяемый (назначение)"), tr("Источник"))):
            grid.addWidget(QLabel(f"<b>{title}</b>"), 0, col + 1)

        dst_stat = (info.dst_size, info.dst_mtime)
        src_stat = (info.src_size, info.src_mtime)
        rows = (
            (tr("Файл"), os.path.basename(info.dst), os.path.basename(info.src)),
            (tr("Каталог"), os.path.dirname(info.dst), os.path.dirname(info.src)),
            (tr("Размер"), _fmt_size(dst_stat[0]), _fmt_size(src_stat[0])),
            (tr("Изменён"), _fmt_time(dst_stat[1]), _fmt_time(src_stat[1])),
        )
        for r, (label, dst_v, src_v) in enumerate(rows, start=1):
            grid.addWidget(QLabel(label), r, 0)
            grid.addWidget(QLabel(dst_v), r, 1)
            grid.addWidget(QLabel(src_v), r, 2)

        buttons = QHBoxLayout()
        for title, answer in self.ANSWERS:
            btn = QPushButton(tr(title))
            btn.clicked.connect(lambda _=False, a=answer: self._choose(a))
            buttons.addWidget(btn)

        layout = QVBoxLayout(self)
        top = QLabel(tr("Файл назначения уже существует. Заменить его?"))
        top.setWordWrap(True)
        layout.addWidget(top)
        layout.addLayout(grid)
        layout.addLayout(buttons)

    def _choose(self, answer: str) -> None:
        self._answer = answer
        self.accept()

    @staticmethod
    def ask(parent, info: ConflictInfo) -> str:
        dlg = OverwriteAskDialog(parent, info)
        dlg.exec()
        return dlg._answer


def confirm_delete(parent, sources: list, current_dir: str,
                   action: str | None = None, button: str | None = None) -> bool:
    action = action or tr("Удалить")
    button = button or tr("Удалить")
    names = ", ".join(e.name for e in sources[:6])
    if len(sources) > 6:
        names += tr(" … и ещё {n}").format(n=len(sources) - 6)
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(action)
    box.setText(tr("{action} {count} объект(ов) из\n{dir}?\n\n{names}").format(
        action=action, count=len(sources), dir=current_dir, names=names))
    yes = box.addButton(button, QMessageBox.AcceptRole)
    box.addButton(tr("Отмена"), QMessageBox.RejectRole)
    box.exec()
    return box.clickedButton() is yes


class BatchRenameDialog(QDialog):
    """Групповое переименование: шаблон, счётчик, живой предпросмотр."""

    def __init__(self, parent, names: list[str]):
        super().__init__(parent)
        self.setWindowTitle(tr("Групповое переименование"))
        self.names = names
        self.pairs: list[tuple[str, str]] = []
        self._error = ""

        self.template = QLineEdit("*")
        self.template.textEdited.connect(self._preview)
        self.start = QSpinBox()
        self.start.setRange(0, 10 ** 6)
        self.start.setValue(1)
        self.start.valueChanged.connect(self._preview)
        self.step = QSpinBox()
        self.step.setRange(1, 1000)
        self.step.setValue(1)
        self.step.valueChanged.connect(self._preview)

        self.table = QTableWidget(len(names), 2)
        self.table.setHorizontalHeaderLabels((tr("Было"), tr("Станет")))
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().hide()

        form = QHBoxLayout()
        form.addWidget(QLabel(tr("Шаблон:")))
        form.addWidget(self.template, 1)
        form.addWidget(QLabel(tr("Начало:")))
        form.addWidget(self.start)
        form.addWidget(QLabel(tr("Шаг:")))
        form.addWidget(self.step)

        self.lbl_error = QLabel("")
        self.lbl_error.setStyleSheet("color: #b00000;")

        ok = QPushButton(tr("Переименовать"))
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        cancel = QPushButton(tr("Отмена"))
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(ok)
        row.addWidget(cancel)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr(
            "Подстановки:  * — имя без расширения,  [E] — расширение,  "
            "[N]/[N03] — счётчик (ширина)")))
        layout.addLayout(form)
        layout.addWidget(self.lbl_error)
        layout.addWidget(self.table, 1)
        layout.addLayout(row)
        self.resize(640, 460)
        self._preview()

    def _current_pairs(self) -> tuple[list[tuple[str, str]], str]:
        from .rename_tool import build_rename

        try:
            return build_rename(self.names, self.template.text(),
                                self.start.value(), self.step.value()), ""
        except ValueError as exc:
            return [], str(exc)

    def _preview(self) -> None:
        pairs, error = self._current_pairs()
        self.pairs = pairs
        self._error = error
        self.lbl_error.setText(error)
        self.table.setRowCount(len(pairs))
        for r, (old, new) in enumerate(pairs):
            self.table.setItem(r, 0, QTableWidgetItem(old))
            self.table.setItem(r, 1, QTableWidgetItem(new))

    def _accept(self) -> None:
        if self._error or not self.pairs:
            return
        self.accept()


def show_op_result(parent, result: OpResult) -> None:
    """Показать сводку/ошибки операции."""
    if result.ok:
        return
    if result.errors:
        details = "\n".join(f"{e.path} — {e.message}" for e in result.errors[:200])
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(tr("Ошибки операции"))
        text = tr("Операция завершена с ошибками: {n}.\nУспешно: {ok}, пропущено: {skipped}.").format(
            n=len(result.errors), ok=result.done_files, skipped=result.skipped)
        if result.cancelled:
            text += tr("\nОперация прервана пользователем.")
        box.setText(text)
        box.setDetailedText(details)
        box.exec()
    elif result.cancelled:
        QMessageBox.information(parent, tr("Операция"),
                                tr("Операция прервана.\nУспешно: {ok}, пропущено: {skipped}.").format(
                                    ok=result.done_files, skipped=result.skipped))


class SearchDialog(QDialog):
    """Поиск файлов по маске и содержимому (Alt+F7); окно живёт, пока ищет.

    Результаты — двойной клик/Enter → openRequested(путь, строка).
    """

    openRequested = Signal(str, int)
    hitArrived = Signal(object)    # searcher.Hit (из рабочего потока)
    statusChanged = Signal(str)
    searchDone = Signal()
    MAX_ROWS = 5000

    def __init__(self, parent, root_dir: str):
        super().__init__(parent)
        self.setWindowTitle(tr("Поиск файлов"))
        self.setModal(False)
        self.resize(860, 560)
        self.root_dir = root_dir
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()

        self.edit_root = QLineEdit(root_dir)
        self.edit_mask = QLineEdit("*")
        self.edit_mask.setToolTip(tr("Маски через пробел или ;:  *.py  *.txt;README*"))
        self.edit_text = QLineEdit()
        self.edit_text.setPlaceholderText(tr("пусто — искать только по маске"))
        self.chk_recursive = QPushButton(tr("Рекурсивно"))
        self.chk_recursive.setCheckable(True)
        self.chk_recursive.setChecked(True)
        self.chk_case = QPushButton(tr("Учитывать регистр"))
        self.chk_case.setCheckable(True)
        self.chk_regex = QPushButton(tr("Рег. выражение"))
        self.chk_regex.setCheckable(True)

        self.btn_start = QPushButton(tr("Найти"))
        self.btn_start.setDefault(True)
        self.btn_start.clicked.connect(self.start_search)
        self.btn_stop = QPushButton(tr("Стоп"))
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._cancel.set)

        form = QGridLayout()
        form.addWidget(QLabel(tr("Где:")), 0, 0)
        form.addWidget(self.edit_root, 0, 1, 1, 3)
        form.addWidget(QLabel(tr("Маска файлов:")), 1, 0)
        form.addWidget(self.edit_mask, 1, 1)
        form.addWidget(QLabel(tr("Текст:")), 2, 0)
        form.addWidget(self.edit_text, 2, 1)
        row = QHBoxLayout()
        row.addWidget(self.chk_recursive)
        row.addWidget(self.chk_case)
        row.addWidget(self.chk_regex)
        row.addStretch(1)
        row.addWidget(self.btn_stop)
        row.addWidget(self.btn_start)
        form.addLayout(row, 2, 2, 1, 2)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels((tr("Файл"), tr("Стр."), tr("Совпадение")))
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tree.itemActivated.connect(self._emit_open)
        self.tree.setRootIsDecorated(False)

        self.lbl_status = QLabel(tr("Поиск в: {dir}").format(dir=root_dir))

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.lbl_status)

    def start_search(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        from . import searcher

        self.tree.clear()
        self._cancel.clear()
        self.btn_stop.setEnabled(True)
        self.btn_start.setEnabled(False)
        masks = self.edit_mask.text()
        needle = self.edit_text.text()
        case = self.chk_case.isChecked()
        regex = self.chk_regex.isChecked()
        recursive = self.chk_recursive.isChecked()
        root = self.edit_root.text()

        def worker():
            stats = searcher.run_search(
                root, masks, needle, recursive=recursive,
                case_sensitive=case, use_regex=regex,
                hit_cb=lambda h: self.hitArrived.emit(h),
                progress_cb=lambda s: self.statusChanged.emit(s.summary()),
                is_cancelled=self._cancel.is_set)
            self.statusChanged.emit(stats.summary())
            self.searchDone.emit()

        self._thread = threading.Thread(target=worker, daemon=True,
                                        name="search")
        self._thread.start()

    def _emit_open(self, item: QTreeWidgetItem, _col: int) -> None:
        path = item.data(0, Qt.UserRole)
        if path:
            self.openRequested.emit(path, int(item.data(1, Qt.UserRole) or 0))

    def attach(self) -> None:
        """Соединить сигналы рабочего потока со слотами (главный поток)."""
        self.hitArrived.connect(self._on_hit)
        self.statusChanged.connect(self.lbl_status.setText)
        self.searchDone.connect(self._on_done)

    def _on_hit(self, hit) -> None:
        if self.tree.topLevelItemCount() >= self.MAX_ROWS:
            return
        item = QTreeWidgetItem(
            [os.path.basename(hit.path) if hit.line_no else hit.path,
             str(hit.line_no) if hit.line_no else "", hit.text])
        item.setData(0, Qt.UserRole, hit.path)
        item.setData(1, Qt.UserRole, hit.line_no)
        item.setToolTip(0, hit.path)
        self.tree.addTopLevelItem(item)

    def _on_done(self) -> None:
        self.btn_stop.setEnabled(False)
        self.btn_start.setEnabled(True)
        self._thread = None

    def reject(self) -> None:
        self._cancel.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        super().reject()


def confirm_overwrite(parent, conflicts: list[tuple[str, str]], dest_dir: str) -> str:
    """Массовое решение о перезаписи (для перетаскивания): POLICY_*."""
    names = "\n".join(dst for _src, dst in conflicts[:8])
    if len(conflicts) > 8:
        names += tr("\n… и ещё {n}").format(n=len(conflicts) - 8)
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(tr("Перезапись файлов"))
    box.setText(tr("В папке назначения уже есть {n} файл(ов) с такими именами:\n{names}\n\nПерезаписать?").format(
        n=len(conflicts), names=names))
    b_over = box.addButton(tr("Перезаписать все"), QMessageBox.AcceptRole)
    b_skip = box.addButton(tr("Пропустить все"), QMessageBox.NoRole)
    box.addButton(tr("Отмена"), QMessageBox.RejectRole)
    box.exec()
    if box.clickedButton() is b_over:
        return "overwrite"
    if box.clickedButton() is b_skip:
        return "skip"
    return "cancel"
