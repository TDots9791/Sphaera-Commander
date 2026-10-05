"""Раскраска файлов по типу: категория расширения → приглушённый цвет
айдентики Iustitia (бронза — архивы, бирюза — изображения, нейтральные
полутона — видео/аудио; «без крайне ярких цветов»). Палитры для тёмной
и светлой темы. Категории и цвета переопределяются файлом
~/.config/sphaera-commander/colorize.json (без редактора в 0.19.0):
  {"categories": {"code": [".py", ".rs"]}, "colors": {"dark": {"code": "#7f8c99"}}}
Выключается «Вид → Раскраска по типу» (config: view/colorize)."""

from __future__ import annotations

import json
import os

CATEGORY_EXT: dict[str, set[str]] = {
    "archive": {".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".tgz",
                ".tbz2", ".txz", ".zst", ".lzma", ".cpio", ".arj", ".lzh",
                ".deb", ".rpm"},
    "image": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg",
              ".tiff", ".tif", ".ico", ".heic", ".avif", ".xcf", ".psd"},
    "video": {".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv",
              ".m4v", ".mpg", ".mpeg", ".ts"},
    "audio": {".mp3", ".flac", ".ogg", ".opus", ".wav", ".m4a", ".aac",
              ".wma", ".mid"},
}

# Приглушённые тона на базе Iustitia (theme.py): тонированная бронза,
# фирменная бирюза, нейтральные полутона графита — читаемо на обеих темах.
PALETTE: dict[str, dict[str, str]] = {
    "dark": {
        "archive": "#b3895f",
        "image": "#5fbab4",
        "video": "#9aa5aa",
        "audio": "#8b979d",
    },
    "light": {
        "archive": "#8a5f3f",
        "image": "#2f8f8b",
        "video": "#5d6a70",
        "audio": "#4f5d64",
    },
}

OVERRIDES_FILE = os.path.expanduser(
    "~/.config/sphaera-commander/colorize.json")

_cats: dict[str, str] | None = None
_colors: dict[tuple[str, str], object] | None = None
_enabled: bool | None = None


def is_enabled() -> bool:
    """Флаг «Вид → Раскраска по типу»; читается из config один раз."""
    global _enabled
    if _enabled is None:
        from sphaera_commander import config

        _enabled = config.qsettings().value(
            "view/colorize", "true") in (True, "true", "1")
    return _enabled


def set_enabled(on: bool) -> None:
    global _enabled
    _enabled = bool(on)


def _load_overrides() -> tuple[dict, dict]:
    try:
        with open(OVERRIDES_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}, {}
    if not isinstance(data, dict):
        return {}, {}
    cats = data.get("categories") or {}
    colors = data.get("colors") or {}
    return (cats if isinstance(cats, dict) else {},
            colors if isinstance(colors, dict) else {})


def _build() -> None:
    global _cats, _colors
    from PySide6.QtGui import QColor

    cats: dict[str, str] = {}
    for category, exts in CATEGORY_EXT.items():
        for ext in exts:
            cats[ext] = category
    override_cats, override_colors = _load_overrides()
    for category, exts in override_cats.items():
        if not isinstance(exts, (list, tuple)):
            continue
        for ext in exts:
            cats[str(ext).lower()] = str(category)
    colors: dict[tuple[str, str], QColor] = {}
    for scheme, per in PALETTE.items():
        for category, hexcolor in per.items():
            colors[(scheme, category)] = QColor(hexcolor)
    for scheme, per in override_colors.items():
        if not isinstance(per, dict):
            continue
        for category, hexcolor in per.items():
            color = QColor(hexcolor) if isinstance(hexcolor, str) else QColor()
            if color.isValid():
                colors[(str(scheme), str(category))] = color
    _cats, _colors = cats, colors


def reset_cache() -> None:
    """Сбросить кэш после правки colorize.json (без перезапуска)."""
    global _cats, _colors
    _cats = _colors = None


def category_of(name: str) -> str | None:
    if _cats is None:
        _build()
    return _cats.get(os.path.splitext(name)[1].lower())


def brush_for(name: str, dark: bool):
    """QColor|None — цвет имени файла по категории (для ForegroundRole)."""
    category = category_of(name)
    if category is None:
        return None
    if _colors is None:
        _build()
    return _colors.get(("dark" if dark else "light", category))
