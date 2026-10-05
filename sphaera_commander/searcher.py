"""Поиск файлов по маске и содержимому (Alt+F7), как в Total Commander.

Чистая логика без Qt: обход каталога, фильтр масками, фильтры по размеру и
дате изменения, поиск текста в файлах с определением кодировки
(utf-8/utf-16/cp1251/latin-1); бинарные файлы пропускаются и считаются
отдельно, а форматные документы (pdf/docx/xlsx/pptx/fb2) извлекаются
своими движками и тоже участвуют в поиске по содержимому.
Отмена — через is_cancelled().
"""

from __future__ import annotations

import fnmatch
import os
import re
import time
from dataclasses import dataclass, field

from .i18n import tr
from .textutil import detect_decode, looks_binary

MAX_FILE_SIZE = 32 * 1024 * 1024   # файлы больше не сканируются
PROGRESS_EVERY_FILES = 25

# форматные документы, в которых ищем извлечённый текст
DOC_TEXT_EXTS = (".pdf", ".docx", ".xlsx", ".xlsm", ".pptx", ".fb2",
                 ".odt", ".ods", ".odp")


@dataclass(frozen=True)
class Hit:
    path: str
    line_no: int   # 0 для совпадения только по маске
    text: str      # строка с совпадением (обрезана)


@dataclass
class SearchStats:
    files_seen: int = 0
    files_scanned: int = 0
    files_matched: int = 0
    skipped_binary: int = 0
    skipped_large: int = 0
    hits: int = 0
    errors: list = field(default_factory=list)
    cancelled: bool = False

    def summary(self) -> str:
        parts = [tr("просмотрено файлов: {n}").format(n=self.files_scanned),
                 tr("совпало файлов: {n}").format(n=self.files_matched),
                 tr("вхождений: {n}").format(n=self.hits)]
        if self.skipped_binary:
            parts.append(tr("бинарных пропущено: {n}").format(n=self.skipped_binary))
        if self.skipped_large:
            parts.append(tr("крупных пропущено: {n}").format(n=self.skipped_large))
        text = " • ".join(parts)
        if self.cancelled:
            text += tr(" • прервано")
        if self.errors:
            text += tr(" • ошибок: {n}").format(n=len(self.errors))
        return text


def parse_size_filter(text: str) -> tuple[int, int]:
    """'10к-5М', '>2Г', '<100к' → (мин, макс) в байтах (0 = без ограничения).

    Суффиксы: пусто/б, к/кб/k/kb, м/мб/m/mb, г/гб/g/gb. ValueError при мусоре.
    """
    UNITS = {"": 1, "б": 1, "b": 1, "к": 1024, "кб": 1024, "k": 1024,
             "kb": 1024, "м": 1024 ** 2, "мб": 1024 ** 2, "m": 1024 ** 2,
             "mb": 1024 ** 2, "г": 1024 ** 3, "гб": 1024 ** 3, "g": 1024 ** 3,
             "gb": 1024 ** 3}
    text = text.strip().lower().replace(" ", "")
    if not text:
        return (0, 0)
    if text.startswith(">"):
        return (_one_size(text[1:], UNITS), 0)
    if text.startswith("<"):
        return (0, _one_size(text[1:], UNITS))
    if "-" in text:
        lo, _, hi = text.partition("-")
        return (_one_size(lo, UNITS), _one_size(hi, UNITS))
    return (_one_size(text, UNITS), 0)


def _one_size(text: str, units: dict) -> int:
    digits = "".join(ch for ch in text if ch.isdigit() or ch in ".")
    suffix = text[len(digits):]
    if not digits or suffix not in units:
        raise ValueError(text)
    return int(float(digits) * units[suffix])


def parse_date_filter(from_text: str, to_text: str) -> tuple[float, float]:
    """'01.01.2024' / '01.01.2024-31.12.2024' → (от, до) в timestamp.
    0 = без ограничения; «до» включает весь указанный день."""
    if from_text.strip() and not to_text.strip() and "-" in from_text:
        from_text, _, to_text = from_text.partition("-")
    lo = parse_date_bound(from_text, end_of_day=False)
    hi = parse_date_bound(to_text, end_of_day=True)
    return (lo, hi)


def parse_date_bound(text: str, end_of_day: bool) -> float:
    text = text.strip()
    if not text:
        return 0.0
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            stamp = time.mktime(time.strptime(text, fmt))
        except ValueError:
            continue
        return stamp + (86399.0 if end_of_day else 0.0)
    raise ValueError(text)


def odf_text(path: str) -> str:
    """Текст OpenDocument (odt/ods/odp): content.xml, по абзацам.

    Свои силы: zipfile + ElementTree; текст элементов text:p/text:h
    (ячейки таблиц идут своими абзацами).
    """
    import zipfile
    import xml.etree.ElementTree as ET

    TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    with zipfile.ZipFile(path) as zf:
        content = zf.read("content.xml")
    root = ET.fromstring(content)
    paragraphs = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("p", "h"):
            text = "".join(el.itertext()).strip()
            if text:
                paragraphs.append(text)
    if not paragraphs:  # вырожденный документ — весь текст одним куском
        return " ".join(root.itertext())
    return "\n".join(paragraphs)


