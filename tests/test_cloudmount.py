"""Тесты облачных дисков (rclone mount): ядро с моками + живой цикл yad:."""

import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock

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

    def test_mount_builds_command_and_marks_ours(self):
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


def cloudsync_remotes():
    from sphaera_commander import cloudsync

    return cloudsync.rclone_remotes()


if __name__ == "__main__":
    unittest.main()
