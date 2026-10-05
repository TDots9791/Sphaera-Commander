"""Hotlist — избранные папки (Ctrl+D), как в Total Commander.

Хранение: ~/.config/sphaera-commander/hotlist.json — список
{"title", "path"}. Логика без Qt; меню собирает приложение."""

from __future__ import annotations

import json
import os

HOTLIST_FILE = os.path.expanduser(
    "~/.config/sphaera-commander/hotlist.json")


def load_hotlist() -> list[dict]:
    try:
        with open(HOTLIST_FILE, encoding="utf-8") as f:
            items = json.load(f).get("hotlist", [])
    except (OSError, ValueError):
        return []
    return [i for i in items
            if isinstance(i, dict) and i.get("path")]


def save_hotlist(items: list[dict]) -> None:
    os.makedirs(os.path.dirname(HOTLIST_FILE), exist_ok=True)
    tmp = HOTLIST_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"hotlist": items}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, HOTLIST_FILE)


def add_current(path: str, items: list[dict]) -> list[dict]:
    """Добавить папку без дублей; заголовок — имя каталога."""
    path = os.path.abspath(path)
    if any(os.path.abspath(i["path"]) == path for i in items):
        return items
    items = list(items)
    items.append({"title": os.path.basename(path) or path, "path": path})
    return items
