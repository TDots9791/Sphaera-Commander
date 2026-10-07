"""Тесты новых возможностей: ask-перезапись, архивы, групповое переименование,
просмотрщик, сравнение каталогов."""

import io
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
import unittest.mock
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sphaera_commander.archives import (  # noqa: E402
    archive_format,
    pack_items,
    unpack_archive,
)
from sphaera_commander.fsmodel import (  # noqa: E402
    FileEntry,
    compare_name_sets,
)
from sphaera_commander.ops import (  # noqa: E402
    ASK_OVERWRITE,
    ASK_OVERWRITE_ALL,
    ASK_SKIP,
    ASK_SKIP_ALL,
    ConflictInfo,
    execute_copy_move,
    plan_copy_move,
    POLICY_OVERWRITE,
    POLICY_SKIP,
)
from sphaera_commander.rename_tool import build_rename  # noqa: E402
from sphaera_commander.viewer import (  # noqa: E402
    detect_decode,
    hexdump,
    looks_binary,
)


def make_entry(path: str) -> FileEntry:
    st = os.lstat(path)
    return FileEntry(name=os.path.basename(path), path=path,
                     is_dir=os.path.isdir(path), is_link=os.path.islink(path),
                     size=0 if os.path.isdir(path) else st.st_size,
                     mtime=st.st_mtime, mode=st.st_mode)


def noop_progress(_p):
    pass


def write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def not_cancelled():
    return False


