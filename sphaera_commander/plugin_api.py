"""API подключаемых модулей Sphaera Commander.

Модуль — файл Python с функцией create(app), возвращающей экземпляр
наследника SphaeraPlugin. Встроенные модули живут в
sphaera_commander/plugins/, модули пользователей — в
~/.config/sphaera-commander/plugins/. Управление включением —
«Инструменты → Плагины…» (применяется после перезапуска).
"""

from __future__ import annotations


class SphaeraPlugin:
    """База модуля. Переопределяйте нужные хуки; неизвестные поля не трогать."""

    id: str = "plugin"        # уникальный идентификатор (латиницей)
    title: str = "Модуль"     # отображаемое имя

    def __init__(self, app):
        self.app = app        # MainWindow

    # -- хуки ---------------------------------------------------------------

    def tools_actions(self):
        """Пункты меню «Инструменты»: [(заголовок, callable, hotkey|None)]."""
        return []

    def context_actions(self, panel, entry):
        """Пункты контекстного меню панели: [(заголовок, callable)]."""
        return []

    def on_loaded(self):
        """Вызывается после старта приложения, когда панели готовы
        (в GUI-потоке) — для встроенной в интерфейс функциональности."""
        return None

    def on_shutdown(self):
        """Вызывается при закрытии приложения (в GUI-потоке)."""
        return None
