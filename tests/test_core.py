"""Тесты движка операций, модели каталога и панели.

Запуск:  .venv/bin/python -m unittest discover -s tests -v
"""

import os
import shutil
import stat
import sys
import tempfile
import unittest

# offscreen до любых импортов PySide6
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sphaera_commander.fsmodel import (  # noqa: E402
    NAME_COL,
    SIZE_COL,
    FileEntry,
    human_size,
    scan_directory,
    sort_entries,
)
from sphaera_commander.ops import (  # noqa: E402
    POLICY_OVERWRITE,
    POLICY_SKIP,
    Plan,
    execute_copy_move,
    execute_delete,
    plan_copy_move,
)


def make_entry(path: str) -> FileEntry:
    st = os.lstat(path)
    return FileEntry(
        name=os.path.basename(path),
        path=path,
        is_dir=os.path.isdir(path),
        is_link=os.path.islink(path),
        size=0 if os.path.isdir(path) else st.st_size,
        mtime=st.st_mtime,
        mode=st.st_mode,
    )


def build_tree(root: str) -> None:
    os.makedirs(os.path.join(root, "sub", "deep"))
    write(os.path.join(root, "a.txt"), "alpha")
    write(os.path.join(root, "b.log"), "beta" * 100)
    write(os.path.join(root, "sub", "c.txt"), "gamma")
    write(os.path.join(root, "sub", "deep", "d.bin"), "delta" * 1000)
    os.makedirs(os.path.join(root, "empty"))


def write(path: str, content: str) -> None:
    with open(path, "w") as f:
        f.write(content)


def tree_state(root: str) -> dict:
    state = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in dirnames + filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if os.path.islink(full):
                state[rel] = "link"
            elif os.path.isdir(full):
                state[rel] = "dir"
            else:
                st = os.stat(full)
                state[rel] = ("file", st.st_size, int(st.st_mtime), stat.S_IMODE(st.st_mode))
    return state


class BaseFsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_test_")
        self.src = os.path.join(self.tmp, "src")
        self.dst = os.path.join(self.tmp, "dst")
        os.makedirs(self.src)
        os.makedirs(self.dst)
        build_tree(self.src)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def noop_progress(_p):
        pass

    @staticmethod
    def not_cancelled():
        return False


