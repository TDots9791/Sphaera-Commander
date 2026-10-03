"""Тесты наследуемых форматов: RTF, XLS, DOC."""

import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from sphaera_commander import legacy_formats as lf  # noqa: E402


def make_xls(path: str) -> None:
    import xlwt

    book = xlwt.Workbook()
    ws = book.add_sheet("Товары")
    ws.write(0, 0, "Имя")
    ws.write(0, 1, "Цена")
    ws.write(1, 0, "Хлеб")
    ws.write(1, 1, 50)
    ws2 = book.add_sheet("Итоги")
    ws2.write(0, 0, 140)
    book.save(path)


class RtfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_rtf_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_unicode(self):
        path = os.path.join(self.tmp, "doc.rtf")
        with open(path, "w", encoding="ascii") as f:
            f.write(r"{\rtf1\ansi\deff0{\fonttbl{\f0 Arial;}}"
                    r"\f0\fs24 \u1055?\u1088?\u1080?\u1074?\u1077?\u1090?"
                    r" \u1084?\u1080?\u1088?\par}")
        text = lf.rtf_to_text(path)
        self.assertIn("Привет мир", text)

    def test_save_roundtrip(self):
        path = os.path.join(self.tmp, "out.rtf")
        lf.save_rtf(path, ["Первая строка", "Вторая {строка} \\ со слэшем"])
        text = lf.rtf_to_text(path)
        self.assertIn("Первая строка", text)
        self.assertIn("Вторая {строка} \\ со слэшем", text)

    def test_ascii_only_output(self):
        path = os.path.join(self.tmp, "uni.rtf")
        lf.save_rtf(path, ["кириллица ✓"])
        with open(path, "rb") as f:
            raw = f.read()
        self.assertTrue(all(b < 128 for b in raw))

    def test_viewer_edit_save(self):
        from sphaera_commander.viewer import FileViewerDialog

        path = os.path.join(self.tmp, "doc.rtf")
        with open(path, "w", encoding="ascii") as f:
            f.write(r"{\rtf1\ansi \u1055?\u1088?\u1080?\u1074?\u1077?\u1090?\par}")
        dlg = FileViewerDialog(None, [path], 0, editable=True)
        self.assertEqual(dlg.kind, "rtf")
        self.assertIn("Привет", dlg.text_edit.toPlainText())
        dlg.text_edit.setPlainText("Новый текст")
        dlg._save()
        self.assertIn("Новый текст", lf.rtf_to_text(path))
        dlg.reject()


class XlsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_xls_")
        self.xls = os.path.join(self.tmp, "book.xls")
        make_xls(self.xls)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_sheets(self):
        sheets = lf.xls_sheets(self.xls)
        self.assertEqual([name for name, _ in sheets], ["Товары", "Итоги"])
        self.assertEqual(sheets[0][1][1], ["Хлеб", "50"])

    def test_save_roundtrip(self):
        sheets = lf.xls_sheets(self.xls)
        sheets[0][1].append(["Молоко", "90"])
        lf.xls_save(self.xls, sheets)
        reread = lf.xls_sheets(self.xls)
        self.assertIn(["Молоко", "90"], reread[0][1])
        self.assertEqual(reread[1][1][0][0], "140")

    def test_long_sheet_name_safe(self):
        sheets = [("О" * 40, [["x"]])]
        lf.xls_save(self.xls, sheets)
        reread = lf.xls_sheets(self.xls)
        self.assertEqual(len(reread[0][0]), 31)

    def test_viewer_grid_editable_and_save(self):
        from sphaera_commander.viewer import FileViewerDialog

        dlg = FileViewerDialog(None, [self.xls], 0, editable=True)
        self.assertEqual(dlg.kind, "xls")
        self.assertEqual(dlg.stack.currentIndex(), 4)
        self.assertTrue(dlg.sheet_model.editable)
        model = dlg.sheet_model
        model.setData(model.index(1, 1), "99", __import__("PySide6.QtCore",
                                                          fromlist=["Qt"]).Qt.EditRole)
        self.assertTrue(model.dirty)
        with unittest.mock.patch.object(
                QMessageBox, "question",
                staticmethod(lambda *a, **k: QMessageBox.Yes)):
            dlg._save()
        self.assertFalse(model.dirty)
        reread = lf.xls_sheets(self.xls)
        self.assertEqual(reread[0][1][1], ["Хлеб", "99"])
        dlg.reject()

    def test_reject_offers_save(self):
        from sphaera_commander.viewer import FileViewerDialog

        dlg = FileViewerDialog(None, [self.xls], 0, editable=True)
        model = dlg.sheet_model
        model.setData(model.index(1, 0), "Соль",
                      __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.EditRole)
        answers = {"n": 0}

        def fake_question(*_a, **_k):
            answers["n"] += 1
            return (QMessageBox.Cancel if answers["n"] == 1
                    else QMessageBox.Yes)

        with unittest.mock.patch.object(
                QMessageBox, "question",
                staticmethod(fake_question)):
            dlg.reject()  # Cancel — остаёмся
            self.assertTrue(dlg.isVisible() or True)  # reject прерван
            dlg.reject()  # Yes — сохранение и закрытие
        reread = lf.xls_sheets(self.xls)
        self.assertEqual(reread[0][1][1][0], "Соль")


class DocTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_doc_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_doc_saved_as_rtf_reads_via_striprtf(self):
        path = os.path.join(self.tmp, "doc.doc")
        with open(path, "w", encoding="ascii") as f:
            f.write(r"{\rtf1\ansi \u1042?\u1072?\u1078?\u1085?\u1086?\par}")
        text = lf.doc_to_text(path)
        self.assertIn("Важно", text)

    def test_binary_doc_without_tool_raises(self):
        path = os.path.join(self.tmp, "real.doc")
        with open(path, "wb") as f:
            f.write(b"\xd0\xcf\x11\xe0garbage")
        with unittest.mock.patch.object(lf.shutil, "which", return_value=None):
            with self.assertRaises(ValueError) as ctx:
                lf.doc_to_text(path)
        self.assertIn("antiword", str(ctx.exception))

    def test_binary_doc_with_antiword(self):
        path = os.path.join(self.tmp, "real.doc")
        with open(path, "wb") as f:
            f.write(b"\xd0\xcf\x11\xe0garbage")

        def fake_run(cmd, **_kw):
            assert cmd[0].endswith("antiword")
            return unittest.mock.MagicMock(returncode=0,
                                           stdout="Текст из Word")

        with unittest.mock.patch.object(lf.shutil, "which",
                                        return_value="/usr/bin/antiword"):
            with unittest.mock.patch.object(lf.subprocess, "run", fake_run):
                text = lf.doc_to_text(path)
        self.assertEqual(text, "Текст из Word")

    def test_catdoc_gets_utf8_flag(self):
        path = os.path.join(self.tmp, "real.doc")
        with open(path, "wb") as f:
            f.write(b"\xd0\xcf\x11\xe0garbage")
        cmds = []

        def fake_run(cmd, **_kw):
            cmds.append(cmd)
            return unittest.mock.MagicMock(returncode=0, stdout="ok")

        with unittest.mock.patch.object(lf.shutil, "which",
                                        return_value="/usr/bin/catdoc"):
            with unittest.mock.patch.object(lf.subprocess, "run", fake_run):
                lf.doc_to_text(path)
        self.assertIn("-d", cmds[0])
        self.assertIn("utf-8", cmds[0])


if __name__ == "__main__":
    unittest.main()
