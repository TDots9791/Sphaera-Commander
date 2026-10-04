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
import threading

YAD_CONFIG = os.path.expanduser("~/.config/yad-sync/pairs.json")
SPHAERA_PAIRS_FILE = os.path.expanduser(
    "~/.config/sphaera-commander/cloud-sync.json")
LISTING_ROOT = os.path.expanduser(
    "~/.local/share/sphaera-commander/cloudsync")

DEFAULT_EXCLUDES = ["*.tmp", "*.swp", ".DS_Store", "Thumbs.db", "desktop.ini"]


def rclone_bin() -> str | None:
    return shutil.which("rclone")


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


def connect_remote(remote_name: str) -> list[str]:
    """Команды первичного подключения remote (gdrive:): create + reconnect
    (второй открывает браузер OAuth)."""
    rclone = rclone_bin() or "rclone"
    name = remote_name.rstrip(":")
    return [rclone, "config", "create", name, "drive",
            "config_refresh_token", "true"], \
        [rclone, "config", "reconnect", f"{name}:"]


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