class CopyTests(BaseFsTest):
    def test_copy_tree_preserves_contents_metadata(self):
        entries = scan_directory(self.src, show_hidden=False)
        plan = plan_copy_move(entries, self.dst, move=False)
        self.assertEqual(plan.total_files, 4)
        self.assertEqual(plan.conflicts, [])
        result = execute_copy_move(plan, move=False, policy=POLICY_OVERWRITE,
                                   progress_cb=self.noop_progress,
                                   is_cancelled=self.not_cancelled)
        self.assertEqual(result.errors, [])
        self.assertFalse(result.cancelled)
        self.assertEqual(result.done_files, 4)
        self.assertEqual(tree_state(self.src), tree_state(self.dst))

    def test_copy_conflict_policies(self):
        write(os.path.join(self.dst, "a.txt"), "DEST")
        entries = scan_directory(self.src, show_hidden=False)
        plan = plan_copy_move(entries, self.dst, move=False)
        self.assertEqual(len(plan.conflicts), 1)
        self.assertEqual(plan.conflicts[0][1], os.path.join(self.dst, "a.txt"))

        res = execute_copy_move(plan, move=False, policy=POLICY_SKIP,
                                progress_cb=self.noop_progress,
                                is_cancelled=self.not_cancelled)
        self.assertEqual(res.skipped, 1)
        with open(os.path.join(self.dst, "a.txt")) as f:
            self.assertEqual(f.read(), "DEST")

        res = execute_copy_move(plan, move=False, policy=POLICY_OVERWRITE,
                                progress_cb=self.noop_progress,
                                is_cancelled=self.not_cancelled)
        self.assertEqual(res.errors, [])
        with open(os.path.join(self.dst, "a.txt")) as f:
            self.assertEqual(f.read(), "alpha")

    def test_copy_preserves_exec_bit(self):
        path = os.path.join(self.src, "run.sh")
        write(path, "#!/bin/sh\n")
        os.chmod(path, 0o755)
        plan = plan_copy_move([make_entry(path)], self.dst, move=False)
        execute_copy_move(plan, move=False, policy=POLICY_OVERWRITE,
                          progress_cb=self.noop_progress, is_cancelled=self.not_cancelled)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.dst, "run.sh")).st_mode), 0o755)

    def test_copy_preserves_symlinks(self):
        file_link = os.path.join(self.src, "a_link")
        dir_link = os.path.join(self.src, "sub_link")
        os.symlink(os.path.join(self.src, "a.txt"), file_link)
        os.symlink(os.path.join(self.src, "sub"), dir_link)
        entries = scan_directory(self.src, show_hidden=False)
        plan = plan_copy_move(entries, self.dst, move=False)
        res = execute_copy_move(plan, move=False, policy=POLICY_OVERWRITE,
                                progress_cb=self.noop_progress,
                                is_cancelled=self.not_cancelled)
        self.assertEqual(res.errors, [])
        dst_fl = os.path.join(self.dst, "a_link")
        dst_dl = os.path.join(self.dst, "sub_link")
        self.assertTrue(os.path.islink(dst_fl))
        self.assertEqual(os.readlink(dst_fl), os.path.join(self.src, "a.txt"))
        self.assertTrue(os.path.islink(dst_dl))
        self.assertEqual(os.readlink(dst_dl), os.path.join(self.src, "sub"))

    def test_cancel_mid_copy(self):
        entries = scan_directory(self.src, show_hidden=False)
        plan = plan_copy_move(entries, self.dst, move=False)
        calls = {"n": 0}

        def cancel_after_two():
            calls["n"] += 1
            return calls["n"] > 2

        res = execute_copy_move(plan, move=False, policy=POLICY_OVERWRITE,
                                progress_cb=self.noop_progress, is_cancelled=cancel_after_two)
        self.assertTrue(res.cancelled)
        self.assertLess(res.done_files, 4)


class MoveTests(BaseFsTest):
    def test_move_same_fs_rename(self):
        plan = plan_copy_move([make_entry(os.path.join(self.src, "a.txt"))],
                              self.dst, move=True)
        self.assertTrue(plan.jobs[0].via_rename)
        res = execute_copy_move(plan, move=True, policy=POLICY_OVERWRITE,
                                progress_cb=self.noop_progress,
                                is_cancelled=self.not_cancelled)
        self.assertEqual(res.errors, [])
        self.assertFalse(os.path.lexists(os.path.join(self.src, "a.txt")))
        with open(os.path.join(self.dst, "a.txt")) as f:
            self.assertEqual(f.read(), "alpha")

    def test_move_tree(self):
        sub = os.path.join(self.src, "sub")
        before = tree_state(sub)
        plan = plan_copy_move([make_entry(sub)], self.dst, move=True)
        res = execute_copy_move(plan, move=True, policy=POLICY_OVERWRITE,
                                progress_cb=self.noop_progress,
                                is_cancelled=self.not_cancelled)
        self.assertEqual(res.errors, [])
        self.assertFalse(os.path.lexists(sub))
        self.assertEqual(before, tree_state(os.path.join(self.dst, "sub")))

    def test_move_conflict_skip(self):
        write(os.path.join(self.dst, "a.txt"), "DEST")
        plan = plan_copy_move([make_entry(os.path.join(self.src, "a.txt"))],
                              self.dst, move=True)
        self.assertEqual(len(plan.conflicts), 1)
        res = execute_copy_move(plan, move=True, policy=POLICY_SKIP,
                                progress_cb=self.noop_progress,
                                is_cancelled=self.not_cancelled)
        self.assertEqual(res.skipped, 1)
        self.assertTrue(os.path.lexists(os.path.join(self.src, "a.txt")))
        with open(os.path.join(self.dst, "a.txt")) as f:
            self.assertEqual(f.read(), "DEST")


