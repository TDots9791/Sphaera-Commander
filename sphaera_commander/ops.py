"""Файловые операции: планирование (scan) и исполнение (copy/move/delete).

Ядро не зависит от Qt — только колбэки, что позволяет тестировать его без GUI.
Каждая операция исполнима в рабочем потоке: прогресс отдаётся через progress_cb,
отмена — через is_cancelled().
"""

from __future__ import annotations

from .i18n import tr

import os
import shutil
import stat
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable

#QtCore — только для тонкой потоковой обёртки в конце модуля
from PySide6.QtCore import QObject, Signal

CHUNK = 1024 * 1024
PROGRESS_EVERY_BYTES = 4 * CHUNK

POLICY_OVERWRITE = "overwrite"
POLICY_SKIP = "skip"
POLICY_CANCEL = "cancel"

# ответы на запрос перезаписи (ask-callback исполняется в рабочем потоке)
ASK_OVERWRITE = "overwrite"
ASK_SKIP = "skip"
ASK_CANCEL = "cancel"
ASK_OVERWRITE_ALL = "overwrite_all"
ASK_SKIP_ALL = "skip_all"

KIND_COPY = "copy"
KIND_MOVE = "move"
KIND_DELETE = "delete"


@dataclass(frozen=True)
class FileError:
    path: str
    message: str


@dataclass(frozen=True)
class Job:
    src: str
    dst: str
    size: int
    is_dir: bool
    is_link: bool = False
    via_rename: bool = False  # перенос переименованием на том же устройстве


@dataclass
class Plan:
    jobs: list[Job] = field(default_factory=list)
    total_files: int = 0
    total_bytes: int = 0
    conflicts: list[tuple[str, str]] = field(default_factory=list)  # (src, dst) для файлов


@dataclass
class Progress:
    phase: str  # "scan" | "run"
    current: str
    done_files: int
    total_files: int
    done_bytes: int
    total_bytes: int


@dataclass
class OpResult:
    done_files: int = 0
    done_bytes: int = 0
    skipped: int = 0
    errors: list[FileError] = field(default_factory=list)
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors and not self.cancelled


@dataclass(frozen=True)
class ConflictInfo:
    """Сведения о конфликте перезаписи для диалога в стиле TC."""
    src: str
    dst: str
    src_size: int
    src_mtime: float
    dst_size: int
    dst_mtime: float
    dst_is_dir: bool


def _conflict_info(src: str, dst: str) -> ConflictInfo:
    def _st(p: str) -> tuple[int, float]:
        try:
            st = os.lstat(p)
            return st.st_size, st.st_mtime
        except OSError:
            return 0, 0.0

    s_size, s_mtime = _st(src)
    d_size, d_mtime = _st(dst)
    return ConflictInfo(src=src, dst=dst, src_size=s_size, src_mtime=s_mtime,
                        dst_size=d_size, dst_mtime=d_mtime,
                        dst_is_dir=os.path.isdir(dst) and not os.path.islink(dst))


# ---------------------------------------------------------------- планирование


def _walk_tree(root: str) -> tuple[list[Job], int, int]:
    """Обход каталога: задания на создание каталогов, симлинки и файлы."""
    jobs: list[Job] = []
    files = 0
    total_bytes = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        rel = os.path.relpath(dirpath, root)
        dst_dir = rel if rel != "." else ""
        jobs.append(Job(src=dirpath, dst=dst_dir, size=0, is_dir=True))
        # симлинки на каталоги не обходятся walk'ом — переносим их как ссылки
        linked_dirs = [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]
        for name in linked_dirs:
            jobs.append(Job(src=os.path.join(dirpath, name),
                            dst=os.path.join(dst_dir, name) if dst_dir else name,
                            size=0, is_dir=False, is_link=True))
        for name in filenames:
            src = os.path.join(dirpath, name)
            try:
                st = os.lstat(src)
            except OSError as exc:
                raise OSError(f"{src}: {exc.strerror or exc}") from exc
            if stat.S_ISLNK(st.st_mode):
                jobs.append(Job(src=src, dst=os.path.join(dst_dir, name) if dst_dir else name,
                                size=0, is_dir=False, is_link=True))
                continue
            jobs.append(Job(src=src, dst=os.path.join(dst_dir, name) if dst_dir else name,
                            size=st.st_size, is_dir=False))
            files += 1
            total_bytes += st.st_size
    return jobs, files, total_bytes


