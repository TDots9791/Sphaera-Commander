"""Тесты VFS-браузера архивов и симлинков в zip."""

import os
import shutil
import stat as stat_m
import sys
import tarfile
import tempfile
import unittest
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sphaera_commander.archives import (  # noqa: E402
    ArchiveBrowser,
    pack_items,
    unpack_archive,
)
from sphaera_commander.fsmodel import FileEntry  # noqa: E402
from sphaera_commander.ops import ASK_SKIP  # noqa: E402


def make_entry(path: str) -> FileEntry:
    st = os.lstat(path)
    return FileEntry(name=os.path.basename(path), path=path,
                     is_dir=os.path.isdir(path), is_link=os.path.islink(path),
                     size=0 if os.path.isdir(path) else st.st_size,
                     mtime=st.st_mtime, mode=st.st_mode)


def noop_progress(_p):
    pass


def not_cancelled():
    return False


def build_tree(root: str) -> None:
    os.makedirs(os.path.join(root, "docs", "deep"))
    os.makedirs(os.path.join(root, "пусто"))
    with open(os.path.join(root, "a.txt"), "w") as f:
        f.write("alpha")
    with open(os.path.join(root, "docs", "b.md"), "w") as f:
        f.write("beta")
    with open(os.path.join(root, "docs", "deep", "c.bin"), "wb") as f:
        f.write(os.urandom(3000))
    with open(os.path.join(root, ".скрытый"), "w") as f:
        f.write("hid")


class ZipSymlinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_zl_")
        self.src = os.path.join(self.tmp, "src")
        self.dst = os.path.join(self.tmp, "dst")
        os.makedirs(self.src)
        os.makedirs(self.dst)
        build_tree(self.src)
        os.symlink("a.txt", os.path.join(self.src, "файл_link"))
        os.symlink("docs", os.path.join(self.src, "каталог_link"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_zip_preserves_symlinks(self):
        out = os.path.join(self.dst, "arc.zip")
        entries = [make_entry(os.path.join(self.src, n))
                   for n in ("a.txt", "docs", "файл_link", "каталог_link")]
        res = pack_items(entries, out, "zip", noop_progress, not_cancelled)
        self.assertEqual(res.errors, [])
        with zipfile.ZipFile(out) as zf:
            links = {i.filename for i in zf.infolist()
                     if i.create_system == 3
                     and stat_m.S_ISLNK(i.external_attr >> 16)}
        self.assertEqual(links, {"файл_link", "каталог_link"})

        dest = os.path.join(self.tmp, "unpacked")
        res = unpack_archive(out, dest, noop_progress, not_cancelled)
        self.assertEqual(res.errors, [])
        self.assertEqual(os.readlink(os.path.join(dest, "файл_link")), "a.txt")
        self.assertEqual(os.readlink(os.path.join(dest, "каталог_link")), "docs")

    def test_tar_preserves_symlinks(self):
        out = os.path.join(self.dst, "arc.tar.gz")
        entries = [make_entry(os.path.join(self.src, n)) for n in ("a.txt", "файл_link")]
        res = pack_items(entries, out, "tar.gz", noop_progress, not_cancelled)
        self.assertEqual(res.errors, [])
        with tarfile.open(out) as tf:
            m = tf.getmember("файл_link")
            self.assertTrue(m.issym())
            self.assertEqual(m.linkname, "a.txt")


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_br_")
        self.src = os.path.join(self.tmp, "src")
        build_tree(self.src)
        self.zip_path = os.path.join(self.tmp, "arc.zip")
        self.tgz_path = os.path.join(self.tmp, "arc.tar.gz")
        entries = [make_entry(os.path.join(self.src, n))
                   for n in ("a.txt", "docs", "пусто", ".скрытый")]
        pack_items(entries, self.zip_path, "zip", noop_progress, not_cancelled)
        pack_items(entries, self.tgz_path, "tar.gz", noop_progress, not_cancelled)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_list_root(self):
        b = ArchiveBrowser(self.zip_path)
        entries = b.list_dir("")
        names = [e.name for e in entries]
        # каталоги первыми: docs, пусто; затем a.txt; .скрытый отфильтрован
        self.assertEqual(names, ["docs", "пусто", "a.txt"])
        self.assertTrue(all(e.path.startswith(self.zip_path + "::") for e in entries))

    def test_list_nested_and_missing(self):
        b = ArchiveBrowser(self.zip_path)
        entries = b.list_dir("docs")
        self.assertEqual([e.name for e in entries], ["deep", "b.md"])
        nested = b.list_dir("docs/deep")
        self.assertEqual([e.name for e in nested], ["c.bin"])
        self.assertEqual(nested[0].size, 3000)
        with self.assertRaises(KeyError):
            b.list_dir("нет/такого")

    def test_hidden_filter(self):
        b = ArchiveBrowser(self.zip_path)
        entries = b.list_dir("", show_hidden=True)
        self.assertIn(".скрытый", [e.name for e in entries])

    def test_list_tar_matches_zip(self):
        bz = ArchiveBrowser(self.zip_path).list_dir("", show_hidden=True)
        bt = ArchiveBrowser(self.tgz_path).list_dir("", show_hidden=True)
        self.assertEqual([e.name for e in bz], [e.name for e in bt])

    def test_extract_selected_dir_recursive(self):
        b = ArchiveBrowser(self.zip_path)
        dest = os.path.join(self.tmp, "out")
        res = b.extract_members(["docs"], dest, noop_progress, not_cancelled)
        self.assertEqual(res.errors, [])
        self.assertTrue(os.path.isfile(os.path.join(dest, "docs", "deep", "c.bin")))

    def test_extract_with_ask_skip(self):
        b = ArchiveBrowser(self.zip_path)
        dest = os.path.join(self.tmp, "out")
        b.extract_members(["a.txt"], dest, noop_progress, not_cancelled)
        with open(os.path.join(dest, "a.txt"), "w") as f:
            f.write("MODIFIED")

        def ask(_info):
            return ASK_SKIP

        res = b.extract_members(["a.txt"], dest, noop_progress, not_cancelled,
                                ask_cb=ask)
        self.assertEqual(res.skipped, 1)
        with open(os.path.join(dest, "a.txt")) as f:
            self.assertEqual(f.read(), "MODIFIED")

    def test_extract_to_temp(self):
        b = ArchiveBrowser(self.zip_path)
        tmp_dir = os.path.join(self.tmp, "t")
        os.makedirs(tmp_dir)
        tmp = b.extract_member_to_temp("docs/b.md", tmp_dir)
        self.assertTrue(os.path.basename(tmp).endswith(".b.md"))
        with open(tmp) as f:
            self.assertEqual(f.read(), "beta")

    def test_delete_members_zip(self):
        b = ArchiveBrowser(self.zip_path)
        res = b.delete_members(["docs"])
        self.assertEqual(res.errors, [])
        names = b.member_names()
        self.assertNotIn("docs", names)
        self.assertNotIn("docs/b.md", names)
        self.assertNotIn("docs/deep/c.bin", names)
        self.assertIn("a.txt", names)
        # архив на месте корректный и открывается
        b2 = ArchiveBrowser(self.zip_path)
        self.assertIn("a.txt", [e.name for e in b2.list_dir("")])

    def test_delete_members_tar_gz(self):
        b = ArchiveBrowser(self.tgz_path)
        res = b.delete_members(["docs/deep"])
        self.assertEqual(res.errors, [])
        names = b.member_names()
        self.assertNotIn("docs/deep/c.bin", names)
        self.assertIn("docs/b.md", names)
        self.assertIn("пусто", names)

    def test_replace_member(self):
        b = ArchiveBrowser(self.zip_path)
        new_file = os.path.join(self.tmp, "new.md")
        with open(new_file, "w") as f:
            f.write("НОВОЕ СОДЕРЖИМОЕ")
        res = b.replace_member("docs/b.md", new_file)
        self.assertEqual(res.errors, [])
        dest = os.path.join(self.tmp, "out2")
        b.extract_members(["docs/b.md"], dest, noop_progress, not_cancelled)
        with open(os.path.join(dest, "docs", "b.md")) as f:
            self.assertEqual(f.read(), "НОВОЕ СОДЕРЖИМОЕ")

    def test_non_archive_rejected(self):
        with self.assertRaises(ValueError):
            ArchiveBrowser(os.path.join(self.src, "a.txt"))


if __name__ == "__main__":
    unittest.main()