class DeleteTests(BaseFsTest):
    def test_delete_tree_with_symlink(self):
        link = os.path.join(self.src, "a.txt")
        os.symlink(link, os.path.join(self.src, "a_link"))
        os.symlink(self.src, os.path.join(self.src, "dir_link"))
        entries = scan_directory(self.src, show_hidden=False)
        res = execute_delete(entries, progress_cb=self.noop_progress,
                             is_cancelled=self.not_cancelled)
        self.assertEqual(res.errors, [])
        self.assertEqual(os.listdir(self.src), [])
        self.assertGreaterEqual(res.done_files, 6)

    def test_delete_files_only(self):
        entries = [make_entry(os.path.join(self.src, "a.txt"))]
        res = execute_delete(entries, progress_cb=self.noop_progress,
                             is_cancelled=self.not_cancelled)
        self.assertEqual(res.errors, [])
        self.assertFalse(os.path.lexists(os.path.join(self.src, "a.txt")))
        self.assertTrue(os.path.isdir(self.src))

    def test_delete_cancel(self):
        entries = scan_directory(self.src, show_hidden=False)
        res = execute_delete(entries, progress_cb=self.noop_progress,
                             is_cancelled=lambda: True)
        self.assertTrue(res.cancelled)


class HumanSizeTests(unittest.TestCase):
    def test_units(self):
        self.assertEqual(human_size(0), "0 Б")
        self.assertEqual(human_size(1023), "1023 Б")
        self.assertTrue(human_size(1024).endswith("КиБ"))
        self.assertTrue(human_size(5 * 1024 * 1024).endswith("МиБ"))


