"""Диалоги: прогресс операции (с очередью), TC-перезапись, подтверждение удаления,
групповое переименование, сводка ошибок."""

from __future__ import annotations

import os
import threading
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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

    def __init__(self, parent, root_dir: str, other_root: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("Поиск файлов"))
        self.setModal(False)
        self.resize(860, 560)
        self.root_dir = root_dir
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()

        self.edit_root = QLineEdit(root_dir)
        self.edit_root.setToolTip(
            tr("Каталог поиска; «путь1|путь2» — обе области сразу"))
        self.edit_mask = QLineEdit("*")
        self.edit_mask.setToolTip(tr("Маски через пробел или ;:  *.py  *.txt;README*"))
        self.edit_text = QLineEdit()
        self.edit_text.setPlaceholderText(tr("пусто — искать только по маске"))
        self.edit_size = QLineEdit()
        self.edit_size.setPlaceholderText(tr("например >2М или 1к-5М"))
        self.edit_dates = QLineEdit()
        self.edit_dates.setPlaceholderText(
            tr("даты: 01.01.2024 или 01.01.2024-31.12.2024"))
        if other_root is not None:
            self.btn_both = QPushButton(tr("⇄ Обе панели"))
            self.btn_both.setToolTip(
                tr("Искать сразу в каталогах обеих панелей (через «|»)"))
            self.btn_both.clicked.connect(
                lambda: self.edit_root.setText(
                    self.root_dir + "|" + other_root
                    if other_root not in self.edit_root.text()
                    else self.root_dir))
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
        form.addWidget(self.edit_root, 0, 1, 1, 2)
        if other_root is not None:
            form.addWidget(self.btn_both, 0, 3)
        form.addWidget(QLabel(tr("Маска файлов:")), 1, 0)
        form.addWidget(self.edit_mask, 1, 1)
        form.addWidget(QLabel(tr("Размер:")), 1, 2)
        form.addWidget(self.edit_size, 1, 3)
        form.addWidget(QLabel(tr("Текст:")), 2, 0)
        form.addWidget(self.edit_text, 2, 1)
        form.addWidget(QLabel(tr("Изменён:")), 2, 2)
        form.addWidget(self.edit_dates, 2, 3)
        row = QHBoxLayout()
        row.addWidget(self.chk_recursive)
        row.addWidget(self.chk_case)
        row.addWidget(self.chk_regex)
        row.addStretch(1)
        row.addWidget(self.btn_stop)
        row.addWidget(self.btn_start)
        form.addLayout(row, 3, 0, 1, 4)

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
        roots = [r.strip() for r in self.edit_root.text().split("|")
                 if r.strip()] or ["."]
        try:
            size_range = searcher.parse_size_filter(self.edit_size.text())
        except ValueError:
            self.lbl_status.setText(tr("Неверный размер: {text}").format(
                text=self.edit_size.text()))
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            return
        dates_text = self.edit_dates.text()
        try:
            if dates_text.strip():
                date_range = searcher.parse_date_filter(dates_text, "")
            else:
                date_range = (0.0, 0.0)
        except ValueError:
            self.lbl_status.setText(tr("Неверные даты: {text}").format(
                text=dates_text))
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            return

        def worker():
            from .searcher import SearchStats

            total = SearchStats()
            for index, root in enumerate(roots):
                if self._cancel.is_set():
                    total.cancelled = True
                    break
                stats = searcher.run_search(
                    root, masks, needle, recursive=recursive,
                    case_sensitive=case, use_regex=regex,
                    size_range=size_range, date_range=date_range,
                    hit_cb=lambda h: self.hitArrived.emit(h),
                    progress_cb=lambda s: self.statusChanged.emit(s.summary()),
                    is_cancelled=self._cancel.is_set)
                total.files_seen += stats.files_seen
                total.files_scanned += stats.files_scanned
                total.files_matched += stats.files_matched
                total.skipped_binary += stats.skipped_binary
                total.skipped_large += stats.skipped_large
                total.hits += stats.hits
                total.errors.extend(stats.errors)
                if stats.cancelled:
                    total.cancelled = True
            self.statusChanged.emit(total.summary())
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


