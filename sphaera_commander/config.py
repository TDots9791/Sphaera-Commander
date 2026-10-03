"""Сохранение и восстановление состояния через QSettings."""

from __future__ import annotations

from PySide6.QtCore import QSettings


def qsettings() -> QSettings:
    return QSettings("SphaeraCommander", "SphaeraCommander")


def load_cmd_history() -> list[str]:
    value = qsettings().value("cmdline/history", [])
    if isinstance(value, str):
        return [value]
    return [str(v) for v in (value or [])]


def save_cmd_history(history: list[str]) -> None:
    qsettings().setValue("cmdline/history", history[-50:])


def load_panel(panel, prefix: str) -> None:
    s = qsettings()
    path = s.value(f"panels/{prefix}/path", "")
    col = int(s.value(f"panels/{prefix}/sort_col", 0))
    desc = s.value(f"panels/{prefix}/sort_desc", "false") in (True, "true", "1")
    panel.model.sort_col = col
    panel.model.sort_desc = desc
    if path:
        panel.cd(path, quiet=True)


def save_panel(panel, prefix: str) -> None:
    s = qsettings()
    s.setValue(f"panels/{prefix}/path", panel.current_path())
    s.setValue(f"panels/{prefix}/sort_col", panel.model.sort_col)
    s.setValue(f"panels/{prefix}/sort_desc", panel.model.sort_desc)


def default_geometry(win) -> None:
    """Размер при первом запуске: ~половина площади экрана (0.7×0.7)."""
    from PySide6.QtGui import QGuiApplication

    screen = QGuiApplication.primaryScreen()
    if screen is None:
        win.resize(1150, 720)
        return
    avail = screen.availableGeometry()
    win.resize(max(int(avail.width() * 0.7), 820),
               max(int(avail.height() * 0.7), 520))


def load_window(win) -> bool:
    """Восстановить геометрию; вернуть флаг показа скрытых файлов."""
    s = qsettings()
    geometry = s.value("window/geometry")
    if geometry is not None:
        win.restoreGeometry(geometry)
        # микроскопическое окно (осталось от старых версий или тестов) —
        # не рабочий размер: заменяем расчётным
        if win.width() < 700 or win.height() < 480:
            default_geometry(win)
    else:
        default_geometry(win)
    sizes = s.value("window/splitter")
    if sizes:
        try:
            win.splitter.setSizes([int(v) for v in sizes])
        except (TypeError, ValueError):
            pass
    return s.value("view/show_hidden", "false") in (True, "true", "1")


def save_window(win) -> None:
    s = qsettings()
    s.setValue("window/geometry", win.saveGeometry())
    s.setValue("window/splitter", win.splitter.sizes())
    s.setValue("view/show_hidden", win.show_hidden)