class AskOverwriteTests(unittest.TestCase):
    """Пофайловый запрос перезаписи (ask_cb) вместо bulk-политики."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_ask_")
        self.src = os.path.join(self.tmp, "src")
        self.dst = os.path.join(self.tmp, "dst")
        os.makedirs(self.src)
        os.makedirs(self.dst)
        for name in ("a.txt", "b.txt"):
            with open(os.path.join(self.src, name), "w") as f:
                f.write("SRC_" + name)
        for name in ("a.txt",):
            with open(os.path.join(self.dst, name), "w") as f:
                f.write("DST_" + name)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _copy(self, answers):
        calls = []

        def ask(info: ConflictInfo) -> str:
            calls.append(info.dst)
            return answers.pop(0)

        plan = plan_copy_move(
            [make_entry(os.path.join(self.src, n)) for n in ("a.txt", "b.txt")],
            self.dst, move=False)
        res = execute_copy_move(plan, move=False, policy=POLICY_OVERWRITE,
                                progress_cb=noop_progress,
                                is_cancelled=not_cancelled, ask_cb=ask)
        return res, calls

    def test_skip_one_overwrite_rest(self):
        res, asked = self._copy([ASK_SKIP])
        self.assertEqual(asked, [os.path.join(self.dst, "a.txt")])
        self.assertEqual(res.skipped, 1)
        self.assertEqual(res.done_files, 1)
        with open(os.path.join(self.dst, "a.txt")) as f:
            self.assertEqual(f.read(), "DST_a.txt")
        with open(os.path.join(self.dst, "b.txt")) as f:
            self.assertEqual(f.read(), "SRC_b.txt")

    def test_overwrite_all_shortcircuits(self):
        res, asked = self._copy([ASK_OVERWRITE_ALL])
        self.assertEqual(len(asked), 1)  # второй конфликт уже не спрашивается
        self.assertEqual(res.done_files, 2)
        with open(os.path.join(self.dst, "a.txt")) as f:
            self.assertEqual(f.read(), "SRC_a.txt")

    def test_cancel_on_ask(self):
        res, _asked = self._copy([ASK_SKIP_ALL])  # b.txt конфликта нет — skip_all хвост
        self.assertEqual(res.skipped, 1)

        # полная отмена
        def cancel_ask(_info):
            return "cancel"

        plan = plan_copy_move([make_entry(os.path.join(self.src, "a.txt"))],
                              self.dst, move=False)
        res = execute_copy_move(plan, move=False, policy=POLICY_OVERWRITE,
                                progress_cb=noop_progress,
                                is_cancelled=not_cancelled, ask_cb=cancel_ask)
        self.assertTrue(res.cancelled)
        with open(os.path.join(self.dst, "a.txt")) as f:
            self.assertEqual(f.read(), "DST_a.txt")

    def test_move_onto_existing_file_with_ask(self):
        plan = plan_copy_move([make_entry(os.path.join(self.src, "a.txt"))],
                              self.dst, move=True)
        self.assertEqual(len(plan.conflicts), 1)

        def ask(_info):
            return ASK_OVERWRITE

        res = execute_copy_move(plan, move=True, policy=POLICY_OVERWRITE,
                                progress_cb=noop_progress, is_cancelled=not_cancelled,
                                ask_cb=ask)
        self.assertEqual(res.errors, [])
        self.assertFalse(os.path.lexists(os.path.join(self.src, "a.txt")))
        with open(os.path.join(self.dst, "a.txt")) as f:
            self.assertEqual(f.read(), "SRC_a.txt")

    def test_move_onto_existing_file_skip(self):
        plan = plan_copy_move([make_entry(os.path.join(self.src, "a.txt"))],
                              self.dst, move=True)

        def ask(_info):
            return ASK_SKIP

        res = execute_copy_move(plan, move=True, policy=POLICY_OVERWRITE,
                                progress_cb=noop_progress, is_cancelled=not_cancelled,
                                ask_cb=ask)
        self.assertEqual(res.skipped, 1)
        self.assertTrue(os.path.lexists(os.path.join(self.src, "a.txt")))

    def test_bulk_policy_still_works_without_ask(self):
        with open(os.path.join(self.dst, "b.txt"), "w") as f:
            f.write("DST_b.txt")
        plan = plan_copy_move(
            [make_entry(os.path.join(self.src, n)) for n in ("a.txt", "b.txt")],
            self.dst, move=False)
        res = execute_copy_move(plan, move=False, policy=POLICY_SKIP,
                                progress_cb=noop_progress, is_cancelled=not_cancelled)
        self.assertEqual(res.skipped, 2)
        self.assertEqual(res.done_files, 0)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_arc_")
        self.src = os.path.join(self.tmp, "src")
        self.dst = os.path.join(self.tmp, "dst")
        os.makedirs(os.path.join(self.src, "sub"))
        os.makedirs(self.dst)
        with open(os.path.join(self.src, "a.txt"), "w") as f:
            f.write("alpha")
        with open(os.path.join(self.src, "sub", "b.bin"), "wb") as f:
            f.write(os.urandom(5000))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_format_detection(self):
        self.assertEqual(archive_format("x.ZIP"), "zip")
        self.assertEqual(archive_format("x.tar.gz"), "tar")
        self.assertEqual(archive_format("x.tgz"), "tar")
        # 0.19.0: .7z/.rar распознаются (внешние утилиты 7z/unrar)
        self.assertEqual(archive_format("x.7z"), "7z")
        self.assertEqual(archive_format("x.rar"), "rar")
        self.assertIsNone(archive_format("x.zip.txt"))

    def _entries(self):
        return [make_entry(os.path.join(self.src, n)) for n in ("a.txt", "sub")]

    def test_pack_unpack_zip_roundtrip(self):
        out = os.path.join(self.dst, "arc.zip")
        res = pack_items(self._entries(), out, "zip", noop_progress, not_cancelled)
        self.assertEqual(res.errors, [])
        # a.txt + docs(dir) + sub/b.bin
        self.assertEqual(res.done_files, 3)
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
        self.assertLessEqual({"a.txt", "sub/b.bin", "sub/"}, names)

        dest = os.path.join(self.tmp, "unpacked")
        res = unpack_archive(out, dest, noop_progress, not_cancelled)
        self.assertEqual(res.errors, [])
        with open(os.path.join(dest, "sub", "b.bin"), "rb") as f:
            self.assertEqual(len(f.read()), 5000)

    def test_pack_unpack_tar_gz_roundtrip(self):
        out = os.path.join(self.dst, "arc.tar.gz")
        res = pack_items(self._entries(), out, "tar.gz", noop_progress, not_cancelled)
        self.assertEqual(res.errors, [])
        dest = os.path.join(self.tmp, "unpacked")
        res = unpack_archive(out, dest, noop_progress, not_cancelled)
        self.assertEqual(res.errors, [])
        self.assertTrue(os.path.isfile(os.path.join(dest, "sub", "b.bin")))

    def test_unpack_overwrite_ask_skip(self):
        out = os.path.join(self.dst, "arc.zip")
        pack_items(self._entries(), out, "zip", noop_progress, not_cancelled)
        dest = os.path.join(self.tmp, "unpacked")
        unpack_archive(out, dest, noop_progress, not_cancelled)
        with open(os.path.join(dest, "a.txt"), "w") as f:
            f.write("MODIFIED")

        def ask(_info):
            return ASK_SKIP

        res = unpack_archive(out, dest, noop_progress, not_cancelled, ask_cb=ask)
        self.assertEqual(res.skipped, 2)  # a.txt и sub/b.bin уже существуют
        with open(os.path.join(dest, "a.txt")) as f:
            self.assertEqual(f.read(), "MODIFIED")

        answers = [ASK_OVERWRITE_ALL]

        def ask_all(_info):
            return answers.pop(0)

        res = unpack_archive(out, dest, noop_progress, not_cancelled, ask_cb=ask_all)
        self.assertEqual(res.errors, [])
        with open(os.path.join(dest, "a.txt")) as f:
            self.assertEqual(f.read(), "alpha")

    def test_zip_slip_rejected(self):
        evil = os.path.join(self.dst, "evil.zip")
        with zipfile.ZipFile(evil, "w") as zf:
            info = zipfile.ZipInfo("../evil.txt")
            zf.writestr(info, "bad")
            zf.writestr("ok.txt", "good")
        dest = os.path.join(self.tmp, "unpacked")
        res = unpack_archive(evil, dest, noop_progress, not_cancelled)
        self.assertEqual(len(res.errors), 1)
        self.assertIn("zip-slip", res.errors[0].message)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.txt")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "ok.txt")))

    def test_tar_absolute_member_sanitized(self):
        evil = os.path.join(self.dst, "evil.tar")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            data = b"bad"
            info = tarfile.TarInfo(name="/tmp/absolute_evil.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
            data2 = b"good"
            info2 = tarfile.TarInfo(name="ok.txt")
            info2.size = len(data2)
            tf.addfile(info2, io.BytesIO(data2))
        buf.seek(0)
        with open(evil, "wb") as f:
            f.write(buf.read())
        dest = os.path.join(self.tmp, "unpacked")
        res = unpack_archive(evil, dest, noop_progress, not_cancelled)
        self.assertEqual(len(res.errors), 1)
        self.assertFalse(os.path.exists("/tmp/absolute_evil.txt"))
        self.assertTrue(os.path.isfile(os.path.join(dest, "ok.txt")))

    def test_cancelled_unpack(self):
        out = os.path.join(self.dst, "arc.zip")
        pack_items(self._entries(), out, "zip", noop_progress, not_cancelled)
        res = unpack_archive(out, self.tmp + "/u2", noop_progress, lambda: True)
        self.assertTrue(res.cancelled)


class RenameToolTests(unittest.TestCase):
    def test_keep_name(self):
        pairs = build_rename(["a.txt", "b.txt"], "*")
        self.assertEqual(pairs, [("a.txt", "a"), ("b.txt", "b")])

    def test_counter_with_padding_and_ext(self):
        pairs = build_rename(["one.TXT", "two.TXT", "three.TXT"], "photo_[N03].[E]")
        self.assertEqual([p[1] for p in pairs],
                         ["photo_001.TXT", "photo_002.TXT", "photo_003.TXT"])

    def test_start_step(self):
        pairs = build_rename(["a", "b"], "f[N]", start=10, step=5)
        self.assertEqual([p[1] for p in pairs], ["f10", "f15"])

    def test_duplicate_rejected(self):
        with self.assertRaises(ValueError):
            build_rename(["a.txt", "b.txt"], "same")

    def test_empty_template_rejected(self):
        with self.assertRaises(ValueError):
            build_rename(["a"], "")


class RenameDialogTests(unittest.TestCase):
    """F2: выделение имени без расширения — случайный ввод не сотрёт «.расш»."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_file_with_ext_selects_base(self):
        from PySide6.QtWidgets import QLineEdit

        from sphaera_commander.app import select_base_name

        le = QLineEdit()
        le.setText("отчёт.docx")
        select_base_name(le, "отчёт.docx", is_dir=False)
        self.assertEqual(le.selectedText(), "отчёт")

    def test_multi_dot_selects_all_but_last_suffix(self):
        from PySide6.QtWidgets import QLineEdit

        from sphaera_commander.app import select_base_name

        le = QLineEdit()
        le.setText("archive.tar.gz")
        select_base_name(le, "archive.tar.gz", is_dir=False)
        self.assertEqual(le.selectedText(), "archive.tar")

    def test_no_ext_selects_all(self):
        from PySide6.QtWidgets import QLineEdit

        from sphaera_commander.app import select_base_name

        le = QLineEdit()
        le.setText("Makefile")
        select_base_name(le, "Makefile", is_dir=False)
        self.assertEqual(le.selectedText(), "Makefile")

    def test_dir_selects_all(self):
        from PySide6.QtWidgets import QLineEdit

        from sphaera_commander.app import select_base_name

        le = QLineEdit()
        le.setText("моя.папка")
        select_base_name(le, "моя.папка", is_dir=True)
        self.assertEqual(le.selectedText(), "моя.папка")

    def test_make_rename_dialog(self):
        from PySide6.QtWidgets import QLineEdit

        from sphaera_commander.app import make_rename_dialog

        entry = FileEntry(name="док.pdf", path="/tmp/док.pdf", is_dir=False,
                          is_link=False, size=1, mtime=0.0, mode=0o100644)
        dlg = make_rename_dialog(None, entry)
        le = dlg.findChild(QLineEdit)
        self.assertIsNotNone(le)
        self.assertEqual(le.text(), "док.pdf")
        self.assertEqual(le.selectedText(), "док")