class CloudSyncDialog(QDialog):
    """Синхронизация с облаком: пары ya.d (через yad-sync) и собственные
    пары rclone bisync (Яндекс.Диск, Google Drive, любые remote rclone).

    Немодальное: окно живёт, пока идут запуски; вывод rclone — в журнал.
    """

    runLine = Signal(str)     # строка вывода синхронизации (из потока)
    runFinished = Signal(int)  # код возврата (из потока)
    gdriveFinished = Signal(int)  # итог авторизации Google (из потока)

    def __init__(self, parent, current_dir: str):
        super().__init__(parent)
        from sphaera_commander import cloudsync

        self.cloudsync = cloudsync
        self.setWindowTitle(tr("Синхронизация с облаком"))
        self.setModal(False)
        self.resize(760, 480)
        self.current_dir = current_dir
        self._holder: dict = {}
        self._running_pair: dict | None = None

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            (tr("Локальная папка"), tr("Облако"), tr("Источник")))
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().hide()

        # форма добавления: текущая папка ↔ remote
        form_row = QHBoxLayout()
        self.lbl_add = QLabel(tr("Новая пара: текущая папка →"))
        self.remote_combo = QComboBox()
        self.remote_path = QLineEdit()
        self.btn_browse = QPushButton(tr("Обзор на облаке…"))
        self.btn_browse.clicked.connect(self._browse_remote)
        self.btn_add = QPushButton(tr("Добавить пару"))
        self.btn_add.clicked.connect(self._add_pair)
        form_row.addWidget(self.lbl_add)
        form_row.addWidget(self.remote_combo, 1)
        form_row.addWidget(self.remote_path, 2)
        form_row.addWidget(self.btn_browse)
        form_row.addWidget(self.btn_add)

        self.log = QLabel("")
        self.log.setWordWrap(True)
        self.log.setMaximumHeight(64)

        row = QHBoxLayout()
        self.btn_sync = QPushButton(tr("Синхронизировать сейчас"))
        self.btn_sync.setDefault(True)
        self.btn_sync.clicked.connect(self._sync_selected)
        self.btn_remove = QPushButton(tr("Убрать пару"))
        self.btn_remove.clicked.connect(self._remove_pair)
        self.btn_gdrive = QPushButton(tr("Подключить Google Диск"))
        self.btn_gdrive.clicked.connect(self._connect_gdrive)
        btn_close = QPushButton(tr("Закрыть"))
        btn_close.clicked.connect(self.close)
        row.addWidget(self.btn_gdrive)
        row.addStretch(1)
        row.addWidget(self.btn_remove)
        row.addWidget(self.btn_sync)
        row.addWidget(btn_close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addLayout(form_row)
        layout.addWidget(self.log)
        layout.addLayout(row)

        self.runLine.connect(self._on_run_line)
        self.runFinished.connect(self._on_run_finished)
        self.gdriveFinished.connect(self._gdrive_done_ui)
        self.reload()
        self._reload_remotes()

    # -- данные ------------------------------------------------------------

    def _pairs_rows(self) -> list[dict]:
        rows = []
        for p in self.cloudsync.yad_pairs():
            rows.append(dict(p, source="yad",
                             cloud=f"yad:{p['remote']}"))
        for p in self.cloudsync.load_pairs():
            rows.append(dict(p, source="sphaera", cloud=p["remote"]))
        return rows

    def reload(self) -> None:
        rows = self._pairs_rows()
        self.table.setRowCount(len(rows))
        for r, p in enumerate(rows):
            for c, text in enumerate((p["local"], p["cloud"],
                                      "Ya.D" if p["source"] == "yad"
                                      else "rclone")):
                self.table.setItem(r, c, QTableWidgetItem(text))
            self.table.item(r, 0).setData(Qt.UserRole, p)

    def _reload_remotes(self) -> None:
        remotes = self.cloudsync.rclone_remotes()
        self.remote_combo.clear()
        self.remote_combo.addItems(remotes)
        if "gdrive:" in remotes:
            self.btn_gdrive.hide()
        elif self.cloudsync.rclone_bin():
            self.btn_gdrive.setToolTip(
                tr("rclone config create gdrive drive, затем вход в браузере"))

    # -- запуск синхронизации ------------------------------------------------

    def start_sync(self, pair: dict) -> None:
        if self._running_pair is not None:
            self.log.setText(tr("Синхронизация уже идёт — дождитесь завершения"))
            return
        self._running_pair = pair
        self.btn_sync.setEnabled(False)
        name = pair.get("local", "")
        self.log.setText(tr("Синхронизация: {name} …").format(name=name))
        self.cloudsync.run_sync(
            pair,
            on_line=self.runLine.emit,
            done=self.runFinished.emit,
            _proc_holder=self._holder)

    def _sync_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            self.log.setText(tr("Выберите пару в таблице"))
            return
        pair = self.table.item(row, 0).data(Qt.UserRole)
        self.start_sync(pair)

    def _on_run_line(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        self.log.setText(text[:300])

    def _on_run_finished(self, rc: int) -> None:
        pair = self._running_pair or {}
        self._running_pair = None
        self.btn_sync.setEnabled(True)
        if rc == 0:
            self.log.setText(tr("Готово: {name}").format(
                name=pair.get("local", "")))
        else:
            self.log.setText(tr("Завершено с ошибкой (код {rc}): {name}").format(
                rc=rc, name=pair.get("local", "")))

    def closeEvent(self, event) -> None:
        if self._running_pair is not None:
            # окно закрывать нельзя: rclone bisync прерывать опасно,
            # восстановится только через resync
            event.ignore()
            self.log.setText(tr("Дождитесь завершения синхронизации (или отмените)"))
            return
        event.accept()

    # -- пары ----------------------------------------------------------------

    def _add_pair(self) -> None:
        local = self.current_dir
        remote_name = self.remote_combo.currentText().strip()
        remote_path = self.remote_path.text().strip().strip("/")
        if not remote_name:
            self.log.setText(tr("Нет ни одного remote в rclone (rclone config)"))
            return
        remote = f"{remote_name.rstrip(':')}:{remote_path}"
        if os.path.exists(local) and not os.path.isdir(local):
            self.log.setText(tr("Локальная папка пары должна быть каталогом"))
            return
        self.cloudsync.add_pair(local, remote)
        self.reload()
        self.log.setText(tr("Пара добавлена: {local} ↔ {remote}").format(
            local=local, remote=remote))

    def _remove_pair(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        pair = self.table.item(row, 0).data(Qt.UserRole)
        if pair.get("source") == "yad":
            self.log.setText(tr("Пары Ya.D убираются в самой ya.d (yad-sync remove)"))
            return
        self.cloudsync.remove_pair(pair["id"])
        self.reload()

    def _browse_remote(self) -> None:
        remote_name = self.remote_combo.currentText().strip()
        current = f"{remote_name.rstrip(':')}:" if remote_name else ""
        if not current:
            return
        dirs = self.cloudsync.remote_dirs(current)
        if not dirs:
            self.log.setText(tr("На облаке нет подкаталогов (или не удалось прочитать)"))
            return
        name, ok = QInputDialog.getItem(self, tr("Обзор на облаке"),
                                        tr("Каталоги {remote}:").format(remote=current),
                                        dirs, 0, False)
        if ok:
            base = self.remote_path.text().strip().strip("/")
            self.remote_path.setText(
                f"{base}/{name}" if base else name)

    def _connect_gdrive(self) -> None:
        """Создать gdrive: и открыть браузер OAuth (rclone config reconnect)."""
        create, reconnect = self.cloudsync.connect_remote("gdrive")
        self.btn_gdrive.setEnabled(False)
        self.log.setText(tr("Подключение Google Диска: откройте браузер и разрешите доступ…"))

        def worker():
            import subprocess

            try:
                subprocess.run(create, capture_output=True, text=True, timeout=60)
                # reconnect открывает браузер и ждёт разрешения доступа
                proc = subprocess.run(reconnect, capture_output=True,
                                      text=True, timeout=300)
                rc = proc.returncode
            except (OSError, subprocess.TimeoutExpired):
                rc = -1
            self.gdriveFinished.emit(rc)

        threading.Thread(target=worker, daemon=True, name="gdrive-auth").start()

    def _gdrive_done_ui(self, rc: int) -> None:
        self.btn_gdrive.setEnabled(True)
        self._reload_remotes()
        if rc == 0:
            self.log.setText(tr("Google Диск подключён (remote gdrive:)"))
        else:
            self.log.setText(tr("Не удалось подключить Google Диск (см. rclone config)"))


class PluginsDialog(QDialog):
    """Управление модулями: включение/выключение (применяется после
    перезапуска), источник, текст ошибки загрузки."""

    def __init__(self, parent):
        super().__init__(parent)
        from sphaera_commander import pluginmgr as plugins_mod

        self.plugins_mod = plugins_mod
        self.setWindowTitle(tr("Плагины"))
        self.resize(680, 420)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            (tr("Модуль"), tr("Источник"), tr("Включён")))
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().hide()

        self.errors = QLabel("")
        self.errors.setWordWrap(True)
        self.hint = QLabel(tr("Пользовательские модули: {dir} — применится после перезапуска").format(
            dir=plugins_mod.USER_DIR))
        self.hint.setWordWrap(True)

        close = QPushButton(tr("Закрыть"))
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.errors)
        layout.addWidget(self.hint)
        layout.addLayout(row)
        self.table.itemChanged.connect(self._on_item_changed)
        self.reload()

    def _on_item_changed(self, item):
        if item.column() != 2:
            return
        name = self.table.item(item.row(), 0).text()
        enabled = self.plugins_mod.enabled_names()
        if item.checkState() == Qt.Checked:
            enabled.add(name)
        else:
            enabled.discard(name)
        self.plugins_mod.set_enabled(enabled)

    def reload(self):
        enabled = self.plugins_mod.enabled_names()
        errors = dict(getattr(self.app_parent(), "_plugin_errors", []))
        metas = self.plugins_mod.available()
        self.table.setRowCount(len(metas))
        for r, meta in enumerate(metas):
            source = (tr("встроенный") if meta["source"] == "builtin"
                      else tr("пользовательский"))
            self.table.setItem(r, 0, QTableWidgetItem(meta["name"]))
            self.table.setItem(r, 1, QTableWidgetItem(source))
            box = QTableWidgetItem()
            box.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
                         | Qt.ItemIsSelectable)
            box.setCheckState(Qt.Checked if meta["name"] in enabled
                              else Qt.Unchecked)
            self.table.setItem(r, 2, box)
            if meta["name"] in errors:
                box.setToolTip(errors[meta["name"]])
        self.errors.setText("\n".join(
            f"⚠ {name}: {err}" for name, err in errors.items()))

    def app_parent(self):
        return self.parent()


