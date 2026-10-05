"""Модуль «Архивные профили»: именованные presets упаковки (формат +
исключения), хранятся в ~/.config/sphaera-commander/archive-profiles.json.
«Упаковать по профилю» берёт отмеченные объекты активной панели."""

from __future__ import annotations

import json
import os
import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

from sphaera_commander.i18n import tr
from sphaera_commander.plugin_api import SphaeraPlugin

PROFILES_FILE = os.path.expanduser(
    "~/.config/sphaera-commander/archive-profiles.json")
FORMATS = ("zip", "tar.gz", "tar.bz2", "tar.xz")


def load_profiles() -> list[dict]:
    try:
        with open(PROFILES_FILE, encoding="utf-8") as f:
            return json.load(f).get("profiles", [])
    except (OSError, ValueError):
        return []


def save_profiles(profiles: list[dict]) -> None:
    os.makedirs(os.path.dirname(PROFILES_FILE), exist_ok=True)
    tmp = PROFILES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"profiles": profiles}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PROFILES_FILE)


class ProfilesDialog(QDialog):
    """Редактор профилей + упаковка отмеченных объектов по профилю."""

    def __init__(self, parent, plugin):
        super().__init__(parent)
        self.plugin = plugin
        self.app = parent
        self.setWindowTitle(tr("Архивные профили"))
        self.resize(620, 420)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(lambda _i: self._load_fields())

        form = QHBoxLayout()
        grid = QVBoxLayout()
        grid.addWidget(QLabel(tr("Имя профиля:")))
        self.name = QLineEdit()
        grid.addWidget(self.name)
        grid.addWidget(QLabel(tr("Формат:")))
        self.fmt = QComboBox()
        self.fmt.addItems(FORMATS)
        grid.addWidget(self.fmt)
        grid.addWidget(QLabel(tr("Исключения (через запятую):")))
        self.excludes = QLineEdit()
        self.excludes.setPlaceholderText("*.tmp, *.log, node_modules")
        grid.addWidget(self.excludes)
        form.addWidget(self.list, 1)
        form.addLayout(grid, 1)

        row = QHBoxLayout()
        btn_new = QPushButton(tr("Сохранить профиль"))
        btn_new.clicked.connect(self._save)
        btn_pack = QPushButton(tr("Упаковать отмеченные по профилю"))
        btn_pack.setDefault(True)
        btn_pack.clicked.connect(self._pack)
        btn_del = QPushButton(tr("Удалить профиль"))
        btn_del.clicked.connect(self._delete)
        close = QPushButton(tr("Закрыть"))
        close.clicked.connect(self.close)
        row.addWidget(btn_del)
        row.addWidget(btn_new)
        row.addStretch(1)
        row.addWidget(btn_pack)
        row.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel(tr(
            "Отмеченные объекты активной панели будут упакованы")))
        layout.addLayout(row)
        self.reload()

    def reload(self):
        self.list.clear()
        for profile in load_profiles():
            self.list.addItem(f"{profile['name']} ({profile['fmt']})")

    def _current(self) -> dict | None:
        row = self.list.currentRow()
        profiles = load_profiles()
        return profiles[row] if 0 <= row < len(profiles) else None

    def _load_fields(self):
        profile = self._current()
        if profile is None:
            return
        self.name.setText(profile["name"])
        self.fmt.setCurrentText(profile["fmt"])
        self.excludes.setText(", ".join(profile.get("excludes", [])))

    def _save(self):
        name = self.name.text().strip()
        if not name:
            return
        profiles = [p for p in load_profiles() if p["name"] != name]
        profiles.append({
            "name": name,
            "fmt": self.fmt.currentText(),
            "excludes": [x.strip() for x in self.excludes.text().split(",")
                         if x.strip()],
        })
        save_profiles(profiles)
        self.reload()

    def _delete(self):
        profile = self._current()
        if profile is None:
            return
        save_profiles([p for p in load_profiles()
                       if p["name"] != profile["name"]])
        self.reload()

    def _pack(self):
        profile = self._current()
        if profile is None:
            self.plugin.app._status(tr("Выберите профиль"))
            return
        panel = self.plugin.app.active
        sources = panel.selected_entries()
        if not sources:
            self.plugin.app._status(tr("Нет отмеченных объектов (Insert — отметить)"))
            return
        default = sources[0].name + "." + profile["fmt"]
        out, ok = QInputDialog.getText(
            self, tr("Упаковать по профилю {name}").format(name=profile["name"]),
            tr("Имя архива:"), text=default)
        if not ok or not out.strip():
            return
        out = os.path.join(panel.current_path(), out.strip())
        from sphaera_commander.archives import pack_items

        self.plugin.app._status(tr("Упаковка…"))

        def worker():
            import itertools

            def progress(_p):
                pass

            def cancelled():
                return False

            excludes = profile.get("excludes", [])
            result = pack_items(sources, out, profile["fmt"],
                                progress, cancelled, excludes=excludes)
            code = 0 if result.ok else (tr("прервано") if result.cancelled
                                        else "; ".join(e.message for e in result.errors[:3]))
            self.app.gui_call.emit(lambda: self._packed(out, code))

        threading.Thread(target=worker, daemon=True,
                         name="archprofile").start()

    def _packed(self, out, code):
        if code == 0:
            self.plugin.app._status(tr("Готово: {path}").format(path=out))
            self.plugin.app.active.reveal(os.path.basename(out))
        else:
            self.plugin.app._status(tr("Ошибка упаковки: {err}").format(err=code))


class Plugin(SphaeraPlugin):
    id = "archprofiles"
    title = "Архивные профили"

    def tools_actions(self):
        return [(tr("Архивные профили…"), self._show, None)]

    def _show(self):
        dlg = ProfilesDialog(self.app, self)
        dlg.setModal(False)
        dlg.show()
        if not hasattr(self.app, "_plugin_dialogs"):
            self.app._plugin_dialogs = []
        self.app._plugin_dialogs.append(dlg)


def create(app):
    return Plugin(app)