def doc_text(path: str) -> str:
    """Извлечь текст форматного документа своими движками (для поиска)."""
    from . import previewers as pv

    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        pages = pv.pdf_page_count(path)
        parts = []
        for i in range(min(pages, 200)):  # больше 200 страниц не ищем
            parts.append(pv.pdf_page_text(path, i))
        return "\n".join(parts)
    if ext == ".docx":
        return "\n".join(pv.docx_paragraphs(path))
    if ext in (".xlsx", ".xlsm"):
        parts = []
        for _name, rows in pv.xlsx_sheets(path):
            for row in rows:
                parts.append(" | ".join(cell for cell in row if cell))
        return "\n".join(parts)
    if ext == ".pptx":
        return "\n".join(text for _i, text in pv.pptx_slides(path))
    if ext == ".fb2":
        _title, html = pv.fb2_html(path)
        return re.sub(r"<[^>]+>", " ", html)
    if ext in (".odt", ".ods", ".odp"):
        return odf_text(path)
    raise ValueError(ext)


def parse_masks(mask_text: str) -> list[str]:
    """'*.py *.txt;README*' → шаблоны; пусто → ['*']."""
    patterns = [p.strip() for chunk in mask_text.split(";") for p in chunk.split()]
    return [p for p in patterns if p] or ["*"]


def iter_files(root: str, patterns: list[str], recursive: bool):
    if recursive:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames.sort()
            for name in sorted(filenames):
                if _match_any(name, patterns):
                    yield os.path.join(dirpath, name)
    else:
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            return
        for name in entries:
            full = os.path.join(root, name)
            if os.path.isfile(full) and _match_any(name, patterns):
                yield full


def _match_any(name: str, patterns: list[str]) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatch(lowered, p.lower()) for p in patterns)


def _find_lines(text: str, needle: str, case_sensitive: bool,
                use_regex: bool) -> list[tuple[int, str]]:
    """[(номер строки, строка)] вхождений."""
    hits = []
    if use_regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(needle, flags)
        except re.error:
            return []
        for line_no, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                hits.append((line_no, line))
    else:
        haystack = text if case_sensitive else text.lower()
        needle_cmp = needle if case_sensitive else needle.lower()
        if not needle_cmp:
            return []
        for line_no, line in enumerate(text.splitlines(), start=1):
            if needle_cmp in (line if case_sensitive else line.lower()):
                hits.append((line_no, line))
    return hits


def run_search(root: str, mask_text: str, needle: str, *, recursive: bool = True,
               case_sensitive: bool = False, use_regex: bool = False,
               size_range: tuple[int, int] = (0, 0),
               date_range: tuple[float, float] = (0.0, 0.0),
               hit_cb=None, progress_cb=None, is_cancelled=lambda: False
               ) -> SearchStats:
    """Фоновый обход; каждый результат — hit_cb(Hit), прогресс — progress_cb(stats).

    size_range: (мин, макс) в байтах, 0 = без ограничения; date_range: (от, до)
    по mtime, 0 = без ограничения.
    """
    stats = SearchStats()
    patterns = parse_masks(mask_text)
    needle = needle.strip()
    size_min, size_max = size_range
    date_from, date_to = date_range
    for path in iter_files(root, patterns, recursive):
        if is_cancelled():
            stats.cancelled = True
            return stats
        stats.files_seen += 1
        try:
            size = os.path.getsize(path)
            if size > MAX_FILE_SIZE:
                stats.skipped_large += 1
                continue
            if size_min and size < size_min:
                continue
            if size_max and size > size_max:
                continue
            if date_from and os.path.getmtime(path) < date_from:
                continue
            if date_to and os.path.getmtime(path) > date_to:
                continue
            with open(path, "rb") as f:
                raw = f.read(MAX_FILE_SIZE)
        except OSError as exc:
            stats.errors.append((path, str(exc)))
            continue
        ext = os.path.splitext(path)[1].lower()
        if ext in DOC_TEXT_EXTS:
            # форматные документы: извлекаем текст своими движками
            try:
                text = doc_text(path)
            except Exception:
                stats.skipped_binary += 1
                continue
            stats.files_scanned += 1
            if not needle:
                stats.files_matched += 1
                stats.hits += 1
                if hit_cb:
                    hit_cb(Hit(path=path, line_no=0, text=""))
            else:
                matched_lines = _find_lines(text, needle, case_sensitive,
                                            use_regex)
                if matched_lines:
                    stats.files_matched += 1
                    stats.hits += len(matched_lines[:200])
                    for line_no, line in matched_lines[:200]:
                        if hit_cb:
                            hit_cb(Hit(path=path, line_no=line_no,
                                       text=line.strip()[:300]))
            if progress_cb and stats.files_seen % PROGRESS_EVERY_FILES == 0:
                progress_cb(stats)
            continue
        if looks_binary(raw):
            stats.skipped_binary += 1
            continue
        try:
            text, _enc = detect_decode(raw)
        except (UnicodeDecodeError, ValueError):
            stats.skipped_binary += 1
            continue
        stats.files_scanned += 1
        if not needle:
            stats.files_matched += 1
            stats.hits += 1
            if hit_cb:
                hit_cb(Hit(path=path, line_no=0, text=""))
        else:
            lines = _find_lines(text, needle, case_sensitive, use_regex)
            if lines:
                stats.files_matched += 1
                for line_no, line in lines[:200]:  # максимум 200 строк на файл
                    stats.hits += 1
                    if hit_cb:
                        hit_cb(Hit(path=path, line_no=line_no,
                                   text=line.strip()[:300]))
        if progress_cb and stats.files_seen % PROGRESS_EVERY_FILES == 0:
            progress_cb(stats)
    return stats
