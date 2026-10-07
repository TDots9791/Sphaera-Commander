"""Тесты партии 5: проверка по файлам сумм, архивные профили,
добавление серверов (rclone remote), статистика папки."""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import unittest.mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sc_m5_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

from sphaera_commander.plugins import archprofiles, checksums, dirstats  # noqa: E402
from sphaera_commander.plugins import remoteadd  # noqa: E402


def write(path, content="x"):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class VerifySumsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_vs_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_verify_against_sums(self):
        import hashlib

        good = write(os.path.join(self.tmp, "good.iso"), "данные")
        changed = write(os.path.join(self.tmp, "changed.iso"), "старое")
        with open(changed, "a", encoding="utf-8") as f:
            f.write("испорчено")
        unknown = write(os.path.join(self.tmp, "unknown.iso"), "без записи")
        sha = hashlib.sha256(open(good, "rb").read()).hexdigest()
        old_hash = hashlib.sha256("старое".encode("utf-8")).hexdigest()
        with open(os.path.join(self.tmp, "hashes.sha256"), "w",
                  encoding="utf-8") as f:
            f.write(f"{sha}  good.iso\n{old_hash}  changed.iso\n")
        ok, bad, unknown_list = checksums.verify_against_sums(
            [good, changed, unknown], self.tmp)
        self.assertEqual(ok, ["good.iso"])
        self.assertEqual([n for n, _w in bad], ["changed.iso"])
        self.assertEqual(unknown_list, ["unknown.iso"])

    def test_verify_report_status(self):
        from sphaera_commander.plugins.checksums import Plugin

        QApplication.instance() or QApplication([])
        tmp = self.tmp
        good = write(os.path.join(tmp, "good.iso"), "данные")
        with open(os.path.join(tmp, "hashes.sha256"), "w",
                  encoding="utf-8") as f:
            f.write(f"{hashlib.sha256(open(good, 'rb').read()).hexdigest()}  good.iso\n")
        plugin = Plugin.__new__(Plugin)
        plugin.app = unittest.mock.MagicMock()
        plugin.app.gui_call = unittest.mock.MagicMock(
            side_effect=lambda fn: fn())
        Plugin._verify_report(plugin, ([good], [], []))
        self.assertIn("все суммы совпали",
                      plugin.app._status.call_args[0][0])


class ArchProfilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_ap_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._old = archprofiles.PROFILES_FILE
        archprofiles.PROFILES_FILE = os.path.join(self.tmp, "profiles.json")

    def tearDown(self):
        archprofiles.PROFILES_FILE = self._old

    def test_profiles_roundtrip_and_pack(self):
        from sphaera_commander.fsmodel import FileEntry

        self.assertEqual(archprofiles.load_profiles(), [])
        archprofiles.save_profiles([
            {"name": "бэкап", "fmt": "zip", "excludes": ["*.log"]}])
        self.assertEqual(archprofiles.load_profiles()[0]["name"], "бэкап")

        write(os.path.join(self.tmp, "документ.txt"), "текст")
        write(os.path.join(self.tmp, "мусор.log"), "мусор")
        entries = []
        for name in ("документ.txt", "мусор.log"):
            path = os.path.join(self.tmp, name)
            entries.append(FileEntry(
                name=name, path=path, is_dir=False, is_link=False,
                size=os.path.getsize(path), mtime=0, mode=0o100644))
        from sphaera_commander.archives import pack_items

        result = pack_items(entries, os.path.join(self.tmp, "бэкап.zip"),
                            "zip", lambda p: None, lambda: False,
                            excludes=["*.log"])
        self.assertTrue(result.ok)
        import zipfile

        with zipfile.ZipFile(os.path.join(self.tmp, "бэкап.zip")) as zf:
            names = zf.namelist()
        self.assertIn("документ.txt", names)
        self.assertNotIn("мусор.log", names)


class RemoteAddTests(unittest.TestCase):
    def test_build_config_args_sftp(self):
        args = remoteadd.build_config_args(
            "sftp-server1", "sftp", "server1.lan", "2222", "mike",
            "obscured-pass", skip_host_key=True)
        self.assertEqual(args[:3], ["config", "create", "sftp-server1"])
        self.assertIn("sftp", args)
        self.assertIn("2222", args)
        self.assertEqual(
            args[args.index("host_key_override") + 1], "sphaera-trusted")

    def test_build_config_args_ftp(self):
        args = remoteadd.build_config_args(
            "ftp-хост", "ftp", "хост.lan", "21", "anon", "obscured",
            skip_host_key=True)
        self.assertNotIn("host_key_override", args)  # только для sftp
        self.assertIn("ftp", args)

    @unittest.skipUnless(shutil.which("rclone"),
                         "rclone не установлен на этой машине")
    def test_obscure_and_create_flow(self):
        app = QApplication.instance() or QApplication([])
        from PySide6.QtCore import QObject, Signal as Sig

        class Holder(QObject):
            gui_call = Sig(object)

        holder = Holder()
        holder.gui_call.connect(lambda fn: fn())
        holder._mount_cloud = unittest.mock.MagicMock()
        holder._status = lambda *_a, **_k: None
        holder.active = unittest.mock.MagicMock()
        dlg = remoteadd.RemoteDialog(None)
        dlg.app = holder
        dlg.type.setCurrentIndex(0)  # SFTP
        dlg.host.setText("server1.lan")
        dlg.user.setText("mike")
        dlg.port.setText("22")
        dlg.password.setText("пароль")
        created = {}

        class P:
            def __init__(self, rc, stdout=""):
                self.returncode, self.stdout, self.stderr = rc, stdout, ""

        def fake_run(args, **_kw):
            if args[1] == "obscure":
                created["obscure_src"] = args[2]
                return P(0, "obscured-pass\n") if args[2] == "пароль" \
                    else P(1)
            created["create_args"] = args
            return P(0)

        with unittest.mock.patch.object(
                remoteadd.subprocess, "run", side_effect=fake_run):
            dlg._create()
            deadline = time.monotonic() + 5
            while not holder._mount_cloud.called \
                    and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.02)
        self.assertEqual(created.get("obscure_src"), "пароль")
        self.assertIn("sftp-server1", created.get("create_args", []))
        holder._mount_cloud.assert_called_once()
        self.assertEqual(holder._mount_cloud.call_args[0][0], "sftp-server1:")


class DirStatsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_stats_dialog(self):
        tmp = tempfile.mkdtemp(prefix="sc_ds_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        os.makedirs(os.path.join(tmp, "папка"))
        write(os.path.join(tmp, "файл.txt"), "abc")
        write(os.path.join(tmp, "папка", "внутри.txt"), "abcdef")
        dlg = dirstats.DirStatsDialog(None, tmp)
        deadline = time.monotonic() + 5
        while "Всего" not in dlg.status.text() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.02)
        self.assertEqual(dlg.table.rowCount(), 2)
        names = {dlg.table.item(r, 0).text()
                 for r in range(dlg.table.rowCount())}
        self.assertEqual(names, {"файл.txt", "папка"})
        self.assertIn("2 файла", dlg.status.text())
        self.assertIn("1 папка", dlg.status.text())
        self.assertIn("9 Б", dlg.status.text())


if __name__ == "__main__":
    unittest.main()
