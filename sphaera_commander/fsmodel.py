"""Модель содержимого каталога: обход, сортировка, отметки, табличное представление."""

from __future__ import annotations

import os
import stat as stat_m
import time
from dataclasses import dataclass

from PySide6.QtCore import QAbstractTableModel, QMimeData, QModelIndex, Qt, QUrl
from PySide6.QtGui import QBrush, QColor, QIcon
from PySide6.QtWidgets import QApplication, QStyle

from . import thumbnails

NAME_COL, EXT_COL, SIZE_COL, MTIME_COL, MODE_COL = range(5)
COLUMNS = ("Имя", "Расш.", "Размер", "Изменён", "Права")

DIR_SIZE_TEXT = "<КАТ>"
MARK_COLOR_LIGHT = QColor(192, 0, 0)
MARK_COLOR_DARK = QColor(255, 106, 106)
COMPARE_COLOR_LIGHT = QColor(0, 90, 200)
COMPARE_COLOR_DARK = QColor(120, 170, 255)


@dataclass(frozen=True)
class FileEntry:
    name: str
    path: str
    is_dir: bool
    is_link: bool
    size: int
    mtime: float
    mode: int


@dataclass(frozen=True)
class DirStats:
    size: int
    dirs: int
    files: int


DOTDOT = FileEntry(name="..", path="", is_dir=True, is_link=False, size=-1, mtime=0.0, mode=0)


def scan_directory(path: str, show_hidden: bool) -> list[FileEntry]:
    """Вернуть список записей каталога; OSError пробрасывается вызывающему."""
    entries: list[FileEntry] = []
    with os.scandir(path) as it:
        for entry in it:
            if not show_hidden and entry.name.startswith("."):
                continue
            try:
                is_link = entry.is_symlink()
                is_dir = entry.is_dir(follow_symlinks=True)
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            entries.append(
                FileEntry(
                    name=entry.name,
                    path=os.path.join(path, entry.name),
                    is_dir=is_dir,
                    is_link=is_link,
                    size=0 if is_dir else st.st_size,
                    mtime=st.st_mtime,
                    mode=st.st_mode,
                )
            )
    return entries


def ext_of(e: FileEntry) -> str:
    """Расширение файла без точки; у каталогов и точечных файлов пусто."""
    if e.is_dir:
        return ""
    return os.path.splitext(e.name)[1][1:]


def sort_entries(entries: list[FileEntry], col: int, desc: bool) -> None:
    """Сортировка на месте: каталоги всегда первыми, затем ключ колонки, затем имя."""
    keys = {
        NAME_COL: lambda e: e.name.lower(),
        EXT_COL: lambda e: (ext_of(e).lower(), e.name.lower()),
        SIZE_COL: lambda e: e.size,
        MTIME_COL: lambda e: e.mtime,
    }
    entries.sort(key=lambda e: e.name.lower())
    entries.sort(key=keys.get(col, keys[NAME_COL]), reverse=desc)
    entries.sort(key=lambda e: 0 if e.is_dir else 1)


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} Б"
    for unit in ("КиБ", "МиБ", "ГиБ", "ТиБ"):
        n /= 1024.0
        if n < 1024:
            return f"{n:,.1f}".replace(",", " ").removesuffix(".0") + " " + unit
    return f"{n:,.0f}".replace(",", " ") + " ПиБ"


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русские формы: 1 файл / 2 файла / 5 файлов."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} {few}"
    return f"{n} {many}"