class QtTestCase(unittest.TestCase):
    """Базовый класс для тестов, требующих QApplication."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        pass


class ModelTests(QtTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_model_")
        build_tree(self.tmp)
        from sphaera_commander.fsmodel import FileTableModel

        self.model = FileTableModel()
        self.model.reload(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_listing_with_dotdot(self):
        # 4 видимых записи: a.txt, b.log, empty, sub + '..'
        self.assertEqual(self.model.rowCount(), 5)
        self.assertEqual(self.model.data(self.model.index(0, 0)), "..")

    def test_hidden_filter(self):
        write(os.path.join(self.tmp, ".hidden"), "x")
        self.model.show_hidden = True
        self.model.reload()
        names = [self.model.entry_at(r).name for r in range(1, self.model.rowCount())]
        self.assertIn(".hidden", names)
        self.model.show_hidden = False
        self.model.reload()
        names = [self.model.entry_at(r).name for r in range(1, self.model.rowCount())]
        self.assertNotIn(".hidden", names)

    def test_dirs_first_sort(self):
        self.model.apply_sort(SIZE_COL, desc=True)
        kinds = [self.model.entry_at(r).is_dir
                 for r in range(1, self.model.rowCount())]
        self.assertEqual(kinds, sorted(kinds, reverse=True))
        files = [self.model.entry_at(r) for r in range(1, self.model.rowCount())
                 if not self.model.entry_at(r).is_dir]
        sizes = [f.size for f in files]
        self.assertEqual(sizes, sorted(sizes, reverse=True))

    def test_marks(self):
        self.model.toggle_mark(self.model.row_of_name("a.txt"))
        self.assertEqual({e.name for e in self.model.marked_entries()}, {"a.txt"})
        self.model.invert_marks()
        marked = {e.name for e in self.model.marked_entries()}
        self.assertEqual(marked, {"b.log"})
        n = self.model.mark_mask("*.log", True)
        self.assertEqual(n, 1)
        self.assertIn("b.log", self.model.marked)

    def test_summary(self):
        files, dirs, total, m_count, m_bytes = self.model.summary()
        self.assertEqual(files, 2)
        self.assertEqual(dirs, 2)
        self.assertGreater(total, 0)
        self.assertEqual(m_count, 0)

    def test_ext_column_display_and_sort(self):
        from sphaera_commander.fsmodel import EXT_COL, ext_of

        write(os.path.join(self.tmp, "заметка.md"), "# x\n")
        write(os.path.join(self.tmp, ".dotfile"), "x")
        write(os.path.join(self.tmp, "archive.tar.gz"), "x")
        self.model.reload()
        row = self.model.row_of_name("заметка.md")
        self.assertEqual(self.model.data(self.model.index(row, EXT_COL)), "md")
        row = self.model.row_of_name(".dotfile")
        self.assertEqual(self.model.data(self.model.index(row, EXT_COL)), "")
        row = self.model.row_of_name("sub")
        self.assertEqual(self.model.data(self.model.index(row, EXT_COL)), "")

        self.model.apply_sort(EXT_COL)
        entries = [self.model.entry_at(r)
                   for r in range(1, self.model.rowCount())]
        exts = [ext_of(e) for e in entries if not e.is_dir]
        self.assertEqual(exts, sorted(exts, key=str.lower))
        kinds = [e.is_dir for e in entries]
        self.assertEqual(kinds, sorted(kinds, reverse=True))


class PanelTests(QtTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_panel_")
        build_tree(self.tmp)
        self.tmp2 = tempfile.mkdtemp(prefix="sc_panel2_")
        from sphaera_commander.panel import FilePanel

        self.panel = FilePanel()
        self.panel.cd(self.tmp)
        self.panel.wait_loaded()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.tmp2, ignore_errors=True)

    def test_cd_and_up(self):
        self.assertEqual(self.panel.current_path(), self.tmp)
        self.panel.view.set_current_row(1)  # '..' всегда строка 0; 1 — первая запись
        entry = self.panel.current_entry()
        if entry and entry.is_dir:
            self.panel._on_entry_activated(entry)
            self.panel.wait_loaded()
            self.assertEqual(
                self.panel.current_path(), os.path.join(self.tmp, entry.name))
            self.panel.up()
            self.panel.wait_loaded()
            self.assertEqual(self.panel.current_path(), self.tmp)

    def test_selected_entries_fallback_to_cursor(self):
        self.panel.view.set_current_row(1)
        sel = self.panel.selected_entries()
        self.assertEqual(len(sel), 1)
        self.assertIsNotNone(sel[0])

    def test_select_mask(self):
        n = self.panel.select_mask("*.txt", True)
        self.assertGreaterEqual(n, 1)
        sel = self.panel.selected_entries()
        self.assertTrue(all(e.name.endswith(".txt") for e in sel))

    def test_enter_on_dotdot_goes_up(self):
        self.panel.cd(os.path.join(self.tmp, "sub"))
        self.panel.wait_loaded()
        self.panel._on_entry_activated(None)  # курсор на '..'
        self.panel.wait_loaded()
        self.assertEqual(self.panel.current_path(), self.tmp)

    def test_columns_fill_panel_width_after_reload(self):
        """«Имя» растягивается, сумма колонок равна ширине вьюпорта —
        в узком окне и после перезагрузки каталога (регрессия setModel)."""
        from PySide6.QtWidgets import QHeaderView

        from sphaera_commander.fsmodel import EXT_COL, NAME_COL

        self.panel.resize(1000, 600)
        self.panel.show()
        self.app.processEvents()
        v = self.panel.view
        header = v.horizontalHeader()
        self.assertIs(header.sectionResizeMode(NAME_COL),
                      QHeaderView.Stretch)
        self.assertIs(header.sectionResizeMode(EXT_COL), QHeaderView.Fixed)
        self.assertLessEqual(v.columnWidth(EXT_COL), 80)
        self.assertGreater(v.columnWidth(NAME_COL), 200)
        self.assertEqual(sum(v.columnWidth(i) for i in range(v.model().columnCount())),
                         v.viewport().width())
        # перезагрузка каталога не должна терять раскладку
        self.panel.refresh()
        self.panel.wait_loaded()
        self.assertIs(header.sectionResizeMode(NAME_COL), QHeaderView.Stretch)
        self.assertEqual(sum(v.columnWidth(i) for i in range(v.model().columnCount())),
                         v.viewport().width())
        self.panel.hide()

    def test_ctrl_click_toggles_mark_without_cursor_move(self):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        v = self.panel.view
        v.set_current_row(1)
        name = self.panel.model.entry_at(1).name
        idx = v.model().index(1, 0)
        rect = v.visualRect(idx)
        ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(rect.center()),
                         Qt.LeftButton, Qt.LeftButton, Qt.ControlModifier)
        v.mousePressEvent(ev)
        self.assertIn(name, self.panel.model.marked)
        self.assertEqual(v.currentIndex().row(), 1)
        ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(rect.center()),
                         Qt.LeftButton, Qt.LeftButton, Qt.ControlModifier)
        v.mousePressEvent(ev)
        self.assertNotIn(name, self.panel.model.marked)

    def test_name_tooltip_for_elided_names(self):
        from sphaera_commander.fsmodel import NAME_COL

        long_name = "очень_длинное_имя_файла_" + "х" * 60 + ".txt"
        write(os.path.join(self.tmp, long_name), "x")
        self.panel.resize(400, 500)
        self.panel.show()
        self.panel.refresh()
        self.panel.wait_loaded()
        self.app.processEvents()
        v = self.panel.view
        idx = v.model().index(v.model().row_of_name(long_name), NAME_COL)
        self.assertEqual(v.name_tooltip(idx), long_name)
        idx = v.model().index(v.model().row_of_name("a.txt"), NAME_COL)
        self.assertEqual(v.name_tooltip(idx), "")
        self.panel.hide()


class PlanTests(unittest.TestCase):
    def test_empty_source_list(self):
        plan = plan_copy_move([], tempfile.gettempdir(), move=False)
        self.assertIsInstance(plan, Plan)
        self.assertEqual(plan.jobs, [])


class PanelVfsTests(QtTestCase):
    """Вход в архив как в каталог (VFS-режим панели)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_vfs_")
        build_tree(self.tmp)
        import zipfile as zf_m

        self.arc = os.path.join(self.tmp, "arc.zip")
        entries = [make_entry(os.path.join(self.tmp, n))
                   for n in ("a.txt", "sub", "empty")]
        with zf_m.ZipFile(self.arc, "w", zf_m.ZIP_DEFLATED) as z:
            z.write(os.path.join(self.tmp, "a.txt"), "a.txt")
            z.write(os.path.join(self.tmp, "sub", "c.txt"), "sub/c.txt")
            z.write(os.path.join(self.tmp, "sub", "deep", "d.bin"),
                    "sub/deep/d.bin")
        from sphaera_commander.panel import FilePanel

        self.panel = FilePanel()
        self.panel.cd(self.tmp)
        self.panel.wait_loaded()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_enter_archive_and_navigate(self):
        # Enter на файле архива -> корень архива
        self.panel.cd(self.arc)
        self.panel.wait_loaded()
        self.assertTrue(self.panel.is_vfs)
        self.assertEqual(self.panel.current_path(), f"{self.arc}::")
        names = [self.panel.model.entry_at(r).name
                 for r in range(1, self.panel.model.row_count())]
        self.assertEqual(names, ["sub", "a.txt"])  # каталоги первыми

        # вход в подкаталог
        sub_row = next(r for r in range(1, self.panel.model.row_count())
                       if self.panel.model.entry_at(r).name == "sub")
        entry = self.panel.model.entry_at(sub_row)
        self.panel._on_entry_activated(entry)
        self.panel.wait_loaded()
        self.assertEqual(self.panel.current_path(), f"{self.arc}::sub")
        inner = [self.panel.model.entry_at(r).name
                 for r in range(1, self.panel.model.row_count())]
        self.assertEqual(inner, ["deep", "c.txt"])

        # up из подкаталога -> корень архива; up из корня -> каталог архива
        self.panel.up()
        self.panel.wait_loaded()
        self.assertEqual(self.panel.current_path(), f"{self.arc}::")
        self.panel.up()
        self.panel.wait_loaded()
        self.assertFalse(self.panel.is_vfs)
        self.assertEqual(self.panel.current_path(), self.tmp)

    def test_extract_member_via_browser(self):
        self.panel.cd(self.arc)
        self.panel.wait_loaded()
        browser = self.panel.vfs
        dest = os.path.join(self.tmp, "out")
        os.makedirs(dest)
        res = browser.extract_members(["a.txt"], dest, lambda *_: None,
                                      lambda: False)
        self.assertEqual(res.errors, [])
        self.assertTrue(os.path.isfile(os.path.join(dest, "a.txt")))


