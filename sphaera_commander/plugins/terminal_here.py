"""Модуль «Открыть терминал здесь»: терминал в каталоге панели."""

from __future__ import annotations

import shutil

from PySide6.QtCore import QProcess

from sphaera_commander.i18n import tr
from sphaera_commander.plugin_api import SphaeraPlugin

# (исполняемый файл, [аргументы с {dir}])
TERMINALS = (
    ("kgx", ("--working-directory", "{dir}")),
    ("gnome-terminal", ("--working-directory", "{dir}")),
    ("konsole", ("--workdir", "{dir}")),
    ("xfce4-terminal", ("--working-directory", "{dir}")),
    ("xterm", ("-e", "sh", "-c", "cd '{dir}'; exec ${SHELL:-sh}")),
)


def find_terminal() -> tuple[str, tuple[str, ...]] | None:
    for name, args in TERMINALS:
        path = shutil.which(name)
        if path:
            return path, args
    return None


def open_in(directory: str) -> str | None:
    """Запустить терминал в каталоге; вернуть ошибку или None."""
    found = find_terminal()
    if found is None:
        return tr("терминал не найден (kgx/gnome-terminal/konsole/xterm)")
    prog, args = found
    argv = [prog] + [a.replace("{dir}", directory) for a in args]
    QProcess.startDetached(argv[0], argv[1:], directory)
    return None


class Plugin(SphaeraPlugin):
    id = "terminal_here"
    title = "Терминал здесь"

    def tools_actions(self):
        return [(tr("Открыть терминал здесь"), self._open_active, "Ctrl+T")]

    def context_actions(self, panel, entry):
        if entry is not None and entry.is_dir:
            return [(tr("Открыть терминал здесь"),
                     lambda e=entry: self._open(e.path))]
        return []

    def _open_active(self):
        self._open(self.app.active.current_path())

    def _open(self, directory: str):
        error = open_in(directory)
        if error:
            self.app._status(error)


def create(app):
    return Plugin(app)
