"""Архивы: запаковка, распаковка и browsing (VFS).

Форматы: zip, tar, tar.gz, tar.bz2, tar.xz — встроенными средствами;
7z и RAR — через внешние утилиты (7z/p7zip и unrar, без новых
python-зависимостей): 7z — запаковка/распаковка/чтение, RAR — только
чтение и распаковка (запись RAR невозможна — проприетарный формат);
изменение состава VFS для внешних форматов в 0.19.0 не делается.
Отсутствие утилиты — честная ошибка «не установлен», не падение.
Симлинки сохраняются: в tar — нативно, в zip — общепринятым unix-способом
(external_attr = S_IFLNK, содержимое = путь цели); распаковка восстанавливает.
Распаковка защищена от zip-slip: абсолютные пути, ".." и Windows-диски
отклоняются с записью ошибки. Замена архива идёт через временный файл + os.replace.
"""

from __future__ import annotations

import calendar
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import zipfile
from dataclasses import dataclass
from typing import Callable

from .fsmodel import FileEntry
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
SEVENZIP_EXTS = (".7z",)
RAR_EXTS = (".rar",)
ARCHIVE_EXTS = ZIP_EXTS + TAR_EXTS + SEVENZIP_EXTS + RAR_EXTS

_DRIVE = re.compile(r"^[A-Za-z]:")


def archive_format(path: str) -> str | None:
    name = os.path.basename(path).lower()
    for ext in ARCHIVE_EXTS:
        if name.endswith(ext):
            if ext in ZIP_EXTS:
                return "zip"
            if ext in SEVENZIP_EXTS:
                return "7z"
            if ext in RAR_EXTS:
                return "rar"
            return "tar"
    return None


def external_tool(fmt: str) -> str | None:
    """Путь к внешней утилите формата или None (graceful «не установлена»)."""
    return shutil.which("7z" if fmt == "7z" else "unrar")


def norm_member(name: str) -> str:
    name = name.replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    return name.rstrip("/")


def _safe_member(name: str) -> bool:
    raw = name.replace("\\", "/")
    norm = raw.lstrip("/")
    if raw.startswith("/") or norm == ".." or norm.startswith("../") or "/../" in norm:
        return False
    return not _DRIVE.match(norm)


def _progress_state(done_files: int, total_files: int, done_bytes: int,
                    total_bytes: int, current: str) -> Progress:
    return Progress(phase="run", current=current, done_files=done_files,
                    total_files=total_files, done_bytes=done_bytes,
                    total_bytes=total_bytes)


# ---------------------------------------------------------------- запаковка

