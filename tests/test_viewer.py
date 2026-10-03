"""Тесты просмотрщика: режимы json/md, валидация при сохранении, перенос строк."""

import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox, QPlainTextEdit  # noqa: E402

from sphaera_commander.viewer import (  # noqa: E402
    FileViewerDialog,
    JsonHighlighter,
    json_error_position,
)


def write(path: str, text: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class ViewerBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_viewer_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class JsonPositionTests(unittest.TestCase):
    def test_ok(self):
        self.assertIsNone(json_error_position('{"a": 1}'))

    def test_error_position(self):
        msg = json_error_position('{\n  "a": 1,\n  "b": \n}')
        self.assertIsNotNone(msg)
        self.assertIn("строка 4", msg)


class JsonViewerTests(ViewerBase):
    def test_valid_json_status_and_format(self):
        path = write(os.path.join(self.tmp, "data.json"),
                     '{"b":2,"a":[1,2,{"c":null}]}')
        dlg = FileViewerDialog(None, [path], 0, editable=False)
        self.assertEqual(dlg.kind, "json")
        self.assertIn("JSON: OK", dlg.lbl_info.text())
        self.assertIsNotNone(dlg._highlighter)
        self.assertFalse(dlg.btn_format.isHidden())
        dlg.btn_format.click()
        text = dlg.text_edit.toPlainText()
        self.assertIn('\n    "b": 2,', text)
        self.assertIn("null", text)
        self.assertIn("JSON: OK", dlg.lbl_info.text())

    def test_invalid_json_status_and_format_guard(self):
        bad = '{"a": 1,'
        path = write(os.path.join(self.tmp, "bad.json"), bad)
        dlg = FileViewerDialog(None, [path], 0, editable=False)
        self.assertIn("JSON: ошибка", dlg.lbl_info.text())
        dlg.btn_format.click()
        self.assertEqual(dlg.text_edit.toPlainText(), bad)  # текст не тронут
        self.assertIn("не отформатировано", dlg.lbl_info.text())


class JsonEditorSaveTests(ViewerBase):
    def _editor(self, content: str) -> tuple[FileViewerDialog, str]:
        path = write(os.path.join(self.tmp, "e.json"), content)
        dlg = FileViewerDialog(None, [path], 0, editable=True)
        return dlg, path

    def test_save_valid_json(self):
        dlg, path = self._editor('{"a": 1}')
        dlg.text_edit.setPlainText('{"a": 2}')
        dlg._save()
        self.assertEqual(open(path, encoding="utf-8").read(), '{"a": 2}')
        self.assertIn("сохранено", dlg.lbl_info.text())

    def test_save_invalid_json_declined(self):
        original = '{"a": 1}'
        dlg, path = self._editor(original)
        dlg.text_edit.setPlainText('{"a": ')
        dlg.text_edit.document().setModified(True)  # как после правки пользователем
        with unittest.mock.patch.object(
                QMessageBox, "question",
                staticmethod(lambda *a, **k: QMessageBox.No)):
            dlg._save()
        self.assertEqual(open(path, encoding="utf-8").read(), original)
        self.assertTrue(dlg.text_edit.document().isModified())

    def test_save_invalid_json_forced(self):
        dlg, path = self._editor('{"a": 1}')
        dlg.text_edit.setPlainText('{"a": ')
        with unittest.mock.patch.object(
                QMessageBox, "question",
                staticmethod(lambda *a, **k: QMessageBox.Yes)):
            dlg._save()
        self.assertEqual(open(path, encoding="utf-8").read(), '{"a": ')
        self.assertFalse(dlg.text_edit.document().isModified())


class MarkdownTests(ViewerBase):
    def test_viewer_default_is_rendered(self):
        path = write(os.path.join(self.tmp, "doc.md"),
                     "# Заголовок\n\nтекст **жирный**\n")
        dlg = FileViewerDialog(None, [path], 0, editable=False)
        self.assertEqual(dlg.kind, "md")
        self.assertEqual(dlg.stack.currentIndex(), 1)  # предпросмотр
        plain = dlg.preview.document().toPlainText()
        self.assertIn("Заголовок", plain)
        self.assertIn("жирный", plain)
        # переключение на исходник
        dlg.btn_preview.setChecked(False)
        self.assertEqual(dlg.stack.currentIndex(), 0)
        self.assertIn("# Заголовок", dlg.text_edit.toPlainText())

    def test_editor_default_is_source_and_preview_updates(self):
        path = write(os.path.join(self.tmp, "doc.md"), "# Один\n")
        dlg = FileViewerDialog(None, [path], 0, editable=True)
        self.assertEqual(dlg.stack.currentIndex(), 0)  # исходник
        dlg.text_edit.setPlainText("# Два\nновый текст\n")
        dlg.btn_preview.setChecked(True)
        self.assertEqual(dlg.stack.currentIndex(), 1)
        self.assertIn("Два", dlg.preview.document().toPlainText())

    def test_md_saved_rerenders_preview(self):
        path = write(os.path.join(self.tmp, "doc.md"), "# Старый\n")
        dlg = FileViewerDialog(None, [path], 0, editable=True)
        dlg.btn_preview.setChecked(True)
        dlg.text_edit.setPlainText("# Новый заголовок\n")
        dlg._save()
        self.assertIn("Новый заголовок", dlg.preview.document().toPlainText())


class TextViewTests(ViewerBase):
    def test_plain_txt_and_wrap_toggle(self):
        path = write(os.path.join(self.tmp, "note.txt"), "просто текст\n")
        dlg = FileViewerDialog(None, [path], 0, editable=False)
        self.assertEqual(dlg.kind, "text")
        self.assertIn("кодировка: utf-8", dlg.lbl_info.text())
        self.assertTrue(dlg.btn_format.isHidden())
        self.assertTrue(dlg.btn_preview.isHidden())
        self.assertEqual(dlg.text_edit.lineWrapMode(), QPlainTextEdit.NoWrap)
        dlg.btn_wrap.setChecked(True)
        self.assertEqual(dlg.text_edit.lineWrapMode(), QPlainTextEdit.WidgetWidth)

    def test_highlighter_attaches_rules(self):
        doc_path = write(os.path.join(self.tmp, "x.json"), '{"k": [1, true]}')
        dlg = FileViewerDialog(None, [doc_path], 0, editable=False)
        hl = dlg._highlighter
        self.assertIsInstance(hl, JsonHighlighter)
        # подсветка не рушит содержимое
        self.assertEqual(dlg.text_edit.toPlainText(), '{"k": [1, true]}')

    def test_navigate_resets_mode(self):
        p_json = write(os.path.join(self.tmp, "a.json"), '{"a": 1}')
        p_txt = write(os.path.join(self.tmp, "b.txt"), "текст\n")
        dlg = FileViewerDialog(None, [p_json, p_txt], 0, editable=False)
        self.assertEqual(dlg.kind, "json")
        dlg.navigate(1)
        self.assertEqual(dlg.kind, "text")
        self.assertTrue(dlg.btn_format.isHidden())
        self.assertNotIn("JSON", dlg.lbl_info.text())


if __name__ == "__main__":
    unittest.main()


class EditorUpgradeTests(ViewerBase):
    """Номера строк, замена, переход на строку, позиция курсора, Ctrl+E."""

    def _editor(self, content: str, name: str = "e.txt") -> FileViewerDialog:
        path = write(os.path.join(self.tmp, name), content)
        return FileViewerDialog(None, [path], 0, editable=True)

    def test_line_number_area_and_width(self):
        dlg = self._editor("строка\n" * 120)
        self.assertTrue(dlg.text_edit.line_number_width() > 20)
        # 120 строк — ширина больше, чем у 2 строк
        dlg2 = self._editor("одна\n")
        self.assertGreater(dlg.text_edit.line_number_width(),
                           dlg2.text_edit.line_number_width())

    def test_replace_one_and_all(self):
        dlg = self._editor("кот, кот, кот")
        dlg.search.setText("кот")
        dlg.replace_edit.setText("пёс")
        dlg._replace_one()
        self.assertEqual(dlg.text_edit.toPlainText(), "пёс, кот, кот")
        dlg._replace_all()
        self.assertEqual(dlg.text_edit.toPlainText(), "пёс, пёс, пёс")

    def test_replace_all_no_self_loop(self):
        dlg = self._editor("a b a")
        dlg.search.setText("a")
        dlg.replace_edit.setText("aa")
        dlg._replace_all()
        self.assertEqual(dlg.text_edit.toPlainText(), "aa b aa")

    def test_replace_missing_reports(self):
        dlg = self._editor("текст")
        dlg.search.setText("нет-такого")
        dlg.replace_edit.setText("x")
        dlg._replace_all()
        self.assertIn("заменено: 0", dlg.lbl_info.text())

    def test_goto_line_moves_cursor(self):
        dlg = self._editor("\n".join(f"строка {i}" for i in range(1, 51)))
        dlg._goto_line(42)
        cursor = dlg.text_edit.textCursor()
        self.assertEqual(cursor.blockNumber(), 41)
        self.assertIn("строка 42", cursor.block().text())

    def test_cursor_status_updates(self):
        dlg = self._editor("первая\nвторая")
        dlg._goto_line(2)
        self.assertEqual(dlg.lbl_pos.text(), "строка 2, столбец 1")

    def test_monospace_font(self):
        from PySide6.QtGui import QFontDatabase

        dlg = self._editor("x")
        expected = QFontDatabase.systemFont(QFontDatabase.FixedFont).family()
        self.assertEqual(dlg.text_edit.font().family(), expected)

    def test_ctrl_e_external(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        dlg = self._editor("x")
        with unittest.mock.patch.object(QDesktopServices, "openUrl") as mock:
            dlg._open_external()
        self.assertEqual(mock.call_count, 1)
        self.assertIsInstance(mock.call_args[0][0], QUrl)

    def test_replace_row_hidden_until_toggled(self):
        dlg = self._editor("x")
        self.assertTrue(dlg.replace_row.isHidden())
        dlg.btn_replace_toggle.setChecked(True)
        self.assertFalse(dlg.replace_row.isHidden())


class JsonTreeTests(ViewerBase):
    def test_tree_build_and_toggle(self):
        path = write(os.path.join(self.tmp, "d.json"),
                     '{"объект": {"внутри": 1}, "список": [10, 20], "число": 5}')
        dlg = FileViewerDialog(None, [path], 0, editable=False)
        self.assertTrue(dlg.btn_tree.isHidden() is False)
        dlg.btn_tree.setChecked(True)
        self.assertEqual(dlg.stack.currentIndex(), 2)
        tree = dlg.tree
        self.assertEqual(tree.topLevelItemCount(), 3)
        names = [tree.topLevelItem(i).text(0) for i in range(3)]
        self.assertEqual(names, ["объект", "список", "число"])
        obj_item = tree.topLevelItem(0)
        self.assertEqual(obj_item.childCount(), 1)
        self.assertEqual(obj_item.child(0).text(1), "1")
        lst = tree.topLevelItem(1)
        self.assertEqual([lst.child(i).text(1) for i in range(2)], ["10", "20"])
        # обратно к тексту
        dlg.btn_tree.setChecked(False)
        self.assertEqual(dlg.stack.currentIndex(), 0)

    def test_tree_invalid_json_stays_in_text(self):
        path = write(os.path.join(self.tmp, "bad.json"), "{oops")
        dlg = FileViewerDialog(None, [path], 0, editable=False)
        dlg.btn_tree.setChecked(True)
        self.assertEqual(dlg.stack.currentIndex(), 0)  # не переключились
        self.assertIn("дерева нет", dlg.lbl_info.text())

    def test_tree_rebuilds_after_format(self):
        path = write(os.path.join(self.tmp, "d.json"), '{"k": [1]}')
        dlg = FileViewerDialog(None, [path], 0, editable=False)
        dlg.btn_tree.setChecked(True)
        dlg.btn_tree.setChecked(False)
        dlg.btn_format.click()
        dlg.btn_tree.setChecked(True)
        self.assertEqual(dlg.tree.topLevelItemCount(), 1)


class MarkdownTableTests(ViewerBase):
    def test_table_reconstructed_with_borders_and_header(self):
        path = write(os.path.join(
            self.tmp, "doc.md"),
            "| Имя | Цена |\n|-----|------|\n| Хлеб | 50 |\n| Молоко | 90 |\n")
        dlg = FileViewerDialog(None, [path], 0, editable=False)
        self.assertEqual(dlg.stack.currentIndex(), 1)
        from sphaera_commander.viewer import find_markdown_tables

        tables = find_markdown_tables(dlg.preview.document())
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].rows(), 3)
        self.assertEqual(tables[0].columns(), 2)
        fmt = tables[0].format()
        self.assertEqual(fmt.border(), 1.0)
        # шапка жирная: у первой ячейки есть жирный формат
        cell = tables[0].cellAt(0, 0)
        cursor = cell.firstCursorPosition()
        self.assertEqual(cursor.charFormat().fontWeight(), QFont.Bold)

    def test_plain_md_without_tables_unchanged(self):
        path = write(os.path.join(self.tmp, "doc.md"), "# Просто заголовок\n")
        dlg = FileViewerDialog(None, [path], 0, editable=False)
        from sphaera_commander.viewer import find_markdown_tables

        self.assertEqual(find_markdown_tables(dlg.preview.document()), [])


if __name__ == "__main__":
    unittest.main()
