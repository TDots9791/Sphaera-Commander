"""Облачные диски как файловые системы: rclone mount (FUSE).

Remote из конфигурации rclone (yad:, gdrive:, …) монтируется в
~/.local/share/sphaera-commander/cloud-mounts/<имя> и дальше панель
работает с ним как с обычным каталогом (навигация, копирование,
просмотр — штатным движком). Повторное подключение готового монтирования
не пересоздаёт его; при выходе из приложения размонтируется то, что
смонтировано этим сеансом.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time

from .i18n import tr

MOUNT_ROOT = os.path.expanduser(
    "~/.local/share/sphaera-commander/cloud-mounts")
DAEMON_WAIT = 15  # секунд на готовность монтирования

# человекочитаемые имена известных remote
REMOTE_TITLES = {
    "yad": "Яндекс.Диск",
    "gdrive": "Google Диск",
    "dropbox": "Dropbox",
    "onedrive": "OneDrive",
}

# remote, смонтированные этим сеансом приложения (размонтируются на выходе)
_mounted_by_us: set[str] = set()
_lock = threading.Lock()


def rclone_bin() -> str | None:
    return shutil.which("rclone")


def clean_name(remote: str) -> str:
    """'gdrive:' → 'gdrive'."""
    return remote.rstrip(":").strip()


def title(remote: str) -> str:
    """Отображаемое имя: 'yad:' → «Яндекс.Диск»."""
    name = clean_name(remote)
    return REMOTE_TITLES.get(name, name)


def mountpoint(remote: str) -> str:
    return os.path.join(MOUNT_ROOT, clean_name(remote))


def is_mounted(remote: str) -> bool:
    return os.path.ismount(mountpoint(remote))


def is_cloud_path(path: str) -> bool:
    """Путь лежит в облачном монтировании (долгие операции — осторожно)."""
    real = os.path.realpath(path)
    return real.startswith(os.path.realpath(MOUNT_ROOT) + os.sep)


def mount(remote: str) -> str:
    """Смонтировать remote и дождаться готовности; вернуть путь монтирования.

    Бросает RuntimeError, если rclone не найден или монтирование не
    поднялось за DAEMON_WAIT секунд. Ужё смонтированное не пересоздаётся.
    """
    path = mountpoint(remote)
    if is_mounted(remote):
        return path
    rclone = rclone_bin()
    if rclone is None:
        raise RuntimeError(tr("rclone не найден"))
    os.makedirs(path, exist_ok=True)
    proc = subprocess.Popen(
        [rclone, "mount", clean_name(remote) + ":", path,
         "--vfs-cache-mode", "writes",
         "--daemon", "--daemon-wait", str(DAEMON_WAIT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        start_new_session=True)
    _rc = proc.wait()
    err = (proc.stderr.read() if proc.stderr else b"").decode(
        errors="replace").strip()
    if proc.stderr:
        proc.stderr.close()
    if not is_mounted(remote):
        raise RuntimeError(err or tr("монтирование не поднялось"))
    with _lock:
        _mounted_by_us.add(clean_name(remote))
    return path


def mount_async(remote: str, done) -> None:
    """mount() в фоне; done(path_or_None, error_or_None) вызывается из потока."""
    def worker():
        try:
            done(mount(remote), None)
        except Exception as exc:  # RuntimeError/OSError — в статус панели
            done(None, str(exc))

    threading.Thread(target=worker, daemon=True,
                     name="cloudmount").start()


def unmount(remote: str) -> None:
    """Отключить облачный диск (лениво: занятые файлы не держат ресурс)."""
    path = mountpoint(remote)
    if not is_mounted(remote):
        return
    fusermount = shutil.which("fusermount3") or shutil.which("fusermount")
    if fusermount is None:
        raise RuntimeError(tr("fusermount3 не найден"))
    subprocess.run([fusermount, "-uz", path], check=False,
                   capture_output=True, timeout=30)
    with _lock:
        _mounted_by_us.discard(clean_name(remote))


def unmount_ours() -> None:
    """Размонтировать всё, что смонтировано этим сеансом."""
    with _lock:
        names = sorted(_mounted_by_us)
    for name in names:
        try:
            unmount(name)
        except (RuntimeError, subprocess.SubprocessError, OSError):
            pass


def wait_ready(remote: str, timeout_s: float = DAEMON_WAIT) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if is_mounted(remote):
            return True
        time.sleep(0.2)
    return False