def _zip_link_info(arcname: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(arcname)
    info.create_system = 3  # unix
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


def pack_items(sources: list, out_path: str, fmt: str,
               progress_cb: Callable[[Progress], None],
               is_cancelled: Callable[[], bool],
               excludes: list[str] | None = None) -> OpResult:
    """Запаковать объекты (FileEntry) в out_path. Каталоги — явно, включая пустые.
    excludes — fnmatch-шаблоны имён (без пути), применяются к файлам и каталогам
    внутри упаковываемых деревьев."""
    import fnmatch

    result = OpResult()
    base = os.path.dirname(sources[0].path) if sources else "."

    def _excluded(name: str) -> bool:
        return any(fnmatch.fnmatch(name, pat) for pat in (excludes or []))

    # (путь | None, имя в архиве, размер, тип) — путь None у zip-заглушек каталогов
    items: list[tuple[str | None, str, int, str]] = []
    for entry in sources:
        if entry.is_link:
            items.append((entry.path, entry.name, 0, "link"))
            continue
        if entry.is_dir:
            for dirpath, dirnames, filenames in os.walk(entry.path, followlinks=False):
                dirnames.sort()
                filenames.sort()
                dirnames[:] = [d for d in dirnames if not _excluded(d)]
                filenames = [f for f in filenames if not _excluded(f)]
                rel_dir = os.path.relpath(dirpath, base)
                if rel_dir != ".":
                    items.append((dirpath, rel_dir, 0, "dir"))
                linked = [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]
                for name in linked:
                    full = os.path.join(dirpath, name)
                    items.append((full, os.path.relpath(full, base), 0, "link"))
                for name in filenames:
                    full = os.path.join(dirpath, name)
                    try:
                        size = os.lstat(full).st_size
                    except OSError:
                        size = 0
                    is_link = os.path.islink(full)
                    items.append((full, os.path.relpath(full, base),
                                  0 if is_link else size, "link" if is_link else "file"))
        elif _excluded(entry.name):
            continue
        else:
            try:
                size = os.lstat(entry.path).st_size
            except OSError:
                size = 0
            items.append((entry.path, entry.name, size, "file"))

    total_files = len(items)
    total_bytes = sum(i[2] for i in items)
    progress_cb(_progress_state(0, total_files, 0, total_bytes, ""))

    try:
        if fmt == "7z":
            tool = external_tool("7z")
            if tool is None:
                result.errors.append(FileError(out_path, "7z (p7zip) не установлен"))
                return result
            # один вызов на все источники; прогресс грубый (до/после)
            args = [tool, "a", "-bd", "--", out_path]
            args += [i[0] for i in items]
            proc = subprocess.run(args, capture_output=True, text=True,
                                  timeout=600)
            if is_cancelled():
                result.cancelled = True
                return result
            if proc.returncode != 0:
                result.errors.append(FileError(
                    out_path, proc.stderr.strip() or "7z не смог упаковать"))
                return result
            result.done_files = total_files
            result.done_bytes = total_bytes
            progress_cb(_progress_state(total_files, total_files,
                                        total_bytes, total_bytes, out_path))
            return result
        if fmt == "rar":
            result.errors.append(FileError(
                out_path, "запись RAR не поддерживается (проприетарный формат)"))
            return result
        if fmt == "zip":
            with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for i, (full, arcname, size, kind) in enumerate(items):
                    if is_cancelled():
                        result.cancelled = True
                        return result
                    try:
                        if kind == "dir":
                            info = zipfile.ZipInfo(arcname + "/", date_time=_now_zip_time())
                            info.external_attr = (stat.S_IFDIR | 0o755) << 16
                            info.create_system = 3
                            zf.writestr(info, b"")
                        elif kind == "link":
                            zf.writestr(_zip_link_info(arcname), os.readlink(full))
                        else:
                            zf.write(full, arcname)
                        result.done_files += 1
                        result.done_bytes += size
                    except OSError as exc:
                        result.errors.append(FileError(full or arcname,
                                                       exc.strerror or str(exc)))
                    if i % PROGRESS_EVERY_FILES == 0 or i == total_files - 1:
                        progress_cb(_progress_state(result.done_files, total_files,
                                                    result.done_bytes, total_bytes,
                                                    full or arcname))
        else:
            mode = ("w:gz" if fmt == "tar.gz"
                    else "w:bz2" if fmt == "tar.bz2" else "w:xz")
            with tarfile.open(out_path, mode) as tf:
                for i, (full, arcname, size, kind) in enumerate(items):
                    if is_cancelled():
                        result.cancelled = True
                        return result
                    try:
                        tf.add(full, arcname=arcname, recursive=False)  # dereference=False
                        result.done_files += 1
                        result.done_bytes += size
                    except OSError as exc:
                        result.errors.append(FileError(full or arcname,
                                                       exc.strerror or str(exc)))
                    if i % PROGRESS_EVERY_FILES == 0 or i == total_files - 1:
                        progress_cb(_progress_state(result.done_files, total_files,
                                                    result.done_bytes, total_bytes,
                                                    full or arcname))
    except OSError as exc:
        result.errors.append(FileError(out_path, exc.strerror or str(exc)))
        return result

    if is_cancelled():
        result.cancelled = True
    return result


# ---------------------------------------------------------------- члены архива

def _zip_members(archive_path: str) -> list[tuple[str, int, str]]:
    out = []
    with zipfile.ZipFile(archive_path) as zf:
        for info in zf.infolist():
            name = info.filename
            if name.endswith("/"):
                out.append((name, 0, "dir"))
            elif (info.create_system == 3
                  and stat.S_ISLNK(info.external_attr >> 16)):
                out.append((name, 0, "link"))
            else:
                out.append((name, info.file_size, "file"))
    return out


def _tar_members(archive_path: str) -> list[tuple[str, int, str]]:
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


def _7z_members(archive_path: str) -> list[tuple[str, int, str]]:
    """Список членов 7z: `7z l -ba -slt` — блоки «Path = …», «Size = …»."""
    tool = external_tool("7z")
    if tool is None:
        raise ValueError("7z (p7zip) не установлен — формат 7z недоступен")
    proc = subprocess.run(
        # -sccUTF-8: на Windows 7z печатает имена в консольной кодировке
        # (OEM), из-за чего кириллические члены не совпадали с ожидаемыми
        [tool, "l", "-ba", "-slt", "-sccUTF-8", "--", archive_path],
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise OSError(proc.stderr.strip() or "7z не смог прочитать архив")
    out: list[tuple[str, int, str]] = []
    entry: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if not line.strip():
            entry = {}
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key == "Path":
            entry["path"] = value.strip()
        elif key == "Size" and "size" not in entry:
            entry["size"] = value.strip()
        elif key == "Attributes":
            entry["attrs"] = value.strip()
        if {"path", "size", "attrs"} <= entry.keys():
            name = entry["path"]
            is_dir = entry["attrs"].find("D") >= 0 or name.endswith("/")
            out.append((name, 0 if is_dir else _int_or_0(entry.get("size")),
                        "dir" if is_dir else "file"))
            entry = {}
    return out


def _7z_rewrite(archive_path: str, skip: set[str], replace: dict[str, str],
                result) -> OpResult:
    """Правка состава 7z утилитой `7z d`/`7z u`. Работа на копии архива,
    проверка целостности `7z t`, затем os.replace — атомарность как у
    zip/tar-пути."""
    tool = external_tool("7z")
    if tool is None:
        return OpResult(errors=[FileError(
            archive_path, "7z (p7zip) не установлен — правка 7z недоступна")])
    base_dir = os.path.dirname(archive_path) or "."
    work_fd, work_path = tempfile.mkstemp(prefix=".sc_7z_", suffix=".7z",
                                          dir=base_dir)
    os.close(work_fd)
    stage = tempfile.mkdtemp(prefix=".sc_7z_stage_", dir=base_dir)
    try:
        shutil.copy2(archive_path, work_path)
        if skip:
            proc = subprocess.run(
                [tool, "d", "-ba", "--", work_path, *sorted(skip)],
                capture_output=True, text=True, timeout=600)
            if proc.returncode != 0:
                raise OSError(proc.stderr.strip() or "7z не смог удалить члены")
            result.skipped += len(skip)
        for member, src_path in sorted(replace.items()):
            dest = os.path.join(stage, member)
            os.makedirs(os.path.dirname(dest) or stage, exist_ok=True)
            shutil.copy2(src_path, dest)
            # cwd=stage: в архив попадает относительный путь = имя члена
            proc = subprocess.run(
                [tool, "u", "--", work_path, member],
                capture_output=True, text=True, timeout=600, cwd=stage)
            if proc.returncode != 0:
                raise OSError(proc.stderr.strip()
                              or "7z не смог обновить член архива")
            result.done_files += 1
            result.done_bytes += os.path.getsize(src_path)
        proc = subprocess.run([tool, "t", work_path],
                              capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise OSError(proc.stderr.strip() or "7z: целостность нарушена")
        os.replace(work_path, archive_path)
    except Exception as exc:
        try:
            os.unlink(work_path)
        except OSError:
            pass
        if isinstance(exc, (OSError, ValueError, subprocess.SubprocessError)):
            result.errors.append(FileError(archive_path, str(exc)))
        else:
            raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return result


def _rar_members(archive_path: str) -> list[tuple[str, int, str]]:
    """Список членов RAR с размерами: `unrar vt` (блоки «Name:/Size:/Type:»).
    Старый unrar без vt — откат на `unrar lb` (только имена, размеры 0)."""
    tool = external_tool("rar")
    if tool is None:
        raise ValueError("unrar не установлен — формат RAR недоступен")
    proc = subprocess.run(
        [tool, "vt", "--", archive_path],
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        return _rar_members_names_only(tool, archive_path)
    out: list[tuple[str, int, str]] = []
    current: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        line = line.rstrip()
        if not line.strip():
            if current.get("name"):
                out.append(_rar_block_to_member(current))
            current = {}
            continue
        if line.startswith("Archive:") or line.startswith("Details:"):
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key in ("name", "size", "type"):
            current[key] = value.strip()
    if current.get("name"):
        out.append(_rar_block_to_member(current))
    if not out:
        return _rar_members_names_only(tool, archive_path)
    return out


def _rar_block_to_member(block: dict[str, str]) -> tuple[str, int, str]:
    name = block.get("name", "")
    kind = "file"
    if block.get("type", "").lower() == "directory" or name.endswith("/"):
        kind = "dir"
    return (name, 0 if kind == "dir" else _int_or_0(block.get("size")), kind)


def _rar_members_names_only(tool: str, archive_path: str
                            ) -> list[tuple[str, int, str]]:
    """Старый unrar: `unrar lb` — только имена (размеры 0)."""
    proc = subprocess.run(
        [tool, "lb", "--", archive_path],
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise OSError(proc.stderr.strip() or "unrar не смог прочитать архив")
    out = []
    for line in proc.stdout.splitlines():
        name = line.rstrip()
        if not name or name.startswith(("---", "Volume")):
            continue
        if name.endswith("/"):
            out.append((name, 0, "dir"))
        else:
            out.append((name, 0, "file"))
    return out


def _int_or_0(text: str | None) -> int:
    try:
        return int(text or 0)
    except ValueError:
        return 0


def read_members(archive_path: str) -> list[tuple[str, int, str]]:
    fmt = archive_format(archive_path)
    if fmt == "zip":
        return _zip_members(archive_path)
    if fmt == "tar":
        return _tar_members(archive_path)
    if fmt == "7z":
        return _7z_members(archive_path)
    if fmt == "rar":
        return _rar_members(archive_path)
    raise ValueError(f"неизвестный формат архива: {archive_path}")


# ---------------------------------------------------------------- распаковка

def _extract_core(fmt: str, archive_path: str, dest_dir: str,
                  members: list[tuple[str, int, str]],
                  progress_cb: Callable[[Progress], None],
                  is_cancelled: Callable[[], bool],
                  ask_cb=None) -> OpResult:
    """Извлечь перечисленные (нормализованные) члены в dest_dir."""
    result = OpResult()
    bulk = {"overwrite": False, "skip": False}

    def resolve(info: ConflictInfo) -> str | None:
        if bulk["overwrite"]:
            return "overwrite"
        if bulk["skip"]:
            return "skip"
        if ask_cb is None:
            return "overwrite"
        answer = ask_cb(info)
        if answer in (ASK_OVERWRITE, ASK_SKIP):
            return answer
        if answer == "overwrite_all":
            bulk["overwrite"] = True
            return "overwrite"
        if answer == "skip_all":
            bulk["skip"] = True
            return "skip"
        return None

    total_bytes = sum(m[1] for m in members)
    progress_cb(_progress_state(0, len(members), 0, total_bytes, ""))
    os.makedirs(dest_dir, exist_ok=True)
    done_bytes = 0

    for i, (member, size, kind) in enumerate(members):
        if is_cancelled():
            result.cancelled = True
            return result
        target = os.path.join(dest_dir, *member.split("/"))
        progress_cb(_progress_state(result.done_files, len(members),
                                    done_bytes, total_bytes, member))
        if kind == "unsupported":
            result.errors.append(FileError(member, "тип элемента не поддерживается"))
            continue
        if not _safe_member(member):
            result.errors.append(FileError(member, "опасный путь в архиве (zip-slip)"))
            continue
        try:
            if kind == "dir":
                os.makedirs(target, exist_ok=True)
            elif kind == "link":
                os.makedirs(os.path.dirname(target), exist_ok=True)
                _extract_link(fmt, archive_path, member, target)
                result.done_files += 1
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                if os.path.lexists(target):
                    action = resolve(_conflict_info(archive_path + "::" + member, target))
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
                _extract_file(fmt, archive_path, member, target)
                result.done_files += 1
                done_bytes += size
        except (OSError, zipfile.BadZipFile, tarfile.TarError, KeyError) as exc:
            result.errors.append(FileError(member, str(exc)))
    result.done_bytes = done_bytes
    return result


def unpack_archive(archive_path: str, dest_dir: str,
                   progress_cb: Callable[[Progress], None],
                   is_cancelled: Callable[[], bool],
                   ask_cb=None) -> OpResult:
    """Распаковать весь архив в dest_dir."""
    try:
        raw = read_members(archive_path)
    except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
        return OpResult(errors=[FileError(archive_path, str(exc))])
    members = [(norm_member(n), s, k) for n, s, k in raw if norm_member(n)]
    return _extract_core(archive_format(archive_path), archive_path, dest_dir,
                         members, progress_cb, is_cancelled, ask_cb)


def _extract_file(fmt: str, archive_path: str, member: str, target: str) -> None:
    if fmt == "7z":
        tool = external_tool("7z")
        if tool is None:
            raise OSError("7z (p7zip) не установлен")
        proc = subprocess.run(
            [tool, "e", "-so", "-bd", "--", archive_path, member],
            capture_output=True, timeout=600)
        if proc.returncode != 0:
            raise OSError(proc.stderr.decode(errors="replace").strip()
                          or "7z не смог извлечь элемент")
        with open(target, "wb") as dst:
            dst.write(proc.stdout)
        return
    if fmt == "rar":
        tool = external_tool("rar")
        if tool is None:
            raise OSError("unrar не установлен")
        tmp_dir = tempfile.mkdtemp(prefix=".sc_rar_",
                                   dir=os.path.dirname(target))
        try:
            proc = subprocess.run(
                [tool, "x", "-o+", "-idq", "--", archive_path, member,
                 tmp_dir + "/"],
                capture_output=True, text=True, timeout=600)
            produced = os.path.join(tmp_dir, *member.split("/"))
            if proc.returncode != 0 or not os.path.isfile(produced):
                raise OSError(proc.stderr.strip()
                              or "unrar не смог извлечь элемент")
            os.replace(produced, target)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return
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
        with zipfile.ZipFile(archive_path) as zf:
            link = zf.read(member).decode(errors="replace")
        os.symlink(link, target)
        return
    with tarfile.open(archive_path) as tf:
        m = tf.getmember(member)
        link = m.linkname if m.issym() else os.path.abspath(m.linkname)
        os.symlink(link, target)


# ---------------------------------------------------------------- VFS-браузер

@dataclass(frozen=True)
class MemberInfo:
    name: str  # нормализованное имя: "docs/sub/readme.txt"
    size: int
    mtime: float
    kind: str  # "dir" | "file" | "link"


def _zip_time(date_time: tuple) -> float:
    try:
        return calendar.timegm(tuple(date_time) + (0, 0, 0))
    except (TypeError, ValueError):
        return 0.0


class ArchiveBrowser:
    """Индекс архива в памяти: навигация, выборочное извлечение, правка состава.

    Изменения состава (delete/replace) перезаписывают архив через временный
    файл и os.replace — ошибка посреди операции не портит исходный архив.
    """

    def __init__(self, archive_path: str):
        self.archive_path = os.path.abspath(archive_path)
        self.format = archive_format(self.archive_path)
        if self.format is None or not os.path.isfile(self.archive_path):
            raise ValueError(f"не архив: {archive_path}")
        self.members: list[MemberInfo] = []
        self._dirs: set[str] = set()
        self._read()

    def _read(self) -> None:
        self.members = []
        self._dirs = set()
        raw: list[tuple[str, int, str, float]] = []  # (имя, размер, тип, mtime)
        if self.format in ("7z", "rar"):
            for name, size, kind in read_members(self.archive_path):
                norm = norm_member(name)
                if not norm:
                    continue
                raw.append((norm, size, kind, 0.0))  # mtime у внешних форматов v1 не читается
        elif self.format == "zip":
            with zipfile.ZipFile(self.archive_path) as zf:
                for info in zf.infolist():
                    norm = norm_member(info.filename)
                    if not norm:
                        continue
                    if info.filename.endswith("/"):
                        raw.append((norm, 0, "dir", _zip_time(info.date_time)))
                    elif (info.create_system == 3
                          and stat.S_ISLNK(info.external_attr >> 16)):
                        raw.append((norm, 0, "link", _zip_time(info.date_time)))
                    else:
                        raw.append((norm, info.file_size, "file",
                                    _zip_time(info.date_time)))
        else:
            with tarfile.open(self.archive_path) as tf:
                for m in tf.getmembers():
                    norm = norm_member(m.name)
                    if not norm:
                        continue
                    if m.isdir():
                        raw.append((norm, 0, "dir", float(m.mtime)))
                    elif m.issym() or m.islnk():
                        raw.append((norm, m.size, "link", float(m.mtime)))
                    elif m.isreg():
                        raw.append((norm, m.size, "file", float(m.mtime)))
                    else:
                        raw.append((norm, 0, "unsupported", float(m.mtime)))

        def ensure_parents(name: str) -> None:
            parts = name.split("/")
            for i in range(1, len(parts)):
                parent = "/".join(parts[:i])
                if parent not in self._dirs:
                    self._dirs.add(parent)
                    self.members.append(MemberInfo(parent, 0, 0.0, "dir"))

        for norm, size, kind, mtime in raw:
            if kind == "dir":
                if norm in self._dirs:
                    continue  # уже синтезирован как родитель
                ensure_parents(norm)
                self._dirs.add(norm)
                self.members.append(MemberInfo(norm, 0, 0.0, "dir"))
            else:
                ensure_parents(norm)
                self.members.append(MemberInfo(norm, size, mtime, kind))

    # -- навигация ---------------------------------------------------------

    def list_dir(self, inner: str, show_hidden: bool = False) -> list[FileEntry]:
        """Записи каталога inner ("" — корень); KeyError если каталога нет."""
        inner = norm_member(inner)
        if inner and inner not in self._dirs:
            raise KeyError(f"в архиве нет каталога: {inner}")
        entries: list[FileEntry] = []
        for m in self.members:
            parent = norm_member(m.name.rsplit("/", 1)[0]) if "/" in m.name else ""
            if parent != inner:
                continue
            base = m.name.rsplit("/", 1)[-1]
            if not show_hidden and base.startswith("."):
                continue
            entries.append(FileEntry(
                name=base,
                path=f"{self.archive_path}::{m.name}",
                is_dir=(m.kind == "dir"),
                is_link=(m.kind == "link"),
                size=m.size,
                mtime=m.mtime,
                mode=0o755 if m.kind == "dir" else 0o644,
            ))
        dirs_first = sorted(
            (e for e in entries if e.is_dir), key=lambda e: e.name.lower())
        files = sorted(
            (e for e in entries if not e.is_dir), key=lambda e: e.name.lower())
        return dirs_first + files

    def expand_selected(self, member_names: list[str]) -> list[str]:
        """Выбор + всё содержимое выбранных каталогов."""
        out: list[str] = []
        for name in member_names:
            name = norm_member(name)
            out.append(name)
            prefix = name + "/"
            out.extend(m.name for m in self.members if m.name.startswith(prefix))
        return sorted(set(out))

    # -- извлечение ----------------------------------------------------------

    def extract_members(self, member_names: list[str], dest_dir: str,
                        progress_cb: Callable[[Progress], None],
                        is_cancelled: Callable[[], bool],
                        ask_cb=None) -> OpResult:
        wanted = set(self.expand_selected(member_names))
        members = [(m.name, m.size, m.kind) for m in self.members
                   if m.name in wanted or m.kind == "dir"
                   and any(w == m.name or w.startswith(m.name + "/") for w in wanted)]
        # родители-каталоги выбранных файлов тоже нужны
        for name in list(wanted):
            parts = name.split("/")
            for i in range(1, len(parts)):
                parent = "/".join(parts[:i])
                if not any(m[0] == parent for m in members):
                    members.append((parent, 0, "dir"))
        return _extract_core(self.format, self.archive_path, dest_dir,
                             members, progress_cb, is_cancelled, ask_cb)

    def extract_member_to_temp(self, member: str, temp_dir: str) -> str:
        """Файл архива во временный файл (для просмотра/правки)."""
        member = norm_member(member)
        base = member.rsplit("/", 1)[-1]
        fd, tmp = tempfile.mkstemp(prefix="sc_", suffix="." + base,
                                   dir=temp_dir)
        os.close(fd)
        try:
            _extract_file(self.format, self.archive_path, member, tmp)
        except Exception:
            os.unlink(tmp)
            raise
        return tmp

    # -- изменение состава ---------------------------------------------------

    def delete_members(self, member_names: list[str]) -> OpResult:
        return self._rewrite(skip=set(self.expand_selected(member_names)),
                             replace={})

    def replace_member(self, member: str, file_path: str) -> OpResult:
        member = norm_member(member)
        return self._rewrite(skip=set(), replace={member: file_path})

    def _open_new(self, tmp_path: str):
        if self.format == "zip":
            return zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED)
        mode = {"tar": "w", "tar.gz": "w:gz", "tar.bz2": "w:bz2", "tar.xz": "w:xz"}[
            _tar_mode_of(self.archive_path)]
        return tarfile.open(tmp_path, mode)

    def _rewrite(self, skip: set[str], replace: dict[str, str]) -> OpResult:
        result = OpResult()
        if self.format == "rar":
            return OpResult(errors=[FileError(
                self.archive_path,
                "изменение состава rar не поддерживается — распакуйте и упакуйте заново")])
        if self.format == "7z":
            return _7z_rewrite(self.archive_path, skip, replace, result)
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=".sc_rebuild_", dir=os.path.dirname(self.archive_path))
        os.close(tmp_fd)
        try:
            raw = read_members(self.archive_path)
            with _open_archive(self.archive_path) as src, \
                    self._open_new(tmp_path) as dst:
                for raw_name, _size, kind in raw:
                    name = norm_member(raw_name)
                    if not name:
                        continue
                    if name in skip:
                        result.skipped += 1
                        continue
                    if kind == "dir":
                        self._copy_member_meta(src, dst, name)
                        continue
                    if name in replace:
                        if self.format == "zip":
                            st = os.stat(replace[name])
                            info = zipfile.ZipInfo(name, date_time=_now_zip_time())
                            info.external_attr = (stat.S_IFREG | 0o644) << 16
                            with open(replace[name], "rb") as f:
                                dst.writestr(info, f.read())
                            result.done_bytes += st.st_size
                        else:
                            dst.add(replace[name], arcname=name, recursive=False)
                        result.done_files += 1
                        continue
                    self._copy_member(src, dst, name, kind)
                    result.done_files += 1
            os.replace(tmp_path, self.archive_path)
            self._read()  # переиндексация после замены
        except Exception as exc:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            if isinstance(exc, (OSError, zipfile.BadZipFile, tarfile.TarError)):
                result.errors.append(FileError(self.archive_path, str(exc)))
            else:
                raise
        return result

    def _copy_member_meta(self, src, dst, name: str) -> None:
        if self.format == "zip":
            with zipfile.ZipFile(self.archive_path) as zf:
                raw = _raw_zip_name(zf, name)
                info = zf.getinfo(raw)
                new = zipfile.ZipInfo(raw, date_time=info.date_time)
                new.external_attr = info.external_attr
                new.create_system = info.create_system
                dst.writestr(new, b"")
        else:
            with tarfile.open(self.archive_path) as tf:
                dst.addfile(tf.getmember(name))

    def _copy_member(self, src, dst, name: str, kind: str) -> None:
        if self.format == "zip":
            with zipfile.ZipFile(self.archive_path) as zf:
                raw = _raw_zip_name(zf, name)
                info = zf.getinfo(raw)
                if kind == "link":
                    new = _zip_link_info(name)
                    new.date_time = info.date_time
                    dst.writestr(new, zf.read(raw))
                else:
                    with zf.open(info) as fsrc, dst.open(_clone_zip_info(info, name),
                                                         "w") as fdst:
                        shutil.copyfileobj(fsrc, fdst, 1024 * 1024)
        else:
            with tarfile.open(self.archive_path) as tf:
                tm = tf.getmember(name)
                if tm.isreg():
                    dst.addfile(tm, tf.extractfile(tm))
                else:
                    dst.addfile(tm)

    # для тестов
    def member_names(self) -> list[str]:
        return [m.name for m in self.members]


def _tar_mode_of(archive_path: str) -> str:
    name = os.path.basename(archive_path).lower()
    if name.endswith((".tar.gz", ".tgz")):
        return "tar.gz"
    if name.endswith((".tar.bz2", ".tbz2")):
        return "tar.bz2"
    if name.endswith((".tar.xz", ".txz")):
        return "tar.xz"
    return "tar"


def _open_archive(path: str):
    if archive_format(path) == "zip":
        return zipfile.ZipFile(path)
    return tarfile.open(path)


def _raw_zip_name(zf: zipfile.ZipFile, norm: str) -> str:
    for info in zf.infolist():
        if norm_member(info.filename) == norm:
            return info.filename
    raise KeyError(norm)


def _clone_zip_info(info: zipfile.ZipInfo, new_name: str) -> zipfile.ZipInfo:
    new = zipfile.ZipInfo(new_name, date_time=info.date_time)
    new.compress_type = info.compress_type
    new.external_attr = info.external_attr
    new.create_system = info.create_system
    return new


def _now_zip_time() -> tuple:
    return time.localtime()[:6]