def plan_copy_move(sources: list, dest_dir: str, move: bool) -> Plan:
    """Построить план копирования/переноса выбранных объектов в dest_dir."""
    plan = Plan()
    dest_dev = os.stat(dest_dir).st_dev
    for entry in sources:
        if entry.name == ".." or entry.path == "":
            continue
        dst = os.path.join(dest_dir, entry.name)
        if move:
            try:
                same_fs = os.stat(os.path.dirname(entry.path) or "/").st_dev == dest_dev
            except OSError:
                same_fs = False
            dst_is_real_dir = os.path.isdir(dst) and not os.path.islink(dst)
            merge_case = dst_is_real_dir and entry.is_dir
            if same_fs and not merge_case:
                plan.jobs.append(Job(src=entry.path, dst=dst, size=entry.size,
                                     is_dir=entry.is_dir, via_rename=True))
                if not entry.is_dir:
                    plan.total_files += 1
                    plan.total_bytes += entry.size
                if os.path.lexists(dst):
                    plan.conflicts.append((entry.path, dst))
                continue
        if entry.is_link:
            plan.jobs.append(Job(src=entry.path, dst=dst, size=0,
                                 is_dir=False, is_link=True))
            if os.path.lexists(dst):
                plan.conflicts.append((entry.path, dst))
            continue
        if entry.is_dir and not entry.is_link:
            jobs, files, total_bytes = _walk_tree(entry.path)
            plan.jobs.append(Job(src=entry.path, dst=dst, size=0, is_dir=True))
            for j in jobs:
                if j.is_dir:
                    rel = j.dst if j.dst else ""
                    plan.jobs.append(Job(src=j.src,
                                         dst=os.path.join(dst, rel) if rel else dst,
                                         size=0, is_dir=True))
                else:
                    dst_file = os.path.join(dst, j.dst) if j.dst else dst
                    plan.jobs.append(Job(src=j.src, dst=dst_file, size=j.size, is_dir=False))
                    plan.total_files += 1
                    plan.total_bytes += j.size
        else:
            try:
                size = os.lstat(entry.path).st_size
            except OSError:
                size = 0
            plan.jobs.append(Job(src=entry.path, dst=dst, size=size, is_dir=False))
            plan.total_files += 1
            plan.total_bytes += size
        if os.path.lexists(dst) and not os.path.isdir(dst):
            plan.conflicts.append((entry.path, dst))
    return plan


# ---------------------------------------------------------------- исполнение


def _rmtree_job(path: str) -> tuple[int, int]:
    """Подсчёт файлов и байт в дереве для прогресса удаления."""
    files = 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path, followlinks=False):
        for name in filenames:
            try:
                st = os.lstat(os.path.join(dirpath, name))
            except OSError:
                continue
            files += 1
            total += st.st_size
    return files, total


def _copy_file(src: str, dst: str, progress, done_bytes_holder: list, is_cancelled) -> None:
    copied = 0
    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        while True:
            if is_cancelled():
                raise InterruptedError
            chunk = fsrc.read(CHUNK)
            if not chunk:
                break
            fdst.write(chunk)
            copied += len(chunk)
            done_bytes_holder[0] += len(chunk)
            if done_bytes_holder[0] - progress.last_emit >= PROGRESS_EVERY_BYTES:
                progress.last_emit = done_bytes_holder[0]
                progress.cb(progress.state)
    shutil.copymode(src, dst)
    shutil.copystat(src, dst)
    progress.state.done_bytes = done_bytes_holder[0]
    progress.state.current = src
    progress.cb(progress.state)


class _ProgressHelper:
    __slots__ = ("state", "cb", "last_emit")

    def __init__(self, state: Progress, cb):
        self.state = state
        self.cb = cb
        self.last_emit = 0


