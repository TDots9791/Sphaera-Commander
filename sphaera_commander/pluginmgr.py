"""Менеджер подключаемых модулей: поиск, включение, загрузка, доступ.

Встроенные модули — файлы в sphaera_commander/plugins/ (кроме начинающихся
с «_»), пользовательские — в ~/.config/sphaera-commander/plugins/.
Включённость хранится в QSettings «plugins/enabled» (список имён файлов);
при первом запуске все найденные модули включены. Модуль обязателен к
формату: функция create(app) возвращает экземпляр SphaeraPlugin. Ошибка
модуля не роняет приложение — попадает в список ошибок менеджера.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from .config import qsettings
from .plugin_api import SphaeraPlugin

BUILTIN_DIR = os.path.join(os.path.dirname(__file__), "plugins")
USER_DIR = os.path.expanduser("~/.config/sphaera-commander/plugins")
SETTING = "plugins/enabled"


def _scan_dir(directory: str) -> list[str]:
    """Имена *.py без префикса «_», отсортированные."""
    if not os.path.isdir(directory):
        return []
    return sorted(n[:-3] for n in os.listdir(directory)
                  if n.endswith(".py") and not n.startswith("_"))


def available() -> list[dict]:
    """Все найденные модули: [{name, source, path}]."""
    out = []
    seen = set()
    for source, directory in (("builtin", BUILTIN_DIR),
                              ("user", USER_DIR)):
        for name in _scan_dir(directory):
            if name in seen:
                continue  # пользовательский с тем же именем перекрывает
            seen.add(name)
            out.append({"name": name, "source": source,
                        "path": os.path.join(directory, name + ".py")})
    return out


def enabled_names() -> set[str]:
    value = qsettings().value(SETTING, [])
    if isinstance(value, str):
        value = [value]
    return {str(v) for v in (value or [])}


def set_enabled(names: set[str]) -> None:
    qsettings().setValue(SETTING, sorted(names))


def _ensure_defaults() -> None:
    """Первый запуск: включить всё найденное (если настройка ещё пустая)."""
    s = qsettings()
    if s.value(SETTING + "/initialized", False) in (True, "true", "1"):
        return
    set_enabled({m["name"] for m in available()})
    s.setValue(SETTING + "/initialized", True)


def load_all(app) -> tuple[list[SphaeraPlugin], list[tuple[str, str]]]:
    """Загрузить включённые модули: (экземпляры, [(имя, ошибка)])."""
    _ensure_defaults()
    enabled = enabled_names()
    instances: list[SphaeraPlugin] = []
    errors: list[tuple[str, str]] = []
    for meta in available():
        if meta["name"] not in enabled:
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"sc_plugin_{meta['name']}", meta["path"])
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            plugin = module.create(app)
            if not isinstance(plugin, SphaeraPlugin):
                raise TypeError("create() должна вернуть SphaeraPlugin")
            instances.append(plugin)
        except Exception as exc:  # модуль не должен ронять приложение
            errors.append((meta["name"], f"{type(exc).__name__}: {exc}"))
    return instances, errors


def shutdown(plugins: list[SphaeraPlugin]) -> None:
    for plugin in plugins:
        try:
            plugin.on_shutdown()
        except Exception:
            pass
