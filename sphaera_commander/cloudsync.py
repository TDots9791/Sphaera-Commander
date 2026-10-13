"""Синхронизация с облаками через rclone (Яндекс.Диск, Google Drive и любые
другие remote) и интеграция с установленным yad-sync (проект Ya.D).

Пары пользователя Ya.D (~/.config/yad-sync/pairs.json) исполняются его же
CLI `yad-sync sync <id>` — состояние bisync (листинги, .lck) принадлежит
ya.d, Sphaera его не дублирует. Собственные пары Sphaera хранятся в
~/.config/sphaera-commander/cloud-sync.json и идут через `rclone bisync
--resilient` с листингами в ~/.local/share/sphaera-commander/cloudsync/.

Первичная синхронизация пары Sphaera — `bisync --resync --resync-mode
newer`: обе стороны объединяются, при конфликте версий побеждает более
новый файл (политика как в Ya.D).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading

YAD_CONFIG = os.path.expanduser("~/.config/yad-sync/pairs.json")
SPHAERA_PAIRS_FILE = os.path.expanduser(
    "~/.config/sphaera-commander/cloud-sync.json")
LISTING_ROOT = os.path.expanduser(
    "~/.local/share/sphaera-commander/cloudsync")

DEFAULT_EXCLUDES = ["*.tmp", "*.swp", ".DS_Store", "Thumbs.db", "desktop.ini"]


def rclone_bin() -> str | None:
    """Путь до rclone: PATH, exe-каталог приложения, типовые места (Windows)."""
    exe = shutil.which("rclone")
    if exe:
        return exe
    local = shutil.which(os.path.expanduser("~/.local/bin/rclone"))
    if local:
        return local
    # rclone, вшитый в дистрибутив (лежит рядом с исполняемым файлом)
    bundled = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                           "rclone.exe" if os.name == "nt" else "rclone")
    if os.path.isfile(bundled):
        return bundled
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "")
        for cand in (
            os.path.join(base, "Microsoft", "WinGet", "Links", "rclone.exe"),
            os.path.join(base, "Programs", "rclone", "rclone.exe"),
            os.path.expanduser(os.path.join("~", "scoop", "apps", "rclone",
                                            "current", "rclone.exe")),
            r"C:\Program Files\rclone\rclone.exe",
            r"C:\Program Files (x86)\rclone\rclone.exe",
        ):
            if cand and os.path.isfile(cand):
                return cand
    return None


def yad_bin() -> str | None:
    return shutil.which("yad-sync") or shutil.which(
        os.path.expanduser("~/.local/bin/yad-sync"))


def rclone_remotes() -> list[str]:
    """Имена remote из конфигурации rclone: ['yad:', 'gdrive:']."""
    rclone = rclone_bin()
    if rclone is None:
        return []
    try:
        proc = subprocess.run([rclone, "listremotes"], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line.strip() for line in proc.stdout.splitlines()
            if line.strip()]


def yad_pairs() -> list[dict]:
    """Пары Ya.D: [{id, local, remote, enabled}]; remote — путь на Диске."""
    try:
        with open(YAD_CONFIG, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    pairs = []
    for p in data.get("pairs", []):
        pairs.append({
            "id": p.get("id", ""),
            "local": p.get("local", ""),
            "remote": p.get("remote", ""),
            "enabled": bool(p.get("enabled", True)),
        })
    return pairs


def load_pairs() -> list[dict]:
    """Собственные пары Sphaera: [{id, local, remote, enabled}];
    remote — полный rclone-путь ('gdrive:backup/docs')."""
    try:
        with open(SPHAERA_PAIRS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [p for p in data.get("pairs", []) if p.get("id")]


def save_pairs(pairs: list[dict]) -> None:
    os.makedirs(os.path.dirname(SPHAERA_PAIRS_FILE), exist_ok=True)
    tmp = SPHAERA_PAIRS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"pairs": pairs}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SPHAERA_PAIRS_FILE)


def add_pair(local: str, remote: str) -> dict:
    pairs = [p for p in load_pairs() if p["local"] != local]
    pair = {"id": _pair_id(local, remote), "local": local,
            "remote": remote, "enabled": True}
    pairs.append(pair)
    save_pairs(pairs)
    return pair


def remove_pair(pair_id: str) -> None:
    save_pairs([p for p in load_pairs() if p["id"] != pair_id])


def _pair_id(local: str, remote: str) -> str:
    h = hashlib.sha1(f"{local}|{remote}".encode()).hexdigest()
    return h[:10]


def listing_dir(pair_id: str) -> str:
    return os.path.join(LISTING_ROOT, pair_id)


def is_first_sync(pair_id: str) -> bool:
    """bisync-листингов ещё нет — нужен первичный прогон (--resync)."""
    d = listing_dir(pair_id)
    if not os.path.isdir(d):
        return True
    for _root, _dirs, files in os.walk(d):
        if any(n.endswith(".lst") for n in files):
            return False
    return True


def pair_for_dir(path: str) -> dict | None:
    """Пара (ya.d или собственная), в которую входит каталог; None — нет."""
    real = os.path.realpath(path)
    best = None
    for source in ("yad", "sphaera"):
        pairs = yad_pairs() if source == "yad" else load_pairs()
        for p in pairs:
            p_local = p["local"] if source == "yad" else p["local"]
            p_real = os.path.realpath(os.path.expanduser(p_local))
            if real == p_real or real.startswith(p_real + os.sep):
                if best is None or len(p_real) > len(best["local"]):
                    best = dict(p, source=source)
    return best


def sync_command(pair: dict) -> list[str]:
    """Команда синхронизации пары (для запуска в фоне).

    yad — `yad-sync sync <id>`; собственная пара: первичная —
    `copy --update` обе стороны + `bisync --resync`, повторная —
    `bisync --resilient`.
    """
    if pair.get("source") == "yad":
        return [yad_bin() or "yad-sync", "sync", pair["id"]]
    rclone = rclone_bin() or "rclone"
    remote = pair["remote"] if ":" in pair["remote"] else f"yad:{pair['remote']}"
    local = os.path.expanduser(pair["local"])
    listing = listing_dir(pair["id"])
    base = [rclone, "--retries=2"]
    for excl in DEFAULT_EXCLUDES:
        base += ["--exclude", excl]
    if is_first_sync(pair["id"]):
        return base + [
            "bisync", local, remote,
            "--resync", "--resync-mode", "newer", "--resilient",
            "--workdir", listing,
            "-v",
        ]
    return base + [
        "bisync", local, remote,
        "--resilient",
        "--workdir", listing,
        "-v",
    ]


def run_sync(pair: dict, on_line=None, done=None,
             _proc_holder: dict | None = None) -> None:
    """Запустить sync_command в фоне; on_line(str) — вывод, done(rc) — финал.

    Функция сама создаёт поток и возвращается немедленно. Отмена —
    через объект процесса в _proc_holder["proc"].terminate().
    """
    holder = _proc_holder if _proc_holder is not None else {}

    def worker():
        os.makedirs(listing_dir(pair["id"]), exist_ok=True)
        try:
            proc = subprocess.Popen(
                sync_command(pair),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", start_new_session=True)
        except OSError as exc:
            if on_line:
                on_line(str(exc))
            if done:
                done(-1)
            return
        holder["proc"] = proc
        for line in proc.stdout:  # type: ignore[union-attr]
            if on_line:
                on_line(line.rstrip("\n"))
        rc = proc.wait()
        if done:
            done(rc)

    threading.Thread(target=worker, daemon=True,
                     name="cloudsync").start()


def cancel_sync(holder: dict) -> None:
    proc = holder.get("proc")
    if proc is not None and proc.poll() is None:
        proc.terminate()


def connect_remote(remote_name: str, backend: str = "drive") -> list[str]:
    """Команды первичного подключения remote: create + reconnect
    (второй открывает браузер OAuth). backend — тип rclone: «drive»
    (Google), «yandex» (Яндекс.Диск)."""
    rclone = rclone_bin() or "rclone"
    name = remote_name.rstrip(":")
    return [rclone, "config", "create", name, backend,
            "config_refresh_token", "true"], \
        [rclone, "config", "reconnect", f"{name}:"]


AUTHORIZE_URL = "https://oauth.yandex.ru/authorize"


def authorize_url(client_id: str) -> str:
    """Страница входа для схемы «код подтверждения» (verification_code):
    Яндекс новые приложения запирает на редирект oauth.yandex.ru, код
    пользователь копирует вручную."""
    from urllib.parse import quote

    return (f"{AUTHORIZE_URL}?response_type=code"
            f"&client_id={quote(client_id)}")


def remote_meta(remote_path: str) -> dict | None:
    """Размер и md5 файла на remote ('yandex:путь') через rclone lsjson.

    None — rclone недоступен или сервер не ответил; md5 может отсутствовать,
    если бэкенд его не считает (тогда ключа 'md5' в ответе не будет).
    """
    rclone = rclone_bin()
    if rclone is None:
        return None
    try:
        proc = subprocess.run(
            [rclone, "lsjson", "--stat", "--hash", remote_path],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    hashes = data.get("Hashes") or {}
    meta = {"size": data.get("Size", -1)}
    md5 = hashes.get("MD5")
    if md5:
        meta["md5"] = md5.lower()
    return meta


# бэкенды, для которых подходят пары ya.d (remote без префикса — путь на
# Диске того же аккаунта, что и remote 'yad:'/'yandex:'); 'yad' и 'yandex'
# — разные rclone-remote одного и того же диска, зеркало у них общее
_YAD_BACKENDS = {"yad", "yandex"}


def _same_backend(a: str, b: str) -> bool:
    return a == b or (a in _YAD_BACKENDS and b in _YAD_BACKENDS)


def mirror_for_remote(remote_path: str) -> str | None:
    """Локальный кандидат из пар синхронизации для облачного пути.

    remote_path — 'backend:путь' (yandex:Папка-пример/...). Ищем пару
    (ya.d или собственную), чей удалённый корень — префикс пути; возвращаем
    самый длинный префикс. Файл может не существовать или быть устаревшим —
    проверка на вызывающей стороне (размер/md5). None — пары нет.
    """
    backend, _sep, rel = remote_path.partition(":")
    rel = rel.lstrip("/")
    best: tuple[int, str] | None = None
    sources = [("sphaera", load_pairs())]
    if backend in _YAD_BACKENDS:
        sources.append(("yad", yad_pairs()))
    for source, pairs in sources:
        for p in pairs:
            if source == "sphaera":
                p_backend, _s, p_rel = p["remote"].partition(":")
                if not _s:
                    p_backend, p_rel = "yad", p["remote"]
            else:
                p_backend, p_rel = "yad", p["remote"]
            p_rel = p_rel.strip("/")
            if not _same_backend(p_backend, backend):
                continue
            if p_rel and not rel.startswith(p_rel + "/") and rel != p_rel:
                continue
            rest = rel[len(p_rel):].lstrip("/") if p_rel else rel
            candidate = os.path.join(os.path.expanduser(p["local"]),
                                     *rest.split("/")) if rest else \
                os.path.expanduser(p["local"])
            if best is None or len(p_rel) > best[0]:
                best = (len(p_rel), candidate)
    return best[1] if best else None


def remote_dirs(remote_path: str) -> list[str]:
    """Подкаталоги на remote ('gdrive:' или 'gdrive:docs') через rclone lsd."""
    rclone = rclone_bin()
    if rclone is None:
        return []
    try:
        proc = subprocess.run([rclone, "lsd", remote_path],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    dirs = []
    for line in proc.stdout.splitlines():
        parts = line.split(maxsplit=4)
        if len(parts) == 5 and parts[2] == "-1" and parts[3] == "dir":
            dirs.append(parts[4].strip().rstrip("/"))
    return dirs