def execute_copy_move(plan: Plan, move: bool, policy: str,
                      progress_cb: Callable[[Progress], None],
                      is_cancelled: Callable[[], bool],
                      ask_cb: Callable[[ConflictInfo], str] | None = None) -> OpResult:
    result = OpResult()
    if policy == POLICY_CANCEL and ask_cb is None:
        result.cancelled = True
        return result
    skip_set = ({dst for _src, dst in plan.conflicts}
                if (policy == POLICY_SKIP and ask_cb is None) else set())
    bulk = {"overwrite": False, "skip": False}

    def resolve(job: Job) -> str | None:
        """'overwrite' | 'skip' | None — None означает отмену операции."""
        if bulk["overwrite"]:
            return "overwrite"
        if bulk["skip"]:
            return "skip"
        if ask_cb is None:
            return "overwrite" if policy == POLICY_OVERWRITE else "skip"
        answer = ask_cb(_conflict_info(job.src, job.dst))
        if answer == ASK_OVERWRITE_ALL:
            bulk["overwrite"] = True
            return "overwrite"
        if answer == ASK_SKIP_ALL:
            bulk["skip"] = True
            return "skip"
        if answer in (ASK_OVERWRITE, ASK_SKIP):
            return answer
        return None

    done_bytes = 0
    state = Progress(phase="run", current="", done_files=0,
                     total_files=plan.total_files, done_bytes=0,
                     total_bytes=plan.total_bytes)
    helper = _ProgressHelper(state, progress_cb)

    progress_cb(state)  # начальное состояние
    for job in plan.jobs:
        if is_cancelled():
            result.cancelled = True
            return result
        state.current = job.dst
        state.done_bytes = done_bytes
        progress_cb(state)
        try:
            if job.via_rename:
                if os.path.lexists(job.dst):
                    action = resolve(job)
                    if action is None:
                        result.cancelled = True
                        return result
                    if action == "skip":
                        result.skipped += 1
                        continue
                    if os.path.isdir(job.dst) and not os.path.islink(job.dst):
                        result.errors.append(FileError(
                            job.dst, tr("в назначении каталог с тем же именем")))
                        continue
                    os.unlink(job.dst)
                os.rename(job.src, job.dst)
                result.done_files += 1
                result.done_bytes += job.size
                done_bytes += job.size
                state.done_files = result.done_files
                state.done_bytes = done_bytes
                progress_cb(state)
                continue
            if job.is_dir:
                os.makedirs(job.dst, exist_ok=True)
                continue
            if job.dst in skip_set:
                result.skipped += 1
                continue
            if os.path.lexists(job.dst):
                action = resolve(job)
                if action is None:
                    result.cancelled = True
                    return result
                if action == "skip":
                    result.skipped += 1
                    state.done_files = result.done_files
                    progress_cb(state)
                    continue
                if job.is_link:
                    os.unlink(job.dst)
                    os.symlink(os.readlink(job.src), job.dst)
                    result.done_files += 1
                    state.done_files = result.done_files
                    progress_cb(state)
                    continue
                if os.path.isdir(job.dst) and not os.path.islink(job.dst):
                    result.errors.append(FileError(
                        job.dst, tr("в назначении каталог с тем же именем")))
                    continue
                os.unlink(job.dst)
            elif job.is_link:
                os.symlink(os.readlink(job.src), job.dst)
                result.done_files += 1
                state.done_files = result.done_files
                progress_cb(state)
                continue
            if job.is_link:
                continue
            _copy_file(job.src, job.dst, helper, [done_bytes], is_cancelled)
            done_bytes = helper.state.done_bytes
            if is_cancelled():
                result.cancelled = True
                return result
            if move:
                os.unlink(job.src)
            result.done_files += 1
            state.done_files = result.done_files
        except InterruptedError:
            result.cancelled = True
            return result
        except OSError as exc:
            result.errors.append(FileError(job.dst if not job.via_rename else job.src,
                                           exc.strerror or str(exc)))
    return result


