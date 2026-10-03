"""Монтирование/размонтирование дисков из окна через udisksctl
(standart для GNOME/Fedora). Разбор lsblk — чистая функция, тестируется
без реальных устройств.
"""

from __future__ import annotations

import json
import shutil
import subprocess


def available() -> bool:
    return bool(shutil.which("udisksctl")) and bool(shutil.which("lsblk"))


def block_devices() -> list[dict]:
    """Блочные устройства: [{path, name, size, removable, mountpoint, label}].

    Служебные (loop, rom/приводы без носителя, zram) исключаются.
    """
    try:
        proc = subprocess.run(
            ["lsblk", "--json", "-o",
             "NAME,PATH,SIZE,RM,MOUNTPOINT,LABEL,TYPE"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []

    devices: list[dict] = []

    def walk(node: dict) -> None:
        children = node.get("children") or []
        if node.get("type") in ("part", "disk"):
            name = str(node.get("name", ""))
            # диск с таблицей разделов не показываем — цель это его разделы;
            # диск без детей (целиком с ФС) показываем
            if not children and not name.startswith(("loop", "zram", "sr")):
                mountpoint = node.get("mountpoint")
                if isinstance(mountpoint, list):  # несколько точек монтирования
                    mountpoint = mountpoint[0] if mountpoint else None
                devices.append({
                    "path": node.get("path") or "/dev/" + name,
                    "name": name,
                    "size": str(node.get("size") or ""),
                    "removable": bool(node.get("rm")),
                    "mountpoint": mountpoint,
                    "label": node.get("label") or "",
                })
        for child in children:
            walk(child)

    for dev in data.get("blockdevices", []):
        walk(dev)
    return devices


def mount(device_path: str) -> tuple[bool, str]:
    """Примонтировать; (успех, сообщение/точка монтирования)."""
    if not shutil.which("udisksctl"):
        return False, "udisksctl не найден"
    try:
        proc = subprocess.run(["udisksctl", "mount", "-b", device_path],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip() or "ошибка монтирования"
    out = (proc.stdout or "").strip()
    if " at " in out:
        return True, out.rsplit(" at ", 1)[1].rstrip(".")
    return True, out


def unmount(device_path: str) -> tuple[bool, str]:
    if not shutil.which("udisksctl"):
        return False, "udisksctl не найден"
    try:
        proc = subprocess.run(["udisksctl", "unmount", "-b", device_path],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip() or "ошибка размонтирования"
    return True, "размонтировано"