class ViewerHelperTests(unittest.TestCase):
    def test_detect_utf8(self):
        text, enc = detect_decode("привет".encode("utf-8"))
        self.assertEqual(text, "привет")
        self.assertEqual(enc, "utf-8")

    def test_detect_cp1251(self):
        raw = "привет".encode("cp1251")
        text, enc = detect_decode(raw)
        self.assertEqual(text, "привет")
        self.assertEqual(enc, "cp1251")

    def test_detect_utf16_bom(self):
        text, enc = detect_decode("привет".encode("utf-16"))
        self.assertEqual(text, "привет")
        self.assertEqual(enc, "utf-16")

    def test_binary_detection(self):
        self.assertFalse(looks_binary("plain text, немного текста".encode("utf-8")))
        self.assertTrue(looks_binary(b"\x00\x01\x02\x00\x03abc\x00"))
        self.assertTrue(looks_binary(os.urandom(8192)))  # ~10% контрольных байтов

    def test_hexdump_format(self):
        out = hexdump(bytes(range(20)))
        lines = out.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("00000000  00 01 02"))
        self.assertIn("|", lines[0])


class CompareTests(unittest.TestCase):
    def test_compare_name_sets(self):
        e = lambda n, s, m: FileEntry(n, "/x/" + n, False, False, s, m, 0o644)
        left = {"a": e("a", 10, 100.0), "b": e("b", 20, 200.0), "d": e("d", 5, 50.0)}
        right = {"a": e("a", 10, 100.0), "b": e("b", 99, 200.0), "c": e("c", 1, 10.0)}
        dl, dr = compare_name_sets(left, right)
        self.assertEqual(dl, {"b", "d"})   # b отличается, d отсутствует справа
        self.assertEqual(dr, {"b", "c"})


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(os.name == "posix",
                     "gio-корзина — POSIX; на Windows корзину делает "
                     "win64.platform.trash (тесты в репо Win64)")
