"""Облачные диски как файловые системы: rclone mount (FUSE).

Remote из конфигурации rclone (yad:, gdrive:, …) монтируется в
~/.local/share/sphaera-commander/cloud-mounts/<имя> и дальше панель
работает с ним как с обычным каталогом (навигация, копирование,
просмотр — штатным движком). Повторное подключение готового монтирования
не пересоздаёт его; при выходе из приложения размонтируется то, что
смонтировано этим сеансом.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import threading
import time

from .i18n import tr

MOUNT_ROOT = os.path.expanduser(
    "~/.local/share/sphaera-commander/cloud-mounts")
DAEMON_WAIT = 15  # секунд на готовность монтирования
TOKEN_URL = "https://oauth.yandex.ru/token"

# человекочитаемые имена известных remote
REMOTE_TITLES = {
    "yad": "Яндекс.Диск",
    "yandex": "Яндекс.Диск",
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


def primary_remotes(remotes: list[str]) -> list[str]:
    """Один remote на диск в интерфейсе: yandex: и yad: — один и тот же
    Яндекс.Диск; при наличии обоих интерфейс работает с yandex:
    (доступ через приложение пользователя, см. YandexKeyDialog)."""
    by_title: dict[str, str] = {}
    order: list[str] = []
    for remote in remotes:
        t = title(remote)
        if t not in by_title:
            by_title[t] = remote
            order.append(t)
    if "Яндекс.Диск" in by_title and "yandex:" in remotes:
        by_title["Яндекс.Диск"] = "yandex:"
    return [by_title[t] for t in order]


def has_client_id(remote: str) -> bool:
    client_id, _secret = credentials_of(remote)
    return bool(client_id)


def credentials_of(remote: str) -> tuple[str, str]:
    """(client_id, client_secret) из конфига remote; пустые — не заданы."""
    rclone = rclone_bin()
    if rclone is None:
        return "", ""
    try:
        proc = subprocess.run([rclone, "config", "dump"],
                              capture_output=True, text=True, timeout=30,
                              check=True)
        data = json.loads(proc.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return "", ""
    section = data.get(clean_name(remote)) or {}
    return section.get("client_id") or "", section.get("client_secret") or ""


def set_client_credentials(remote: str, client_id: str,
                           client_secret: str) -> None:
    """Сохранить client_id/client_secret приложения пользователя в конфиг
    remote (дальнейший OAuth идёт через это приложение)."""
    rclone = rclone_bin()
    if rclone is None:
        raise RuntimeError(tr("rclone не найден"))
    subprocess.run([rclone, "config", "update", clean_name(remote),
                    "client_id", client_id, "client_secret", client_secret],
                   check=True, capture_output=True, timeout=30)


def exchange_code(remote: str, code: str) -> None:
    """Схема «код подтверждения» (verification_code): обменять код,
    показанный на странице Яндекса, на токен и записать его в конфиг
    remote — то же, что делает config reconnect, но без редиректа."""
    client_id, client_secret = credentials_of(remote)
    if not client_id or not client_secret:
        raise RuntimeError(tr("ключи приложения не заданы"))
    import urllib.error
    import urllib.parse
    import urllib.request

    from datetime import datetime, timedelta, timezone

    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    try:
        with urllib.request.urlopen(TOKEN_URL, data=data, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"{exc.code}: {detail}") from exc
    except OSError as exc:
        raise RuntimeError(str(exc)) from exc
    if not payload.get("access_token"):
        raise RuntimeError(tr("Яндекс не вернул access_token"))
    expires = int(payload.get("expires_in", 31536000))
    expiry = (dt.datetime.now(dt.timezone.utc)
              + dt.timedelta(seconds=expires)).isoformat()
    token = json.dumps({
        "access_token": payload["access_token"],
        "token_type": payload.get("token_type", "bearer"),
        "refresh_token": payload.get("refresh_token", ""),
        "expiry": expiry,
    })
    rclone = rclone_bin()
    if rclone is None:
        raise RuntimeError(tr("rclone не найден"))
    subprocess.run([rclone, "config", "update", clean_name(remote),
                    "token", token],
                   check=True, capture_output=True, timeout=30)


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
    try:
        # rclone mount поднимается и при мёртвом облаке (Яндекс 10.2026:
        # 410 на любые resources при живом about) — первый листинг это
        # вскрывает; тогда размонтируем и честно сообщаем
        next(os.scandir(path), None)
    except OSError as exc:
        unmount(remote)
        raise RuntimeError(
            tr("облако смонтировано, но не читается: {exc} — проверьте доступ (rclone lsd {name}:)").format(
                exc=exc, name=clean_name(remote))) from exc
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
