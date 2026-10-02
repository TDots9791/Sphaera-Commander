"""Тесты просмотрщика: режимы json/md, валидация при сохранении, перенос строк."""

import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
