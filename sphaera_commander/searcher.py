"""Поиск файлов по маске и содержимому (Alt+F7), как в Total Commander.

Чистая логика без Qt: обход каталога, фильтр масками, поиск текста в файлах
с определением кодировки (utf-8/utf-16/cp1251/latin-1); бинарные файлы
пропускаются и считаются отдельно. Отмена — через is_cancelled().
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field

from .textutil import detect_decode, looks_binary

MAX_FILE_SIZE = 32 * 1024 * 1024   # файлы больше не сканируются
PROGRESS_EVERY_FILES = 25


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
        parts = [f"просмотрено файлов: {self.files_scanned}",
                 f"совпало: {self.files_matched}",
                 f"вхождений: {self.hits}"]
        if self.skipped_binary:
            parts.append(f"бинарных пропущено: {self.skipped_binary}")
        if self.skipped_large:
            parts.append(f"крупных пропущено: {self.skipped_large}")
        text = " • ".join(parts)
        if self.cancelled:
            text += " • прервано"
        if self.errors:
            text += f" • ошибок: {len(self.errors)}"
        return text


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
               hit_cb=None, progress_cb=None, is_cancelled=lambda: False
               ) -> SearchStats:
    """Фоновый обход; каждый результат — hit_cb(Hit), прогресс — progress_cb(stats)."""
    stats = SearchStats()
    patterns = parse_masks(mask_text)
    needle = needle.strip()
    for path in iter_files(root, patterns, recursive):
        if is_cancelled():
            stats.cancelled = True
            return stats
        stats.files_seen += 1
        try:
            if os.path.getsize(path) > MAX_FILE_SIZE:
                stats.skipped_large += 1
                continue
            with open(path, "rb") as f:
                raw = f.read(MAX_FILE_SIZE)
        except OSError as exc:
            stats.errors.append((path, str(exc)))
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
