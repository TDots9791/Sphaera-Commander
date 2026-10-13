"""Тесты облачных дисков (rclone mount): ядро с моками + живой цикл yad:."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import urllib.request

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sphaera_commander import cloudmount  # noqa: E402


class CloudMountCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_cm_")
        self._old_root = cloudmount.MOUNT_ROOT
        cloudmount.MOUNT_ROOT = self.tmp
        with unittest.mock.patch.object(cloudmount, "rclone_bin",
                                        return_value="/usr/bin/rclone"):
            self.remote = "gdrive:"

    def tearDown(self):
        cloudmount.MOUNT_ROOT = self._old_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_title_and_names(self):
        self.assertEqual(cloudmount.title("yad:"), "Яндекс.Диск")
        self.assertEqual(cloudmount.title("gdrive:"), "Google Диск")
        self.assertEqual(cloudmount.title("mybox:"), "mybox")
        self.assertEqual(cloudmount.clean_name("yad:"), "yad")

    def test_is_cloud_path(self):
        inside = os.path.join(self.tmp, "yad", "docs")
        os.makedirs(inside)
        self.assertTrue(cloudmount.is_cloud_path(inside))
        self.assertFalse(cloudmount.is_cloud_path("/home/u/Документы"))

    @unittest.mock.patch.object(cloudmount.shutil, "which",
                                return_value="/fake/rclone")
    def test_mount_builds_command_and_marks_ours(self, _mock_which):
        # путь до rclone подменяется: тест проверяет сборку команды,
        # а не наличие утилиты (на раннерах её нет)
        calls = []

        class FakeProc:
            stderr = None

            def wait(self):
                calls.append("wait")
                return 0

        def fake_popen(cmd, **_kw):
            calls.append(cmd)
            # ismount станет истиной «после монтирования»
            os.makedirs(os.path.join(self.tmp, "gdrive"), exist_ok=True)
            self.addCleanup(shutil.rmtree,
                            os.path.join(self.tmp, "gdrive"),
                            ignore_errors=True)
            with unittest.mock.patch.object(os.path, "ismount",
                                            return_value=True):
                return FakeProc()

        # ismount: до fake_popen каталога нет (монтируем), после — есть
        with unittest.mock.patch.object(subprocess_popen(), "Popen",
                                        side_effect=fake_popen), \
             unittest.mock.patch.object(
                 os.path, "ismount",
                 side_effect=lambda p: os.path.isdir(
                     os.path.join(self.tmp, "gdrive"))):
            path = cloudmount.mount(self.remote)
        cmds = [c for c in calls if isinstance(c, list)]
        self.assertTrue(any("--vfs-cache-mode" in " ".join(c)
                            and "--daemon" in " ".join(c) for c in cmds))
        self.assertIn("gdrive", path)
        self.assertIn("gdrive", cloudmount._mounted_by_us)

    def test_mount_unreadable_cloud_raises_and_unmounts(self):
        # Яндкес-кейс 10.2026: mount поднимается, но листинг даёт I/O
        # ошибку — mount() обязан размонтировать и честно бросить RuntimeError
        class FakeProc:
            stderr = None

            def wait(self):
                return 0

        with unittest.mock.patch.object(subprocess_popen(), "Popen",
                                        return_value=FakeProc()), \
             unittest.mock.patch.object(os.path, "ismount",
                                        side_effect=[False, True]), \
             unittest.mock.patch.object(os, "scandir",
                                        side_effect=OSError("input/output error")), \
             unittest.mock.patch.object(cloudmount, "unmount") as mock_umd:
            with self.assertRaises(RuntimeError) as cm:
                cloudmount.mount(self.remote)
        self.assertIn("не читается", str(cm.exception))
        self.assertIn("input/output error", str(cm.exception))
        mock_umd.assert_called_once()

    def test_primary_remotes_yandex_dedupe(self):
        # yandex: и yad: — один Яндекс.Диск; интерфейс работает с yandex:
        self.assertEqual(
            cloudmount.primary_remotes(["yad:", "yandex:", "gdrive:"]),
            ["yandex:", "gdrive:"])
        self.assertEqual(cloudmount.primary_remotes(["yad:", "gdrive:"]),
                         ["yad:", "gdrive:"])

    def test_has_client_id_from_config_dump(self):
        import json

        class FakeProc:
            def __init__(self, out):
                self.stdout = out

        dump = json.dumps({"yandex": {"type": "yandex", "client_id": "abc"},
                           "yad": {"type": "yandex"}})
        with unittest.mock.patch.object(cloudmount, "rclone_bin",
                                        return_value="/usr/bin/rclone"), \
             unittest.mock.patch.object(cloudmount.subprocess, "run",
                                        return_value=FakeProc(dump)):
            self.assertTrue(cloudmount.has_client_id("yandex:"))
            self.assertFalse(cloudmount.has_client_id("yad:"))
            client_id, secret = cloudmount.credentials_of("yandex:")
            self.assertEqual((client_id, secret), ("abc", ""))
            client_id, secret = cloudmount.credentials_of("yad:")
            self.assertEqual((client_id, secret), ("", ""))

    def test_exchange_code_writes_token(self):
        # схема verification_code: код -> /token -> токен в конфиг remote
        import json

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"access_token": "tok",
                                   "refresh_token": "ref",
                                   "expires_in": 3600}).encode()

        class FakeProc:
            def __init__(self, out=None):
                self.stdout = out

        dump = json.dumps({"yandex": {"type": "yandex", "client_id": "cid",
                                      "client_secret": "csec"}})
        with unittest.mock.patch.object(cloudmount, "rclone_bin",
                                        return_value="/usr/bin/rclone"), \
             unittest.mock.patch.object(cloudmount.subprocess, "run",
                                        side_effect=[FakeProc(dump),
                                                     FakeProc(dump),
                                                     FakeProc()]) as mock_run, \
             unittest.mock.patch.object(urllib.request, "urlopen",
                                        return_value=FakeResp()):
            cloudmount.exchange_code("yandex:", "код")
        token_write = [c for c in mock_run.call_args_list
                       if c.args and "token" in c.args[0]][0]
        self.assertEqual(token_write.args[0][:3],
                         ["/usr/bin/rclone", "config", "update"])
        token = json.loads(token_write.args[0][-1])
        self.assertEqual(token["access_token"], "tok")
        self.assertEqual(token["refresh_token"], "ref")

    def test_set_client_credentials_command(self):
        class FakeProc:
            returncode = 0

        with unittest.mock.patch.object(cloudmount, "rclone_bin",
                                        return_value="/usr/bin/rclone"), \
             unittest.mock.patch.object(cloudmount.subprocess,
                                        "run") as mock_run:
            mock_run.return_value = FakeProc()
            cloudmount.set_client_credentials("yandex:", "cid", "csec")
        # config dump (есть ли секция) идёт первым; update — последний
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[:3], ["/usr/bin/rclone", "config", "update"])
        for token in ("yandex", "client_id", "cid", "client_secret", "csec"):
            self.assertIn(token, cmd)

    def test_mount_failure_raises(self):
        class FakeProc:
            stderr = None

            def wait(self):
                return 1

        with unittest.mock.patch.object(subprocess_popen(), "Popen",
                                        return_value=FakeProc()), \
             unittest.mock.patch.object(os.path, "ismount",
                                        return_value=False):
            with self.assertRaises(RuntimeError):
                cloudmount.mount(self.remote)

    def test_unmount_ours(self):
        cloudmount._mounted_by_us.add("yad")
        try:
            with unittest.mock.patch.object(cloudmount, "is_mounted",
                                            return_value=True), \
                 unittest.mock.patch.object(cloudmount.shutil, "which",
                                            return_value="/usr/bin/fusermount3"), \
                 unittest.mock.patch.object(
                     cloudmount.subprocess, "run") as mock_run:
                cloudmount.unmount_ours()
            self.assertTrue(any(isinstance(c[0][0], list)
                                and "-uz" in c[0][0]
                                for c in mock_run.call_args_list))
        finally:
            cloudmount._mounted_by_us.discard("yad")


def subprocess_popen():
    import subprocess

    return subprocess


class CloudMountLiveTests(unittest.TestCase):
    """Живой цикл на yad: — пропускается без rclone/remote/FUSE."""

    def setUp(self):
        if cloudmount.rclone_bin() is None:
            self.skipTest("rclone не установлен")
        if not os.path.exists("/dev/fuse"):
            self.skipTest("нет /dev/fuse")
        if "yad:" not in cloudsync_remotes():
            self.skipTest("remote yad: не настроен")
        # страховка: не лезем в чужие монтирования
        if cloudmount.is_mounted("yad:"):
            self.skipTest("yad: уже смонтирован вне теста")
        # страховка от сломанного облака (Яндекс 10.2026: 410 на листинг
        # для токенов rclone-приложения — серверное ограничение)
        probe = subprocess.run(
            [cloudmount.rclone_bin() or "rclone", "lsd", "yad:"],
            capture_output=True, timeout=60)
        if probe.returncode != 0:
            self.skipTest("yad: не читается (облако недоступно)")

    def test_mount_list_unmount(self):
        path = cloudmount.mount("yad:")
        self.assertTrue(cloudmount.is_mounted("yad:"))
        self.assertTrue(os.path.isdir(path))
        # в смонтированном каталоге можно читать список (содержимое может
        # быть пустым — важно, что монтирование отвечает)
        os.listdir(path)
        cloudmount.unmount("yad:")
        self.assertFalse(cloudmount.is_mounted("yad:"))
        self.assertNotIn("yad", cloudmount._mounted_by_us)


class WindowsMountTests(unittest.TestCase):
    """Windows-ветка cloudmount: диск-буква через WinFsp, state.json,
    размонтирование завершением процесса. Ветка включается подменой
    IS_WINDOWS, реального монтирования нет — только механика."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_winmnt_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        data_root = os.path.join(self.tmp, "data")
        for patch in (
            unittest.mock.patch.object(cloudmount, "IS_WINDOWS", True),
            unittest.mock.patch.object(cloudmount, "_data_root",
                                       return_value=data_root),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _fake_rclone(self, proc):
        return unittest.mock.patch.object(
            cloudmount.subprocess, "Popen",
            unittest.mock.Mock(return_value=proc, side_effect=None))

    def test_mount_builds_windows_command_and_state(self):
        # целевой каталог «диска» существует заранее — монтирование сразу
        # готово (в жизни его создаёт WinFsp)
        target = os.path.join(self.tmp, "Z:")
        os.makedirs(target)
        calls = {}

        def fake_popen(cmd, **kw):
            calls["cmd"] = cmd
            return unittest.mock.Mock(pid=4321, poll=lambda: None)

        with unittest.mock.patch.object(cloudmount, "_pick_letter",
                                        return_value=target), \
                unittest.mock.patch.object(cloudmount.subprocess, "Popen",
                                           side_effect=fake_popen):
            got = cloudmount.mount("yandex:")
        self.assertEqual(got, target)
        self.assertIn("--volname", calls["cmd"])
        self.assertNotIn("--daemon", calls["cmd"])  # на Windows его нет
        self.assertIn("yandex:", calls["cmd"])
        state = cloudmount._load_state()
        self.assertEqual(state["yandex"]["target"], target)
        self.assertEqual(state["yandex"]["pid"], 4321)
        self.assertTrue(cloudmount.is_mounted("yandex:"))
        self.assertEqual(cloudmount.mountpoint("yandex:"), target)

    @unittest.skipUnless(os.name == "nt",
                         "буквы дисков и splitdrive существуют только на Windows")
    def test_windows_path_recognition(self):
        target = "Z:\\"
        state = cloudmount._load_state()
        state["yandex"] = {"target": target, "pid": 1}
        cloudmount._save_state(state)
        inside = os.path.join(target, "dir", "f.docx")
        self.assertTrue(cloudmount.is_mounted("yandex:"))
        self.assertTrue(cloudmount.is_cloud_path(inside))
        self.assertEqual(cloudmount.remote_of_path(inside),
                         "yandex:dir/f.docx")
        self.assertFalse(cloudmount.is_cloud_path("C:\\Windows"))

    def test_unmount_kills_process_and_clears_state(self):
        target = os.path.join(self.tmp, "Y:")
        os.makedirs(target)
        state = cloudmount._load_state()
        state["yad"] = {"target": target, "pid": 999}
        cloudmount._save_state(state)
        kills = []

        def fake_run(cmd, **kw):
            kills.append(cmd)
            os.rmdir(target)  # WinFsp «убрал» диск вслед за процессом

        with unittest.mock.patch.object(cloudmount.subprocess, "run",
                                        side_effect=fake_run):
            cloudmount.unmount("yad:")
        self.assertIn("/PID", kills[0])
        self.assertEqual(kills[0][2], "999")
        self.assertNotIn("yad", cloudmount._load_state())
        self.assertFalse(cloudmount.is_mounted("yad:"))

    def test_pick_letter_skips_existing_drives(self):
        # «занятость» буквы проверяется по реальной файловой системе —
        # подменяем exists: D: есть, C: нет
        def fake_exists(path):
            return path.upper() == "D:" + os.sep

        with unittest.mock.patch.object(cloudmount, "_DRIVE_LETTERS", "DC"), \
                unittest.mock.patch.object(os.path, "exists", fake_exists):
            self.assertEqual(cloudmount._pick_letter({}), "C:" + os.sep)
            with unittest.mock.patch.object(
                    os.path, "exists", lambda p: True), \
                    self.assertRaises(RuntimeError):
                cloudmount._pick_letter({})

    def test_rclone_bin_probes_windows_locations(self):
        # первый кандидат — shim winget в %LOCALAPPDATA%
        base = os.path.join(self.tmp, "loc")
        probe = os.path.join(base, "Microsoft", "WinGet", "Links",
                             "rclone.exe")
        os.makedirs(os.path.dirname(probe))
        open(probe, "wb").close()

        with unittest.mock.patch("os.name", "nt"), \
                unittest.mock.patch.dict(
                    os.environ, {"LOCALAPPDATA": base}), \
                unittest.mock.patch.object(shutil, "which",
                                           return_value=None):
            self.assertEqual(cloudmount.rclone_bin(), probe)

    def test_set_client_credentials_creates_missing_section(self):
        runs = []

        def fake_run(cmd, **kw):
            runs.append(cmd)
            if cmd[1] == "config" and cmd[2] == "dump":
                return unittest.mock.Mock(returncode=0, stdout="{}")

            class P:
                returncode = 0
            return P()

        with unittest.mock.patch.object(cloudmount, "rclone_bin",
                                        return_value="/bin/rclone"), \
                unittest.mock.patch.object(cloudmount.subprocess, "run",
                                           side_effect=fake_run):
            cloudmount.set_client_credentials("yandex:", "id", "secret")
        # [0] — config dump, [1] — create недостающей секции, [2] — update
        self.assertEqual(runs[1][:3], ["/bin/rclone", "config", "create"])
        self.assertEqual(runs[1][3:5], ["yandex", "yandex"])
        self.assertEqual(runs[2][:3], ["/bin/rclone", "config", "update"])


def cloudsync_remotes():
    from sphaera_commander import cloudsync

    return cloudsync.rclone_remotes()


if __name__ == "__main__":
    unittest.main()
