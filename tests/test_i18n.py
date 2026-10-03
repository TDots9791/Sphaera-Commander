"""Тесты локализации: полнота словаря en/zh, плейсхолдеры, окно на языках,
фокус в панели при старте."""

import ast
import glob
import os
import re
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# MainWindow при закрытии пишет настройки — изолируем QSettings
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sc_i18n_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

from sphaera_commander import config, i18n  # noqa: E402

_FIELD = re.compile(r"{([a-zA-Z_]+)(![rs])?")


def _tr_const_keys() -> set[str]:
    """Все строковые константы, переданные tr() в коде приложения."""
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "sphaera_commander")
    keys = set()
    for path in glob.glob(os.path.join(root, "*.py")):
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "tr"):
                if (node.args and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)):
                    keys.add(node.args[0].value)
    return keys


class I18nDictTests(unittest.TestCase):
    def test_every_tr_key_has_translation(self):
        known = set(i18n.all_keys())
        self.assertEqual(_tr_const_keys() - known, set(),
                         "ключи tr() без записи в словаре")
        dead = known - _tr_const_keys() - i18n.DYNAMIC_KEYS
        self.assertEqual(dead, set(),
                         "мёртвые ключи словаря (строки уже не в коде)")
        self.assertTrue(i18n.DYNAMIC_KEYS <= known,
                        "динамический ключ отсутствует в словаре")

    def test_every_key_has_en_and_zh(self):
        for key, langs in i18n._STRINGS.items():
            self.assertIn("en", langs, key)
            self.assertIn("zh", langs, key)
            self.assertTrue(langs["en"].strip(), f"пустой en: {key!r}")
            self.assertTrue(langs["zh"].strip(), f"пустой zh: {key!r}")

    def test_placeholders_preserved_in_translations(self):
        for key, langs in i18n._STRINGS.items():
            fields = set(_FIELD.findall(key))
            for lang, value in langs.items():
                if value == key:  # непереводимая строка (суффикс файла и т.п.)
                    continue
                self.assertEqual(set(_FIELD.findall(value)), fields,
                                 f"{key!r} [{lang}]")

    def test_tr_fallback_to_russian(self):
        i18n._STRINGS["Ключ-без-перевода"] = {"en": "x"}  # без zh
        try:
            i18n.LANG = "zh"
            self.assertEqual(i18n.tr("Ключ-без-перевода"), "Ключ-без-перевода")
        finally:
            del i18n._STRINGS["Ключ-без-перевода"]
            i18n.LANG = "ru"


class I18nWindowTests(unittest.TestCase):
    """Интерфейс собирается на en и zh, выбор языка читается из config."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_apply_language_from_config(self):
        config.qsettings().setValue("view/language", "en")
        try:
            from sphaera_commander import app as app_mod

            app_mod._apply_language()
            self.assertEqual(i18n.LANG, "en")
        finally:
            config.qsettings().remove("view/language")
            i18n.LANG = "ru"

    def test_window_builds_in_en_and_zh(self):
        from sphaera_commander import app as app_mod

        saved = i18n.LANG
        try:
            for lang, file_title in (("en", "&File"), ("zh", "文件(&F)")):
                i18n.LANG = lang
                w = app_mod.MainWindow()
                w.show()
                self.app.processEvents()
                self.assertEqual(w.menuBar().actions()[0].text(), file_title)
                self.assertTrue(w.left.view.focusWidget() is not None)
                w.close()
                self.app.processEvents()
        finally:
            i18n.LANG = saved


class StartupFocusTests(unittest.TestCase):
    """Фокус при старте — в таблице левой панели, не в строке адреса."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_focus_on_left_view(self):
        from sphaera_commander import app as app_mod

        w = app_mod.MainWindow()
        try:
            w.show()
            self.app.processEvents()
            self.assertIs(QApplication.focusWidget(), w.left.view)
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