class PropertiesDialog(QDialog):
    """Свойства файла/папки (Alt+Enter): размеры, счётчики, времена,
    владелец/группа и права-чекбоксы rwx (владелец/группа/остальные);
    ОК применяет chmod, для папки — опционально рекурсивно (в фоне)."""

    permissionsApplied = Signal(str)

    def __init__(self, parent, path: str, apply_async=None):
        super().__init__(parent)
        from .fsmodel import (entry_details, human_size, mode_string,
                              permission_bits)

        self.path = path
        # apply_async(fn, after) — рекурсивное применение через очередь
        # операций приложения (фон, прогресс, отмена); None — синхронно
        self._apply_async = apply_async
        self.setWindowTitle(tr("Свойства: {name}").format(
            name=os.path.basename(path) or path))
        self.resize(560, 520)
        details = entry_details(path)

        info = QTreeWidget()
        info.setHeaderHidden(True)
        info.setColumnCount(2)
        info.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)

        def add_row(key, value):
            QTreeWidgetItem(info, [key, value])

        add_row(tr("Тип"), tr("папка") if details["is_dir"] else
                (tr("символьная ссылка") if details["is_link"] else tr("файл")))
        if details["is_link"]:
            try:
                add_row(tr("Указывает на"), os.readlink(path))
            except OSError:
                pass
        add_row(tr("Размер"), _fmt_size(details["size"]))
        if details["is_dir"]:
            add_row(tr("Содержимое"), tr("{files} файл(ов), {dirs} подпапок").format(
                files=details.get("files", 0), dirs=details.get("dirs", 0)))
        add_row(tr("Место на диске"), _fmt_size(details["blocks"]))
        add_row(tr("Владелец"), f"{details['owner']}:{details['group']}")
        add_row(tr("Жёстких ссылок"), str(details["nlink"]))
        add_row(tr("Изменён"), _fmt_time(details["mtime"]))
        add_row(tr("Доступ"), _fmt_time(details["atime"]))
        add_row(tr("Создан/метаданные"), _fmt_time(details["ctime"]))
        add_row(tr("Права (буквы)"), mode_string(details["mode"]))

        names = (tr("Владелец"), tr("Группа"), tr("Остальные"))
        kinds = (tr("Чтение"), tr("Запись"), tr("Исполнение"))
        self._boxes: list[tuple[int, tuple]] = []
        perm_grid = QGridLayout()
        bits = permission_bits(details["mode"])
        for who, who_name in enumerate(names):
            perm_grid.addWidget(QLabel(who_name), 0, who + 1)
        for kind, kind_name in enumerate(kinds):
            perm_grid.addWidget(QLabel(kind_name), kind + 1, 0)
            for who in range(3):
                box = QCheckBox()
                idx = who * 3 + kind
                box.setChecked(bits[idx])
                self._boxes.append((idx, box))
                perm_grid.addWidget(box, kind + 1, who + 1)

        self.recursive = QCheckBox(tr("Применить рекурсивно (вложенные)"))
        self.recursive.setVisible(details["is_dir"] and not details["is_link"])

        btn_ok = QPushButton(tr("Применить права"))
        btn_ok.clicked.connect(self._apply)
        btn_close = QPushButton(tr("Закрыть"))
        btn_close.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(self.recursive)
        buttons.addStretch(1)
        buttons.addWidget(btn_ok)
        buttons.addWidget(btn_close)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(path))
        layout.addWidget(info, 1)
        layout.addLayout(perm_grid)
        layout.addLayout(buttons)
        self._details = details

    def _apply(self) -> None:
        from .fsmodel import bits_to_mode, set_permissions

        bits = [False] * 9
        for idx, box in self._boxes:
            bits[idx] = box.isChecked()
        mode = bits_to_mode(tuple(bits), self._details["mode"])
        recursive = self.recursive.isChecked()
        if recursive and self._apply_async is not None:
            # большое дерево не должно висеть в GUI-потоке
            path = self.path

            def fn(progress_cb, is_cancelled):
                from sphaera_commander.fsmodel import DirStats
                from sphaera_commander.ops import FileError, OpResult, Progress

                result = OpResult()
                set_permissions(path, mode)
                result.done_files += 1
                total = 0
                for dirpath, dirnames, filenames in os.walk(path):
                    total += len(dirnames) + len(filenames)
                done = 0
                for dirpath, dirnames, filenames in os.walk(path):
                    if is_cancelled():
                        result.cancelled = True
                        return result
                    for name in dirnames + filenames:
                        try:
                            set_permissions(os.path.join(dirpath, name), mode)
                            result.done_files += 1
                        except OSError as exc:
                            result.errors.append(
                                FileError(os.path.join(dirpath, name),
                                          str(exc)))
                        done += 1
                        if progress_cb is not None and done % 25 == 0:
                            progress_cb(Progress(
                                phase="run", current=dirpath, done_files=done,
                                total_files=total,
                                done_bytes=0, total_bytes=0))
                return result

            self._apply_async(fn, self.accept)
            return
        try:
            set_permissions(self.path, mode)
            if recursive:
                for dirpath, dirnames, filenames in os.walk(self.path):
                    for name in dirnames + filenames:
                        try:
                            set_permissions(os.path.join(dirpath, name), mode)
                        except OSError as exc:
                            QMessageBox.warning(
                                self, tr("Свойства"),
                                tr("Не удалось сменить права: {name}: {exc}").format(
                                    name=name, exc=exc))
        except OSError as exc:
            QMessageBox.critical(self, tr("Свойства"),
                                 tr("Не удалось сменить права: {exc}").format(exc=exc))
            return
        self.accept()