if __name__ == "__main__":
    unittest.main()

class CopyPathsTests(QtTestCase):
    """«Копировать полный путь» из контекстного меню."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_copy_")
        build_tree(self.tmp)
        from sphaera_commander.app import MainWindow

        self.win = MainWindow()
        self.win.left.cd(self.tmp)
        self.win.left.wait_loaded()
        self.win._set_active(self.win.left)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_copy_marked_paths(self):
        from PySide6.QtWidgets import QApplication

        self.win.left.model.set_mark("a.txt", True)
        self.win.left.model.set_mark("b.log", True)
        self.win._copy_paths(self.win.left)
        lines = QApplication.clipboard().text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn(os.path.join(self.tmp, "a.txt"), lines)
        self.assertIn(os.path.join(self.tmp, "b.log"), lines)

    def test_copy_cursor_fallback(self):
        from PySide6.QtWidgets import QApplication

        self.win.left.view.set_current_row(1)
        entry = self.win.left.model.entry_at(1)
        self.win._copy_paths(self.win.left)
        self.assertEqual(QApplication.clipboard().text(), entry.path)

    def test_copy_panel_path_when_cursor_on_dotdot(self):
        from PySide6.QtWidgets import QApplication

        self.win.left.view.set_current_row(0)  # '..'
        self.win._copy_paths(self.win.left)
        self.assertEqual(QApplication.clipboard().text(), self.tmp)


class FullscreenTests(QtTestCase):
    """F11: полноэкранный режим и выход из него."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from sphaera_commander.app import MainWindow

        cls.win = MainWindow()
        cls.win.show()

    def test_toggle_fullscreen_and_exit(self):
        w = self.win
        self.assertFalse(w.isFullScreen())
        self.assertTrue(w._corner_close.isHidden())
        w.toggle_fullscreen()
        self.assertTrue(w.isFullScreen())
        # меню остаётся видимым, закрывать можно угловым крестиком
        self.assertFalse(w.menuBar().isHidden())
        self.assertFalse(w._corner_close.isHidden())
        self.assertTrue(w.act_fullscreen.isChecked())
        w.toggle_fullscreen()
        self.assertFalse(w.isFullScreen())
        self.assertFalse(w.menuBar().isHidden())
        self.assertTrue(w._corner_close.isHidden())
        self.assertFalse(w.act_fullscreen.isChecked())

    def test_escape_exits_fullscreen_only(self):
        w = self.win
        w._exit_fullscreen()  # не в фулскрине — ничего не меняет
        self.assertFalse(w.isFullScreen())
        w.toggle_fullscreen()
        self.assertTrue(w.isFullScreen())
        w._exit_fullscreen()
        self.assertFalse(w.isFullScreen())
        self.assertFalse(w.menuBar().isHidden())