def execute_delete(sources: list, progress_cb: Callable[[Progress], None],
                   is_cancelled: Callable[[], bool]) -> OpResult:
    result = OpResult()
    # фаза подсчёта
    total_files = 0
    total_bytes = 0
    trees: list[tuple[str, int, int]] = []
    for entry in sources:
        if entry.name == ".." or entry.path == "":
            continue
        if entry.is_dir and not entry.is_link:
            files, total = _rmtree_job(entry.path)
            files += 1  # сам каталог
            trees.append((entry.path, files, total))
        else:
            trees.append((entry.path, 1, entry.size))
        total_files += trees[-1][1]
        total_bytes += trees[-1][2]
    state = Progress(phase="run", current="", done_files=0, total_files=total_files,
                     done_bytes=0, total_bytes=total_bytes)
    progress_cb(state)
    for path, _files, _bytes in trees:
        if is_cancelled():
            result.cancelled = True
            return result
        if os.path.islink(path) or not os.path.isdir(path):
            state.current = path
            progress_cb(state)
            try:
                st = os.lstat(path)
                os.unlink(path)
                result.done_files += 1
                result.done_bytes += st.st_size
            except FileNotFoundError:
                pass
            except OSError as exc:
                result.errors.append(FileError(path, exc.strerror or str(exc)))
            state.done_files = result.done_files
            progress_cb(state)
            continue
        # удаление дерева: снизу вверх
        all_dirs: list[str] = []
        for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
            dirnames.sort()
            all_dirs.append(dirpath)
            for name in filenames:
                if is_cancelled():
                    result.cancelled = True
                    return result
                full = os.path.join(dirpath, name)
                state.current = full
                try:
                    st = os.lstat(full)
                    os.unlink(full)
                    result.done_files += 1
                    result.done_bytes += st.st_size
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    result.errors.append(FileError(full, exc.strerror or str(exc)))
                state.done_files = result.done_files
                state.done_bytes = result.done_bytes
                progress_cb(state)
            # симлинки на каталоги: os.walk кладёт их в dirnames, но не спускается
            for name in dirnames:
                full = os.path.join(dirpath, name)
                if os.path.islink(full):
                    try:
                        os.unlink(full)
                    except OSError as exc:
                        result.errors.append(FileError(full, exc.strerror or str(exc)))
        for dirpath in sorted(all_dirs, key=len, reverse=True):
            if is_cancelled():
                result.cancelled = True
                return result
            try:
                os.rmdir(dirpath)
                result.done_files += 1
            except OSError as exc:
                result.errors.append(FileError(dirpath, exc.strerror or str(exc)))
        state.done_files = result.done_files
        progress_cb(state)
    return result


def execute(kind: str, plan: Plan | None, sources: list, policy: str,
            progress_cb: Callable[[Progress], None],
            is_cancelled: Callable[[], bool],
            ask_cb: Callable[[ConflictInfo], str] | None = None) -> OpResult:
    if kind == KIND_DELETE:
        return execute_delete(sources, progress_cb, is_cancelled)
    assert plan is not None
    return execute_copy_move(plan, kind == KIND_MOVE, policy, progress_cb,
                             is_cancelled, ask_cb=ask_cb)


def execute_trash(sources: list, progress_cb: Callable[[Progress], None],
                  is_cancelled: Callable[[], bool]) -> OpResult:
    """Переместить объекты в корзину (XDG) через gio trash; только реальные пути."""
    result = OpResult()
    gio = shutil.which("gio")
    if gio is None:
        result.errors.append(FileError("", tr("утилита gio не найдена — корзина недоступна")))
        return result
    items = [e for e in sources
             if e.name != ".." and e.path and "::" not in e.path]
    state = Progress(phase="run", current="", done_files=0,
                     total_files=len(items), done_bytes=0, total_bytes=0)
    progress_cb(state)
    for entry in items:
        if is_cancelled():
            result.cancelled = True
            return result
        state.current = entry.path
        progress_cb(state)
        proc = subprocess.run([gio, "trash", "--", entry.path],
                              capture_output=True, text=True)
        if proc.returncode == 0:
            result.done_files += 1
        else:
            message = (proc.stderr or "").strip() or tr("не удалось переместить в корзину")
            result.errors.append(FileError(entry.path, message))
        state.done_files = result.done_files
        progress_cb(state)
    return result


# ---------------------------------------------------------------- поток Qt

class OpWorker(QObject):
    """Исполняет одну операцию в рабочем потоке; отмена — через cancel().

    spec["fn"](progress_cb, is_cancelled) -> OpResult — единственная точка работы.
    """

    progressChanged = Signal(object)
    finished = Signal(object)

    def __init__(self, spec: dict):
        super().__init__()
        self._spec = spec
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):  # вызывается в потоке
        try:
            result = self._spec["fn"](self.progressChanged.emit, self._cancel.is_set)
        except Exception as exc:  # защитная сетка: поток не должен падать молча
            result = OpResult(errors=[FileError("", tr("внутренняя ошибка: {exc}").format(exc=exc))])
        self.finished.emit(result)


def run_in_thread(fn: Callable):
    """Запустить fn(progress_cb, is_cancelled) -> OpResult в QThread.

    Сигналы воркера соединяет вызывающий (в главном потоке).
    """
    from PySide6.QtCore import QThread

    worker = OpWorker({"fn": fn})
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    return thread, worker
