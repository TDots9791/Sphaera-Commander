"""Архивы: запаковка (zip / tar.gz / tar.bz2 / tar.xz) и распаковка.

Симлинки при запаковке разыменовываются (в zip нет переносимого формата ссылок).
Распаковка защищена от zip-slip: элементы с абсолютными путями, ".." и
устройствами отклоняются с записью ошибки.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import tarfile
import zipfile
from typing import Callable

from .ops import (
    ASK_OVERWRITE,
    ASK_SKIP,
    ConflictInfo,
    FileError,
    OpResult,
    Progress,
    _conflict_info,
)

PROGRESS_EVERY_FILES = 8

ZIP_EXTS = (".zip",)
TAR_EXTS = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")
ARCHIVE_EXTS = ZIP_EXTS + TAR_EXTS


def archive_format(path: str) -> str | None:
    name = os.path.basename(path).lower()
    for ext in ARCHIVE_EXTS:
        if name.endswith(ext):
            return "zip" if ext in ZIP_EXTS else "tar"
    return None


def _progress_state(done_files: int, total_files: int, done_bytes: int,
                    total_bytes: int, current: str) -> Progress:
    return Progress(phase="run", current=current, done_files=done_files,
                    total_files=total_files, done_bytes=done_bytes,
                    total_bytes=total_bytes)


# ---------------------------------------------------------------- запаковка

def pack_items(sources: list, out_path: str, fmt: str,
               progress_cb: Callable[[Progress], None],
               is_cancelled: Callable[[], bool]) -> OpResult:
    """Запаковать объекты (FileEntry) в out_path. Каталоги обходятся рекурсивно."""
    result = OpResult()
    base = os.path.dirname(sources[0].path) if sources else "."
    # фаза подсчёта
    files: list[tuple[str, str, int]] = []  # (абс-путь, имя в архиве, размер)
    for entry in sources:
        if entry.is_dir and not entry.is_link:
            for dirpath, dirnames, filenames in os.walk(entry.path, followlinks=False):
                dirnames.sort()
                filenames.sort()
                for name in filenames:
                    full = os.path.join(dirpath, name)
                    arcname = os.path.relpath(full, base)
                    try:
                        size = os.lstat(full).st_size
                    except OSError:
                        size = 0
                    files.append((full, arcname, size))
        else:
            arcname = entry.name
            try:
                size = os.lstat(entry.path).st_size
            except OSError:
                size = 0
            files.append((entry.path, arcname, size))

    total_bytes = sum(f[2] for f in files)
    progress_cb(_progress_state(0, len(files), 0, total_bytes, ""))

    try:
        if fmt == "zip":
            with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for i, (full, arcname, size) in enumerate(files):
                    if is_cancelled():
                        result.cancelled = True
                        return result
                    try:
                        zf.write(full, arcname)
                        result.done_files += 1
                        result.done_bytes += size
                    except OSError as exc:
                        result.errors.append(FileError(full, exc.strerror or str(exc)))
                    if i % PROGRESS_EVERY_FILES == 0 or i == len(files) - 1:
                        progress_cb(_progress_state(result.done_files, len(files),
                                                    result.done_bytes, total_bytes, full))
        else:
            mode = "w:gz" if fmt == "tar.gz" else ("w:bz2" if fmt == "tar.bz2" else "w:xz")
            with tarfile.open(out_path, mode) as tf:
                for i, (full, arcname, size) in enumerate(files):
                    if is_cancelled():
                        result.cancelled = True
                        return result
                    try:
                        tf.add(full, arcname=arcname, recursive=False)
                        result.done_files += 1
                        result.done_bytes += size
                    except OSError as exc:
                        result.errors.append(FileError(full, exc.strerror or str(exc)))
                    if i % PROGRESS_EVERY_FILES == 0 or i == len(files) - 1:
                        progress_cb(_progress_state(result.done_files, len(files),
                                                    result.done_bytes, total_bytes, full))
    except OSError as exc:
        result.errors.append(FileError(out_path, exc.strerror or str(exc)))
        return result

    if is_cancelled():
        result.cancelled = True
    return result


# ---------------------------------------------------------------- распаковка

_DRIVE = re.compile(r"^[A-Za-z]:")


def _safe_member(name: str) -> bool:
    raw = name.replace("\\", "/")
    norm = raw.lstrip("/")
    if raw.startswith("/") or norm == ".." or norm.startswith("../") or "/../" in norm:
        return False
    return not _DRIVE.match(norm)


def unpack_archive(archive_path: str, dest_dir: str,
                   progress_cb: Callable[[Progress], None],
                   is_cancelled: Callable[[], bool],
                   ask_cb=None) -> OpResult:
    """Распаковать архив в dest_dir с прогрессом, отменой и защитой путей."""
    result = OpResult()
    fmt = archive_format(archive_path)
    if fmt is None:
        result.errors.append(FileError(archive_path, "неизвестный формат архива"))
        return result
    bulk = {"overwrite": False, "skip": False}

    def resolve(info: ConflictInfo) -> str | None:
        if bulk["overwrite"]:
            return "overwrite"
        if bulk["skip"]:
            return "skip"
        if ask_cb is None:
            return "overwrite"
        answer = ask_cb(info)
        if answer == ASK_OVERWRITE:
            return "overwrite"
        if answer == ASK_SKIP:
            return "skip"
        if answer == "overwrite_all":
            bulk["overwrite"] = True
            return "overwrite"
        if answer == "skip_all":
            bulk["skip"] = True
            return "skip"
        return None

    try:
        if fmt == "zip":
            members = _zip_members(archive_path)
        else:
            members = _tar_members(archive_path)
    except (OSError, zipfile.BadZipFile, tarfile.TarError) as exc:
        result.errors.append(FileError(archive_path, str(exc)))
        return result

    total_bytes = sum(m[1] for m in members)
    progress_cb(_progress_state(0, len(members), 0, total_bytes, ""))

    os.makedirs(dest_dir, exist_ok=True)
    done_bytes = 0
    for i, (member_name, size, kind) in enumerate(members):
        if is_cancelled():
            result.cancelled = True
            return result
        target = os.path.join(dest_dir, *member_name.replace("\\", "/").split("/"))
        progress_cb(_progress_state(result.done_files, len(members),
                                    done_bytes, total_bytes, member_name))
        if kind == "unsupported":
            result.errors.append(FileError(member_name, "тип элемента не поддерживается"))
            continue
        if not _safe_member(member_name):
            result.errors.append(FileError(member_name, "опасный путь в архиве (zip-slip)"))
            continue
        try:
            if kind == "dir":
                os.makedirs(target, exist_ok=True)
            elif kind == "link":
                os.makedirs(os.path.dirname(target), exist_ok=True)
                _extract_link(fmt, archive_path, member_name, target)
                result.done_files += 1
            else:  # file
                os.makedirs(os.path.dirname(target), exist_ok=True)
                if os.path.lexists(target):
                    action = resolve(_conflict_info(archive_path + "::" + member_name, target))
                    if action is None:
                        result.cancelled = True
                        return result
                    if action == "skip":
                        result.skipped += 1
                        continue
                    if os.path.isdir(target) and not os.path.islink(target):
                        result.errors.append(FileError(target, "каталог с тем же именем"))
                        continue
                    os.unlink(target)
                _extract_file(fmt, archive_path, member_name, target)
                result.done_files += 1
                done_bytes += size
        except (OSError, zipfile.BadZipFile, tarfile.TarError, KeyError) as exc:
            result.errors.append(FileError(member_name, str(exc)))
    result.done_bytes = done_bytes
    return result


def _zip_members(archive_path: str):
    out = []
    with zipfile.ZipFile(archive_path) as zf:
        for info in zf.infolist():
            name = info.filename
            if name.endswith("/"):
                out.append((name, 0, "dir"))
            elif stat.S_ISLNK(info.external_attr >> 16) and info.file_size == 0:
                out.append((name, 0, "unsupported"))  # unix-ссылки в zip нестандартны
            else:
                out.append((name, info.file_size, "file"))
    return out


def _tar_members(archive_path: str):
    out = []
    with tarfile.open(archive_path) as tf:
        for m in tf.getmembers():
            if m.isdir():
                out.append((m.name, 0, "dir"))
            elif m.issym() or m.islnk():
                out.append((m.name, m.size, "link"))
            elif m.isreg():
                out.append((m.name, m.size, "file"))
            else:
                out.append((m.name, 0, "unsupported"))
    return out


def _extract_file(fmt: str, archive_path: str, member: str, target: str) -> None:
    if fmt == "zip":
        with zipfile.ZipFile(archive_path) as zf:
            with zf.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
    else:
        with tarfile.open(archive_path) as tf:
            src = tf.extractfile(member)
            if src is None:
                raise OSError(f"не удалось прочитать элемент: {member}")
            with open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)


def _extract_link(fmt: str, archive_path: str, member: str, target: str) -> None:
    if os.path.lexists(target):
        os.unlink(target)
    if fmt == "zip":
        raise OSError("ссылки в zip не поддерживаются")
    with tarfile.open(archive_path) as tf:
        m = tf.getmember(member)
        link = m.linkname if m.issym() else os.path.abspath(m.linkname)
        os.symlink(link, target)
