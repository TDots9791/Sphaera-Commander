"""Модуль «Быстрый поиск» (Total Commander): Ctrl+Alt+буквы — курсор
прыгает на первый объект, чьё имя начинается с накопленного префикса.
Backspace стирает последний знак, Esc/навигация сбрасывает поиск.
Обычный ввод букв работает штатно (keyboardSearch Qt)."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, QTimer

from sphaera_commander.i18n import tr
from sphaera_commander.plugin_api import SphaeraPlugin


class _PrefixFilter(QObject):
    """Фильтр клавиатуры одного представления панели."""

    def __init__(self, view, panel, status):
        super().__init__(view)
        self.view = view
        self.panel = panel
        self.status = status
        self.prefix = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(1600)
        self._timer.timeout.connect(self._reset)
        view.installEventFilter(self)

    def _reset(self) -> None:
        self.prefix = ""
        self.status("")

    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtCore import QEvent

        if event.type() != QEvent.KeyPress:
            return super().eventFilter(obj, event)
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Up, Qt.Key_Down,
                   Qt.Key_PageUp, Qt.Key_PageDown, Qt.Key_Home, Qt.Key_End):
            self._reset()
            return super().eventFilter(obj, event)
        if key == Qt.Key_Escape and self.prefix:
            self._reset()
            event.accept()
            return True
        if key == Qt.Key_Backspace and self.prefix:
            self.prefix = self.prefix[:-1]
            self._jump()
            event.accept()
            return True
        mods = event.modifiers()
        if (mods & Qt.ControlModifier) and (mods & Qt.AltModifier):
            text = event.text()
            if text and text.isalnum() and text.isprintable():
                self.prefix += text.lower()
                self._timer.start()
                self._jump()
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def _jump(self) -> None:
        model = self.panel.model
        prefix = self.prefix
        for i, entry in enumerate(model.entries):
            if entry.name.lower().startswith(prefix):
                self.panel.view.set_current_row(i + 1)
                self.status(tr("Быстрый поиск: {prefix}*").format(prefix=prefix))
                return
        self.status(tr("Быстрый поиск: {prefix}* — нет совпадений").format(
            prefix=prefix))


class Plugin(SphaeraPlugin):
    id = "quicksearch"
    title = "Быстрый поиск"

    def __init__(self, app):
        super().__init__(app)
        self._filters = []

    def on_loaded(self):
        for panel in (self.app.left, self.app.right):
            self._filters.append(
                _PrefixFilter(panel.view, panel, self.app._status))


def create(app):
    return Plugin(app)
