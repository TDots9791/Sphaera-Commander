"""Модуль «Вкладки папок»: QTabBar над каждой панелью, как в Total
Commander. Навигация по панели живёт внутри текущей вкладки; Ctrl+T —
новая, Ctrl+W — закрыть, двойной клик по пустому месту — новая вкладка,
средний клик — закрыть. Вкладки переживают перезапуск: сохраняются при
выходе приложения, восстанавливаются при старте."""

from __future__ import annotations

import json
import os

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QTabBar

from sphaera_commander.i18n import tr
from sphaera_commander.plugin_api import SphaeraPlugin

TABS_KEY = "panels/foldertabs"


def _display(path: str) -> str:
    base = os.path.basename(path.rstrip("/"))
    return base or path or "/"


class _PanelTabs:
    """Вкладки одной панели: синхронизация QTabBar ↔ панель."""

    def __init__(self, panel, status):
        self.panel = panel
        self.status = status
        self.paths: list[str] = [panel.current_path()]
        self.syncing = False
        self.bar = QTabBar()
        self.bar.setTabsClosable(True)
        self.bar.setMovable(True)
        self.bar.setExpanding(False)
        self.bar.setUsesScrollButtons(True)
        self.bar.addTab(_display(self.paths[0]))
        self.bar.setCurrentIndex(0)
        panel.layout().insertWidget(1, self.bar)
        self.bar.currentChanged.connect(self._on_current_changed)
        self.bar.tabCloseRequested.connect(self._on_close)
        self.bar.tabMoved.connect(self._on_moved)
        self.bar.tabBarDoubleClicked.connect(self._on_double)
        panel.path_changed.connect(self._on_path_changed)

    # -- операции ------------------------------------------------------------

    def new_tab(self) -> None:
        path = self.panel.current_path()
        self.syncing = True
        index = self.bar.currentIndex() + 1
        self.paths.insert(index, path)
        self.bar.insertTab(index, _display(path))
        self.bar.setCurrentIndex(index)
        self.syncing = False
        self.status(tr("Новая вкладка"))

    def close_tab(self, index: int | None = None) -> None:
        if self.bar.count() <= 1:
            self.status(tr("Последнюю вкладку закрыть нельзя"))
            return
        if index is None:
            index = self.bar.currentIndex()
        self.syncing = True
        was_current = index == self.bar.currentIndex()
        self.paths.pop(index)
        self.bar.removeTab(index)
        if was_current:
            target = min(index, self.bar.count() - 1)
            self.bar.setCurrentIndex(target)
            path = self.paths[target]
            self.syncing = False
            self.panel.cd(path)
            return
        self.syncing = False

    def next_tab(self) -> None:
        index = (self.bar.currentIndex() + 1) % self.bar.count()
        self.bar.setCurrentIndex(index)

    # -- слоты ---------------------------------------------------------------

    def _on_current_changed(self, index: int) -> None:
        if self.syncing or not (0 <= index < len(self.paths)):
            return
        path = self.paths[index]
        if path != self.panel.current_path():
            self.syncing = True
            self.panel.cd(path)
            self.syncing = False

    def _on_close(self, index: int) -> None:
        if self.bar.tabRect(index).contains(
                self.bar.mapFromGlobal(self._cursor_pos())):
            pass  # закрытие кликом — индекс из сигнала
        self.close_tab(index)

    @staticmethod
    def _cursor_pos():
        from PySide6.QtGui import QCursor

        return QCursor.pos()

    def _on_moved(self, From: int, to: int) -> None:  # noqa: N803
        path = self.paths.pop(From)
        self.paths.insert(to, path)

    def _on_double(self, index: int) -> None:
        if index < 0:  # пустое место — новая вкладка
            self.new_tab()

    def _on_path_changed(self, path: str) -> None:
        if self.syncing:
            return
        index = self.bar.currentIndex()
        if 0 <= index < len(self.paths) and self.paths[index] != path:
            self.paths[index] = path
            self.bar.setTabText(index, _display(path))


class Plugin(SphaeraPlugin):
    id = "foldertabs"
    title = "Вкладки папок"

    def __init__(self, app):
        super().__init__(app)
        self._tabs: dict = {}
        self._restored = False

    def on_loaded(self):
        for panel in (self.app.left, self.app.right):
            self._tabs[panel] = _PanelTabs(panel, self.app._status)
        if not self._restored:
            self._restored = True
            # восстановление — когда панели уже получили пути из config
            # (MainWindow восстанавливает панели после загрузки модулей)
            QTimer.singleShot(0, self._restore)

    def _restore(self) -> None:
        """Восстановить сохранённый набор вкладок (пересборка списка):
        панель к этому моменту могла уже получить путь из config — он
        становится текущей вкладкой, сохранённые пути дополняются к ней."""
        from sphaera_commander import config

        raw = config.qsettings().value(TABS_KEY, "")
        try:
            data = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            return
        for side, panel in (("left", self.app.left), ("right", self.app.right)):
            paths = data.get(side) or []
            if not isinstance(paths, list):
                continue
            tabs = self._tabs.get(panel)
            if tabs is None:
                continue
            valid: list[str] = []
            for p in paths:
                if (isinstance(p, str) and os.path.isdir(p)
                        and p not in valid):
                    valid.append(p)
            if not valid:
                continue
            # фактический путь панели берём из config, а не из модели:
            # скан начального cd мог ещё не примениться
            configured = config.qsettings().value(
                f"panels/{side}/path", "") or ""
            current = (configured if os.path.isdir(configured)
                       else panel.current_path())
            if current not in valid and os.path.isdir(current):
                valid.insert(0, current)
            if valid == tabs.paths:
                continue
            tabs.syncing = True
            tabs.paths = list(valid)
            while tabs.bar.count() > len(tabs.paths):
                tabs.bar.removeTab(tabs.bar.count() - 1)
            while tabs.bar.count() < len(tabs.paths):
                tabs.bar.addTab("")
            for index, p in enumerate(tabs.paths):
                tabs.bar.setTabText(index, _display(p))
            if current in tabs.paths:
                tabs.bar.setCurrentIndex(tabs.paths.index(current))
            tabs.syncing = False

    def _save(self) -> None:
        from sphaera_commander import config

        data = {}
        for side, panel in (("left", self.app.left), ("right", self.app.right)):
            tabs = self._tabs.get(panel)
            data[side] = tabs.paths if tabs is not None else [panel.current_path()]
        config.qsettings().setValue(TABS_KEY, json.dumps(data, ensure_ascii=False))

    def on_shutdown(self):
        self._save()

    def _tabs_for_active(self) -> _PanelTabs:
        return self._tabs[self.app.active]

    def tools_actions(self):
        return [
            (tr("Новая вкладка"), lambda: self._tabs_for_active().new_tab(),
             "Ctrl+T"),
            (tr("Закрыть вкладку"), lambda: self._tabs_for_active().close_tab(),
             "Ctrl+W"),
            (tr("Следующая вкладка"), lambda: self._tabs_for_active().next_tab(),
             None),
        ]


def create(app):
    return Plugin(app)