class HotlistEditor(QDialog):
    """Редактор избранных папок (Ctrl+D): добавить текущую / удалить."""

    def __init__(self, parent, items: list[dict], current_dir: str):
        super().__init__(parent)
        self.setWindowTitle(tr("Избранные папки"))
        self.resize(520, 360)
        self._items = [dict(i) for i in items]
        self._current_dir = current_dir

        self.list = QListWidget(self)
        for i in self._items:
            QListWidgetItem(f"{i.get('title') or i['path']}  —  {i['path']}",
                            self.list)

        row = QHBoxLayout()
        btn_add = QPushButton(tr("Добавить текущую"))
        btn_add.clicked.connect(self._add_current)
        btn_del = QPushButton(tr("Удалить"))
        btn_del.clicked.connect(self._delete)
        row.addWidget(btn_add)
        row.addWidget(btn_del)
        row.addStretch(1)
        btn_ok = QPushButton(tr("Сохранить"))
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton(tr("Отмена"))
        btn_cancel.clicked.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(btn_ok)
        bottom.addWidget(btn_cancel)

        layout = QVBoxLayout(self)
        layout.addWidget(self.list, 1)
        layout.addLayout(row)
        layout.addLayout(bottom)

    def _refresh(self):
        self.list.clear()
        for i in self._items:
            QListWidgetItem(f"{i.get('title') or i['path']}  —  {i['path']}",
                            self.list)

    def _add_current(self):
        from sphaera_commander import hotlist

        self._items = hotlist.add_current(self._current_dir, self._items)
        self._refresh()

    def _delete(self):
        row = self.list.currentRow()
        if 0 <= row < len(self._items):
            del self._items[row]
            self._refresh()

    def result_items(self) -> list[dict]:
        return self._items
