"""Тесты облачной синхронизации: пары ya.d/sphaera, команды rclone, диалог."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import unittest.mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sc_cloud_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

from sphaera_commander import cloudsync  # noqa: E402


class CloudCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_cloud_core_")
        self._old = (cloudsync.YAD_CONFIG, cloudsync.SPHAERA_PAIRS_FILE,
                     cloudsync.LISTING_ROOT)
        cloudsync.YAD_CONFIG = os.path.join(self.tmp, "yad-pairs.json")
        cloudsync.SPHAERA_PAIRS_FILE = os.path.join(self.tmp, "sp-pairs.json")
        cloudsync.LISTING_ROOT = os.path.join(self.tmp, "listings")

    def tearDown(self):
        cloudsync.YAD_CONFIG, cloudsync.SPHAERA_PAIRS_FILE, \
            cloudsync.LISTING_ROOT = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_yad(self, pairs):
        with open(cloudsync.YAD_CONFIG, "w", encoding="utf-8") as f:
            json.dump({"pairs": pairs}, f, ensure_ascii=False)

    def test_yad_pairs_parse(self):
        self._write_yad([{"id": "a1", "local": "/home/u/Док", "remote": "Док",
                          "enabled": True},
                         {"id": "b2", "local": "/home/u/Карт",
                          "remote": "Карт", "enabled": False}])
        pairs = cloudsync.yad_pairs()
        self.assertEqual([p["id"] for p in pairs], ["a1", "b2"])
        self.assertEqual(pairs[1]["enabled"], False)
        # битый/отсутствующий файл — пусто, не исключение
        cloudsync.YAD_CONFIG = os.path.join(self.tmp, "нет-файла.json")
        self.assertEqual(cloudsync.yad_pairs(), [])

    def test_pairs_store_roundtrip(self):
        self.assertEqual(cloudsync.load_pairs(), [])
        pair = cloudsync.add_pair("/home/u/Проект", "gdrive:backup/Проект")
        self.assertEqual(cloudsync.load_pairs(), [pair])
        again = cloudsync.add_pair("/home/u/Проект", "gdrive:backup2")
        self.assertEqual([p["id"] for p in cloudsync.load_pairs()],
                         [again["id"]])  # та же локальная папка — замена
        cloudsync.remove_pair(again["id"])
        self.assertEqual(cloudsync.load_pairs(), [])

    def test_pair_for_dir_prefers_deepest(self):
        self._write_yad([{"id": "a1", "local": "/home/u", "remote": "u",
                          "enabled": True}])
        cloudsync.add_pair("/home/u/Проект", "gdrive:p")
        pair = cloudsync.pair_for_dir("/home/u/Проект/sub/dir")
        self.assertEqual(pair["source"], "sphaera")
        pair = cloudsync.pair_for_dir("/home/u/Другое")
        self.assertEqual(pair["source"], "yad")
        self.assertIsNone(cloudsync.pair_for_dir("/opt/чужое"))

    def test_sync_command_yad_uses_cli(self):
        with unittest.mock.patch.object(cloudsync, "yad_bin",
                                        return_value="/usr/bin/yad-sync"):
            cmd = cloudsync.sync_command({"source": "yad", "id": "a1"})
        self.assertEqual(cmd, ["/usr/bin/yad-sync", "sync", "a1"])

    def test_sync_command_rclone_first_and_next(self):
        pair = cloudsync.add_pair("/home/u/Проект", "gdrive:p")
        # первичная: листингов нет
        self.assertTrue(cloudsync.is_first_sync(pair["id"]))
        cmd = cloudsync.sync_command(dict(pair, source="sphaera"))
        self.assertIn("bisync", cmd)
        self.assertIn("--resync", cmd)
        self.assertIn("newer", cmd)
        self.assertIn("--resilient", cmd)
        self.assertIn(cloudsync.listing_dir(pair["id"]), " ".join(cmd))
        # после первого прогона появился листинг — повторная без --resync
        d = cloudsync.listing_dir(pair["id"])
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "L1.lst"), "w").close()
        self.assertFalse(cloudsync.is_first_sync(pair["id"]))
        cmd = cloudsync.sync_command(dict(pair, source="sphaera"))
        self.assertIn("--resilient", cmd)
        self.assertNotIn("--resync", cmd)

    def test_run_sync_streams_and_finishes(self):
        holder = {}
        lines, result = [], {}

        def done(rc):
            result["rc"] = rc

        with unittest.mock.patch.object(
                cloudsync, "sync_command",
                return_value=["sh", "-c", "echo строка-вывода"]):
            cloudsync.run_sync({"id": "x", "local": "/tmp", "remote": "r:",
                                "source": "sphaera"},
                               on_line=lines.append, done=done,
                               _proc_holder=holder)
            # ждать ВНУТРИ контекста мока: поток мог ещё не стартовать
            deadline = time.monotonic() + 5
            while "rc" not in result and time.monotonic() < deadline:
                time.sleep(0.02)
        self.assertEqual(result.get("rc"), 0)
        self.assertIn("строка-вывода", " ".join(lines))


class CloudDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_cloud_dlg_")
        self._old = (cloudsync.YAD_CONFIG, cloudsync.SPHAERA_PAIRS_FILE,
                     cloudsync.LISTING_ROOT)
        cloudsync.YAD_CONFIG = os.path.join(self.tmp, "yad.json")
        cloudsync.SPHAERA_PAIRS_FILE = os.path.join(self.tmp, "sp.json")
        cloudsync.LISTING_ROOT = os.path.join(self.tmp, "listings")
        with open(cloudsync.YAD_CONFIG, "w", encoding="utf-8") as f:
            json.dump({"pairs": [{"id": "a1", "local": "/home/u/Док",
                                  "remote": "Док", "enabled": True}]}, f)
        cloudsync.add_pair("/home/u/Проект", "gdrive:p")

    def tearDown(self):
        cloudsync.YAD_CONFIG, cloudsync.SPHAERA_PAIRS_FILE, \
            cloudsync.LISTING_ROOT = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dialog_lists_both_sources_and_gdrive_button(self):
        from sphaera_commander.dialogs import CloudSyncDialog

        with unittest.mock.patch.object(cloudsync, "rclone_remotes",
                                        return_value=["gdrive:", "yad:"]), \
             unittest.mock.patch.object(cloudsync, "rclone_bin",
                                        return_value="/usr/bin/rclone"):
            dlg = CloudSyncDialog(None, "/home/u/Проект")
            self.assertEqual(dlg.table.rowCount(), 2)
            self.assertEqual(dlg.table.item(0, 2).text(), "Ya.D")
            self.assertEqual(dlg.table.item(1, 2).text(), "rclone")
            self.assertTrue(dlg.btn_gdrive.isHidden())

    def test_dialog_shows_gdrive_button_without_remote(self):
        from sphaera_commander.dialogs import CloudSyncDialog

        with unittest.mock.patch.object(cloudsync, "rclone_remotes",
                                        return_value=["yad:"]), \
             unittest.mock.patch.object(cloudsync, "rclone_bin",
                                        return_value="/usr/bin/rclone"):
            dlg = CloudSyncDialog(None, "/home/u/Проект")
            self.assertFalse(dlg.btn_gdrive.isHidden())

    def test_dialog_starts_yad_sync(self):
        from sphaera_commander.dialogs import CloudSyncDialog

        with unittest.mock.patch.object(cloudsync, "rclone_remotes",
                                        return_value=["yad:"]), \
             unittest.mock.patch.object(cloudsync, "rclone_bin",
                                        return_value="/usr/bin/rclone"), \
             unittest.mock.patch.object(
                 cloudsync, "sync_command",
                 return_value=["sh", "-c", "echo ok"]) as mock_cmd, \
             unittest.mock.patch.object(cloudsync, "yad_bin",
                                        return_value="/usr/bin/yad-sync"):
            dlg = CloudSyncDialog(None, "/home/u/Док")
            pair = dlg.table.item(0, 0).data(
                __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.UserRole)
            dlg.start_sync(pair)
            deadline = time.monotonic() + 5
            while dlg._running_pair is not None and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.02)
            self.assertEqual(mock_cmd.call_args[0][0]["id"], "a1")
            self.assertIn("Готово", dlg.log.text())


if __name__ == "__main__":
    unittest.main()
