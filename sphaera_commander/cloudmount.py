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
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time

from .i18n import tr

MOUNT_ROOT = os.path.expanduser(
    "~/.local/share/sphaera-commander/cloud-mounts")
DAEMON_WAIT = 15  # секунд на готовность монтирования
TOKEN_URL = "https://oauth.yandex.ru/token"
FALLBACK_TIMEOUT = 1800  # секунд на один запасной инструмент скачивания

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


# ------------------------------------------------ запасное скачивание
#
# Чтение через FUSE при сбое облака даёт EIO после внутренних ретраев
# rclone (реальный случай Яндекса 10.2026: метаданные живы, а блоб файла
# сервер не отдаёт — «500 file IO failure»). Тогда файл добирается
# запасными инструментами: сначала локальное зеркало пары синхронизации,
# затем прямой rclone copyto из remote в обход монтирования.


def remote_of_path(local_path: str) -> str | None:
    """Путь в монтировании → 'remote:путь'; вне монтирований — None."""
    real = os.path.realpath(local_path)
    root = os.path.realpath(MOUNT_ROOT)
    if not real.startswith(root + os.sep):
        return None
    name, sep, rel = real[len(root) + 1:].partition(os.sep)
    if not name:
        return None
    return f"{name}:{rel}" if sep else f"{name}:"


def _run_cancellable(cmd: list[str], is_cancelled,
                     timeout_s: float = FALLBACK_TIMEOUT) -> tuple[int, str]:
    """Запустить команду с отслеживанием отмены и общим бюджетом времени.

    (код, stderr-хвост): 0 — успех, -1 — отмена, -2 — превышен timeout_s.
    """
    started = time.monotonic()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    while proc.poll() is None:
        if is_cancelled():
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            return -1, ""
        if time.monotonic() - started > timeout_s:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            return -2, ""
        time.sleep(0.2)
    err = ""
    if proc.stderr:
        err = (proc.stderr.read() or "").strip()
        proc.stderr.close()
    return proc.returncode, err


def _tmp_in_dst_dir(dst: str) -> str:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dst) or ".",
                               prefix=".sphaera-fb-")
    os.close(fd)
    return tmp


def _unlink_quiet(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _from_mirror(remote: str, dst: str, expected_size: int) -> tuple[bool, str]:
    """Достать файл из локального зеркала пары синхронизации (со сверкой)."""
    from . import cloudsync

    mirror = cloudsync.mirror_for_remote(remote)
    if not mirror or not os.path.isfile(mirror):
        return False, tr("зеркало синхронизации не найдено")
    meta = cloudsync.remote_meta(remote)
    size = meta["size"] if meta else expected_size
    if size is None or size < 0:
        return False, tr("нечем сверить размер зеркала")
    if os.path.getsize(mirror) != size:
        return False, tr("зеркало устарело (размер не совпал)")
    if meta and meta.get("md5"):
        h = hashlib.md5()
        with open(mirror, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        if h.hexdigest() != meta["md5"]:
            return False, tr("зеркало устарело (контрольная сумма не совпала)")
        note = tr("восстановлено из зеркала синхронизации (сверка md5)")
    else:
        note = tr("восстановлено из зеркала синхронизации (сверка размера)")
    tmp = _tmp_in_dst_dir(dst)
    try:
        shutil.copyfile(mirror, tmp)
        shutil.copystat(mirror, tmp)
        os.replace(tmp, dst)
    except OSError:
        _unlink_quiet(tmp)
        raise
    return True, note


def _direct_copyto(remote: str, dst: str, expected_size: int,
                   is_cancelled) -> tuple[bool, str]:
    """Скачивание в обход монтирования: rclone copyto remote → tmp → замена.

    Тайминги короткие: монтирование уже попыталось прочитать файл с полными
    ретраями; здесь ловим только «облако ожило». Мёртвый файл (Яндекс
    отдаёт 500 мгновенно или молчит подолгу) не должен держать операцию.
    """
    rclone = rclone_bin()
    if rclone is None:
        return False, tr("rclone не найден")
    tmp = _tmp_in_dst_dir(dst)
    cmd = [rclone, "copyto", remote, tmp,
           "--retries", "1", "--low-level-retries", "4",
           "--contimeout", "10s", "--timeout", "20s", "--stats", "0"]
    try:
        rc, err = _run_cancellable(cmd, is_cancelled)
    except OSError as exc:
        _unlink_quiet(tmp)
        return False, str(exc)
    if rc != 0:
        _unlink_quiet(tmp)
        if rc == -1:
            return False, tr("прервано")
        if rc == -2:
            return False, tr("rclone: превышен бюджет времени скачивания")
        tail = err.splitlines()[-1] if err else tr("rclone вернул код {rc}").format(rc=rc)
        return False, tr("rclone: {err}").format(err=tail)
    if expected_size >= 0 and os.path.getsize(tmp) != expected_size:
        got = os.path.getsize(tmp)
        _unlink_quiet(tmp)
        return False, tr("rclone: получено {got} из {expected} байт").format(
            got=got, expected=expected_size)
    os.replace(tmp, dst)
    return True, tr("скачано напрямую через rclone (в обход монтирования)")


def fallback_download(src: str, dst: str, expected_size: int,
                      is_cancelled) -> tuple[bool, str]:
    """Добрать облачный файл запасными инструментами; (успех, пояснение).

    src — путь в монтировании, dst — куда положить готовый файл (атомарно,
    через временный в том же каталоге). Инструменты по порядку: локальное
    зеркало пары синхронизации, затем прямой rclone copyto из remote.
    Пояснение при неудаче объединяет причины обоих.
    """
    remote = remote_of_path(src)
    if remote is None:
        return False, tr("путь вне облачных монтирований")
    try:
        ok, note = _from_mirror(remote, dst, expected_size)
        if ok:
            return True, note
        mirror_note = note
        ok, note = _direct_copyto(remote, dst, expected_size, is_cancelled)
        if ok:
            return True, note
        return False, tr("зеркало: {mirror}; {note}").format(
            mirror=mirror_note, note=note)
    except OSError as exc:
        return False, exc.strerror or str(exc)
