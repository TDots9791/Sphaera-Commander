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
    """Фокус при старте — в таблице левой панели, не в строке адреса;
    Tab/Shift+Tab переключает панели, фокус следует за активной."""

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

    def test_tab_switches_panels_with_focus(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from sphaera_commander import app as app_mod

        w = app_mod.MainWindow()
        try:
            w.show()
            w.left.view.setFocus()
            self.app.processEvents()
            QTest.keyClick(w.left.view, Qt.Key_Tab)
            self.app.processEvents()
            self.assertIs(w.active, w.right)
            self.assertIs(QApplication.focusWidget(), w.right.view)
            QTest.keyClick(w.right.view, Qt.Key_Backtab)
            self.app.processEvents()
            self.assertIs(w.active, w.left)
            self.assertIs(QApplication.focusWidget(), w.left.view)
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()


class DeleteKeyTests(unittest.TestCase):
    """Del — в корзину, Shift+Del — безвозвратно: та же цепь, что F8."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_view_emits_delete_requested(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from sphaera_commander.panel import FileView

        v = FileView()
        got = []
        v.delete_requested.connect(got.append)
        QTest.keyClick(v, Qt.Key_Delete)
        QTest.keyClick(v, Qt.Key_Delete, Qt.ShiftModifier)
        self.assertEqual(got, [False, True])

    def test_del_trashes_via_app_chain(self):
        import time
        import unittest.mock

        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from sphaera_commander import app as app_mod
        from sphaera_commander.ops import OpResult

        w = app_mod.MainWindow()
        try:
            w.show()
            self.app.processEvents()
            tmp = tempfile.mkdtemp(prefix="sc_del_")
            self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
            target = os.path.join(tmp, "файл.txt")
            with open(target, "w") as f:
                f.write("x")
            w.left.cd(tmp)
            w.left.wait_loaded()
            w.left.view.set_current_row(
                w.left.model.row_of_name("файл.txt"))
            calls = []

            def fake_trash(entries, progress_cb, is_cancelled):
                calls.extend(e.path for e in entries)
                return OpResult(done_files=len(entries))

            with unittest.mock.patch.object(app_mod, "confirm_delete",
                                            return_value=True), \
                 unittest.mock.patch.object(app_mod, "execute_trash",
                                            side_effect=fake_trash):
                QTest.keyClick(w.left.view, Qt.Key_Delete)
                deadline = time.monotonic() + 5
                while w._thread is not None and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.02)
            self.assertEqual(calls, [target])
        finally:
            w.close()
            self.app.processEvents()
