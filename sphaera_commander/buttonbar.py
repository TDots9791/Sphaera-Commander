"""Панель кнопок под адресной строкой — как в Total Commander.

Кнопка = {title, cmd}; в cmd подставляются плейсхолдеры TC:
  %f — объект под курсором панели (полный путь),
  %d — текущий каталог панели.
Команда выполняется /bin/sh -c в каталоге панели, отсоединённо.
Хранение: ~/.config/sphaera-commander/buttons.json. Логика (load/save,
build_command) не зависит от Qt и тестируется без GUI."""

from __future__ import annotations

import json
import os
import shlex
import subprocess

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sphaera_commander.i18n import tr

BUTTONS_FILE = os.path.expanduser(
    "~/.config/sphaera-commander/buttons.json")


def load_buttons() -> list[dict]:
    try:
        with open(BUTTONS_FILE, encoding="utf-8") as f:
            buttons = json.load(f).get("buttons", [])
    except (OSError, ValueError):
        return []
    return [b for b in buttons
            if isinstance(b, dict) and b.get("title") and b.get("cmd")]


def save_buttons(buttons: list[dict]) -> None:
    os.makedirs(os.path.dirname(BUTTONS_FILE), exist_ok=True)
    tmp = BUTTONS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"buttons": buttons}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, BUTTONS_FILE)


def build_command(cmd: str, panel_dir: str, entry_path: str | None) -> str:
    """Подставить %f (объект под курсором) и %d (каталог панели) с кавычками."""
    out = cmd.replace("%f", shlex.quote(entry_path) if entry_path else "''")
    return out.replace("%d", shlex.quote(panel_dir))


# Отсоединённый запуск через платформенный слой: win64/run.py инжектирует
# реализацию (ТЗ §4); по умолчанию — /bin/sh -c, вывод в никуда.
run_detached_shell = None


def run_command(built: str, cwd: str) -> None:
    """Отсоединённый запуск: не тянем потомка за собой, вывод в никуда."""
    if run_detached_shell is not None:
        run_detached_shell(built, cwd, detached=True)
        return
    subprocess.Popen(
        ["/bin/sh", "-c", built], cwd=cwd,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True)


class ButtonBar(QWidget):
    """Строка кнопок + «⚙» (редактор). Одна и та же кнопка работает в контексте
    той панели, под которой лежит: %d — её каталог, %f — её курсор."""

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(2, 1, 2, 1)
        self._row.setSpacing(2)
        self.reload()

    def reload(self) -> None:
        while self._row.count():
            item = self._row.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for spec in load_buttons():
            btn = QToolButton(self)
            btn.setText(str(spec.get("title", "")))
            btn.setToolTip(str(spec.get("cmd", "")))
            btn.clicked.connect(
                lambda _=False, command=str(spec.get("cmd", "")): self._run(command))
            self._row.addWidget(btn)
        self._row.addStretch(1)
        gear = QToolButton(self)
        gear.setText("⚙")
        gear.setToolTip(tr("Настроить кнопки панели"))
        gear.clicked.connect(self.edit_buttons)
        self._row.addWidget(gear)

    def _run(self, command: str) -> None:
        entry = self.panel.current_entry()
        entry_path = entry.path if entry is not None else None
        if entry_path is not None and self.panel.is_vfs:
            entry_path = None  # команды работают только с обычными путями
        if self.panel.is_vfs:
            cwd = os.path.expanduser("~")
        else:
            cwd = self.panel.current_path()
        run_command(build_command(command, cwd, entry_path), cwd)

    def edit_buttons(self) -> None:
        dlg = ButtonsEditor(self, load_buttons())
        if dlg.exec():
            save_buttons(dlg.result_buttons())
            self.reload()


class ButtonsEditor(QDialog):
    """Редактор кнопок: список «имя — команда», добавить/переименовать/удалить."""

    def __init__(self, parent, buttons: list[dict]):
        super().__init__(parent)
        self.setWindowTitle(tr("Кнопки панели"))
        self.resize(520, 320)
        self._buttons = [dict(b) for b in buttons]

        self.list = QListWidget(self)
        for b in self._buttons:
            QListWidgetItem(f"{b['title']}  —  {b['cmd']}", self.list)

        row = QHBoxLayout()
        btn_add = QPushButton(tr("Добавить…"))
        btn_add.clicked.connect(self._add)
        btn_edit = QPushButton(tr("Изменить…"))
        btn_edit.clicked.connect(self._edit)
        btn_del = QPushButton(tr("Удалить"))
        btn_del.clicked.connect(self._delete)
        btn_up = QPushButton(tr("Выше"))
        btn_up.clicked.connect(lambda: self._move(-1))
        btn_down = QPushButton(tr("Ниже"))
        btn_down.clicked.connect(lambda: self._move(1))
        for w in (btn_add, btn_edit, btn_del, btn_up, btn_down):
            row.addWidget(w)
        row.addStretch(1)

        btn_close = QPushButton(tr("Сохранить"))
        btn_close.clicked.connect(self.accept)
        btn_cancel = QPushButton(tr("Отмена"))
        btn_cancel.clicked.connect(self.reject)
        bottom = QHBoxLayout()
        hint = QLabel(tr("Плейсхолдеры: %f — файл под курсором, %d — каталог панели"))
        bottom.addWidget(hint, 1)
        bottom.addWidget(btn_close)
        bottom.addWidget(btn_cancel)

        layout = QVBoxLayout(self)
        layout.addWidget(self.list, 1)
        layout.addLayout(row)
        layout.addLayout(bottom)

    def result_buttons(self) -> list[dict]:
        return self._buttons

    def _refresh(self) -> None:
        self.list.clear()
        for b in self._buttons:
            QListWidgetItem(f"{b['title']}  —  {b['cmd']}", self.list)

    def _ask(self, title: str, label: str, value: str = "") -> str:
        text, ok = QInputDialog.getText(self, title, label, text=value)
        return text.strip() if ok else ""

    def _add(self):
        title = self._ask(tr("Кнопки панели"), tr("Название кнопки:"))
        if not title:
            return
        cmd = self._ask(tr("Кнопки панели"), tr("Команда (%f — файл, %d — каталог):"))
        if not cmd:
            return
        self._buttons.append({"title": title, "cmd": cmd})
        self._refresh()

    def _edit(self):
        row = self.list.currentRow()
        if not 0 <= row < len(self._buttons):
            return
        b = self._buttons[row]
        title = self._ask(tr("Кнопки панели"), tr("Название кнопки:"), b["title"])
        if not title:
            return
        cmd = self._ask(tr("Кнопки панели"),
                        tr("Команда (%f — файл, %d — каталог):"), b["cmd"])
        if not cmd:
            return
        self._buttons[row] = {"title": title, "cmd": cmd}
        self._refresh()

    def _delete(self):
        row = self.list.currentRow()
        if not 0 <= row < len(self._buttons):
            return
        del self._buttons[row]
        self._refresh()

    def _move(self, delta: int):
        row = self.list.currentRow()
        new = row + delta
        if not 0 <= row < len(self._buttons) or not 0 <= new < len(self._buttons):
            return
        self._buttons[row], self._buttons[new] = (
            self._buttons[new], self._buttons[row])
        self._refresh()
        self.list.setCurrentRow(new)