class TrashTests(unittest.TestCase):
    """F8: удаление в корзину через gio trash."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_trash_")
        self.xdg = tempfile.mkdtemp(prefix="sc_trash_xdg_")
        self._old_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = self.xdg
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(self.src)
        write(os.path.join(self.src, "a.txt"), "alpha")
        os.makedirs(os.path.join(self.src, "sub"))
        write(os.path.join(self.src, "sub", "b.txt"), "beta")

    def tearDown(self):
        if self._old_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self._old_xdg
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.xdg, ignore_errors=True)

    def test_trash_invokes_gio_and_accounts(self):
        from sphaera_commander import ops as ops_mod
        from sphaera_commander.ops import execute_trash

        entries = [make_entry(os.path.join(self.src, n)) for n in ("a.txt", "sub")]
        calls = []

        def fake_run(cmd, **_kw):
            calls.append(cmd)
            return unittest.mock.MagicMock(returncode=0, stderr="")

        with unittest.mock.patch.object(ops_mod.subprocess, "run", side_effect=fake_run):
            res = execute_trash(entries, noop_progress, not_cancelled)
        self.assertEqual(res.errors, [])
        self.assertEqual(res.done_files, 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual([os.path.basename(c) for c in calls[0][:3]],
                         ["gio", "trash", "--"])
        self.assertEqual(calls[0][3], os.path.join(self.src, "a.txt"))

    def test_trash_gio_failure_recorded(self):
        from sphaera_commander import ops as ops_mod
        from sphaera_commander.ops import execute_trash

        entries = [make_entry(os.path.join(self.src, "a.txt"))]

        def fake_run(cmd, **_kw):
            return unittest.mock.MagicMock(returncode=1, stderr="отказано")

        with unittest.mock.patch.object(ops_mod.subprocess, "run", side_effect=fake_run):
            res = execute_trash(entries, noop_progress, not_cancelled)
        self.assertEqual(res.done_files, 0)
        self.assertEqual(res.errors[0].message, "отказано")

    def test_trash_real_gio(self):
        """Реальный gio trash; skip на монтированиях без поддержки корзины."""
        from sphaera_commander.ops import execute_trash

        entries = [make_entry(os.path.join(self.src, "a.txt"))]
        res = execute_trash(entries, noop_progress, not_cancelled)
        if res.errors and "не поддерживается" in res.errors[0].message:
            self.skipTest("gio запрещает корзину на этом монтировании (tmpfs)")
        self.assertEqual(res.errors, [], res.errors)
        # setUp кладёт ещё и sub/ — он в корзину не входил
        self.assertNotIn("a.txt", os.listdir(self.src))
        trash_files = os.path.join(self.xdg, "Trash", "files")
        self.assertTrue(os.path.isfile(os.path.join(trash_files, "a.txt")))
        info = os.path.join(self.xdg, "Trash", "info", "a.txt.trashinfo")
        self.assertTrue(os.path.isfile(info))
        self.assertIn("Path=", open(info).read())

    def test_trash_cancel_and_vfs_guard(self):
        from sphaera_commander.fsmodel import FileEntry
        from sphaera_commander.ops import execute_trash

        virtual = FileEntry("x", "/tmp/arc.zip::x", False, False, 1, 0.0, 0o644)
        entries = [make_entry(os.path.join(self.src, "a.txt")), virtual]
        res = execute_trash(entries, noop_progress, lambda: True)
        self.assertTrue(res.cancelled)
        self.assertTrue(os.path.isfile(os.path.join(self.src, "a.txt")))

    def test_trash_without_gio(self):
        from sphaera_commander import ops as ops_mod
        from sphaera_commander.ops import execute_trash

        entries = [make_entry(os.path.join(self.src, "a.txt"))]
        with unittest.mock.patch.object(ops_mod.shutil, "which", return_value=None):
            res = execute_trash(entries, noop_progress, not_cancelled)
        self.assertTrue(res.errors)
        self.assertIn("gio", res.errors[0].message)
        self.assertTrue(os.path.isfile(os.path.join(self.src, "a.txt")))


if __name__ == "__main__":
    unittest.main()