def dir_stats(path: str) -> DirStats:
    """Рекурсивно: суммарный размер, число подпапок и файлов.
    Симлинки не разворачиваются (как du без -L): считаются файлами;
    ошибки доступа пропускаются."""
    size = dirs = files = 0
    stack = [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            dirs += 1
                            stack.append(entry.path)
                        else:
                            files += 1
                            size += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return DirStats(size, dirs, files)


def mode_string(mode: int) -> str:
    return stat_m.filemode(mode)[1:] if mode else ""


def compare_name_sets(left: dict[str, FileEntry],
                      right: dict[str, FileEntry]) -> tuple[set[str], set[str]]:
    """Имена файлов, отсутствующие или различающиеся (размер/время) в другой панели."""
    def diff(mine: dict[str, FileEntry], other: dict[str, FileEntry]) -> set[str]:
        out = set()
        for name, e in mine.items():
            o = other.get(name)
            if o is None or o.size != e.size or int(o.mtime) != int(e.mtime):
                out.add(name)
        return out

    return diff(left, right), diff(right, left)


def entry_for(path: str) -> FileEntry | None:
    """Запись для существующего файла/каталога (для перетаскивания извне)."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return FileEntry(
        name=os.path.basename(path) or path,
        path=path,
        is_dir=os.path.isdir(path),
        is_link=os.path.islink(path),
        size=0 if os.path.isdir(path) else st.st_size,
        mtime=st.st_mtime,
        mode=st.st_mode,
    )


class FileTableModel(QAbstractTableModel):
    """Строки: '..' + записи каталога; отметки и результат сравнения — по имени."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path: str = "/"
        self.entries: list[FileEntry] = []
        self.show_hidden = False
        self.sort_col = NAME_COL
        self.sort_desc = False
        self.marked: set[str] = set()
        self.compared: set[str] = set()
        self.thumbnails_on = False
        self._thumb_store = None
        self._icons: dict[str, QIcon] = {}

    def set_thumbnails(self, on: bool, store=None) -> None:
        self.thumbnails_on = on
        if store is not None:
            self._thumb_store = store

    def notify_thumbnail(self, path: str) -> None:
        """Миниатюра готова — обновить строку, если файл в списке."""
        if not self.thumbnails_on:
            return
        for i, e in enumerate(self.entries, start=1):
            if e.path == path:
                self.dataChanged.emit(self.index(i, 0), self.index(i, 0))
                return

    # -- загрузка ----------------------------------------------------------

    def set_entries(self, path: str, entries: list[FileEntry]) -> None:
        """Применить готовый (отсортированный) список; отметки сохраняются по имени."""
        names = {e.name for e in entries}
        self.marked &= names
        self.compared &= names
        self.beginResetModel()
        self.path = path
        self.entries = entries
        self.endResetModel()

    def reload(self, path: str | None = None) -> None:
        if path is not None:
            self.path = path
        entries = scan_directory(self.path, self.show_hidden)
        sort_entries(entries, self.sort_col, self.sort_desc)
        self.set_entries(self.path, entries)

    def apply_sort(self, col: int, desc: bool | None = None) -> None:
        """Пересортировать уже загруженные записи (без повторного чтения диска)."""
        if col == self.sort_col and desc is None:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col = col
            self.sort_desc = bool(desc)
        sort_entries(self.entries, self.sort_col, self.sort_desc)
        self.beginResetModel()
        self.endResetModel()
        self.headerDataChanged.emit(Qt.Horizontal, 0, len(COLUMNS) - 1)

    # -- доступ ------------------------------------------------------------

    def row_count(self) -> int:
        return len(self.entries) + 1  # строка '..'

    def entry_at(self, row: int) -> FileEntry | None:
        if row <= 0:
            return None
        if row > len(self.entries):
            return None
        return self.entries[row - 1]

    def row_of_name(self, name: str) -> int:
        for i, e in enumerate(self.entries):
            if e.name == name:
                return i + 1
        return 0

    def marked_entries(self) -> list[FileEntry]:
        if not self.marked:
            return []
        return [e for e in self.entries if e.name in self.marked]

    def summary(self) -> tuple[int, int, int, int, int]:
        """(файлов, папок, байт, отмечено объектов, отмечено байт)."""
        files = sum(1 for e in self.entries if not e.is_dir)
        dirs = len(self.entries) - files
        total = sum(e.size for e in self.entries if not e.is_dir)
        m_entries = self.marked_entries()
        m_bytes = sum(e.size for e in m_entries if not e.is_dir)
        return files, dirs, total, len(m_entries), m_bytes

    # -- отметки -----------------------------------------------------------

    def toggle_mark(self, row: int) -> None:
        e = self.entry_at(row)
        if e is None:
            return
        self._set_mark(e.name, e.name not in self.marked)

    def set_mark(self, name: str, on: bool) -> None:
        self._set_mark(name, on)

    def _set_mark(self, name: str, on: bool) -> None:
        if on:
            self.marked.add(name)
        else:
            self.marked.discard(name)
        row = self.row_of_name(name)
        if row:
            self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMNS) - 1))

    def invert_marks(self) -> None:
        names = [e.name for e in self.entries if not e.is_dir]
        self.marked = {n for n in names if n not in self.marked}
        self.dataChanged.emit(self.index(1, 0), self.index(len(self.entries), len(COLUMNS) - 1))

    def mark_mask(self, pattern: str, on: bool) -> int:
        import fnmatch

        count = 0
        for e in self.entries:
            if e.is_dir:
                continue
            if fnmatch.fnmatch(e.name.lower(), pattern.lower()):
                self._set_mark(e.name, on)
                count += 1
        return count

    def clear_marks(self) -> None:
        self.marked.clear()
        if self.entries:
            self.dataChanged.emit(self.index(1, 0), self.index(len(self.entries), len(COLUMNS) - 1))

    def set_compared(self, names: set[str]) -> None:
        """Отметить имена как отличающиеся при сравнении каталогов (синим)."""
        existing = {e.name for e in self.entries}
        self.compared = set(names) & existing
        if self.entries:
            self.dataChanged.emit(self.index(1, 0),
                                  self.index(len(self.entries), len(COLUMNS) - 1))

    # -- Qt model ----------------------------------------------------------

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.row_count()

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def data(self, index, role=Qt.DisplayRole):
        row = index.row()
        if row == 0:  # '..'
            if role == Qt.DisplayRole:
                return ("..", "", "", "", "")[index.column()]
            if role == Qt.DecorationRole and index.column() == NAME_COL:
                return self._icon("up")
            return None
        e = self.entries[row - 1]
        col = index.column()
        if role == Qt.DisplayRole:
            if col == NAME_COL:
                return e.name
            if col == EXT_COL:
                return ext_of(e)
            if col == SIZE_COL:
                return DIR_SIZE_TEXT if e.is_dir else human_size(e.size)
            if col == MTIME_COL:
                return time.strftime("%d.%m.%Y %H:%M", time.localtime(e.mtime))
            if col == MODE_COL:
                return mode_string(e.mode)
        if role == Qt.DecorationRole and col == NAME_COL:
            if (self.thumbnails_on and self._thumb_store is not None
                    and not e.is_dir and thumbnails.is_image(e.path)):
                icon = self._thumb_store.get(e.path, e.mtime, e.size)
                if icon is not None and not icon.isNull():
                    return icon
                return self._icon("file")  # заглушка, пока рисуется в фоне
            return self._icon("dir" if e.is_dir else ("link" if e.is_link else "file"))
        if role == Qt.ForegroundRole:
            pal = QApplication.instance().palette()
            dark = pal.color(pal.ColorRole.Window).lightness() < 128
            if e.name in self.marked:
                return QBrush(MARK_COLOR_DARK if dark else MARK_COLOR_LIGHT)
            if e.name in self.compared:
                return QBrush(COMPARE_COLOR_DARK if dark else COMPARE_COLOR_LIGHT)
            return None
        if role == Qt.TextAlignmentRole and col == SIZE_COL:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.UserRole:
            return e
        if role == Qt.ToolTipRole and col == SIZE_COL:
            return f"{e.size:,}".replace(",", " ")
        return None

    def flags(self, index):
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.isValid() and index.row() > 0:
            base |= Qt.ItemIsDragEnabled
        return base

    def supportedDragActions(self):
        return Qt.CopyAction

    def mimeData(self, indexes):
        rows = sorted({i.row() for i in indexes if i.isValid() and i.row() > 0})
        entries: list[FileEntry] = []
        if self.marked:
            entries = self.marked_entries()
        else:
            entries = [e for e in (self.entry_at(r) for r in rows) if e]
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(e.path) for e in entries])
        return mime

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            title = COLUMNS[section]
            if section == self.sort_col:
                title += " ↓" if self.sort_desc else " ↑"
            return title
        return None

    def _icon(self, kind: str) -> QIcon:
        if not self._icons:
            style = QApplication.instance().style()
            self._icons = {
                "dir": style.standardIcon(QStyle.StandardPixmap.SP_DirIcon),
                "file": style.standardIcon(QStyle.StandardPixmap.SP_FileIcon),
                "link": style.standardIcon(QStyle.StandardPixmap.SP_FileLinkIcon),
                "up": style.standardIcon(QStyle.StandardPixmap.SP_ArrowUp),
            }
        return self._icons[kind]
