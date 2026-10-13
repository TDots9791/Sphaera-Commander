"""Устойчивое копирование из облака: докачка (.part), повтор сбойных
файлов после основного прохода, запасные инструменты (зеркало/копto).

Запуск:  .venv/bin/python -m unittest discover -s tests -v
"""

import builtins
import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sphaera_commander import cloudmount, cloudsync, ops  # noqa: E402
from sphaera_commander.ops import (  # noqa: E402
    PART_SUFFIX,
    Job,
    Plan,
    execute_copy_move,
)

CONTENT = b"0123456789" * 100  # 1000 байт


def _plan_one(src: str, dst: str) -> Plan:
    plan = Plan()
    size = os.path.getsize(src)
    plan.jobs.append(Job(src=src, dst=dst, size=size, is_dir=False))
    plan.total_files = 1
    plan.total_bytes = size
    return plan


class _FlakyReader:
    """Обёртка файла: первые fail_times чтений падают после fail_after байт."""

    def __init__(self, f, fail_after: int, fail_times: int):
        self._f = f
        self._left = fail_after
        self._fails = fail_times

    def read(self, n):
        if self._fails > 0:
            if self._left <= 0:
                self._fails -= 1
                raise OSError(5, "Input/output error")
            data = self._f.read(min(n, self._left))
            self._left -= len(data)
            return data
        return self._f.read(n)

    def seek(self, *a):
        return self._f.seek(*a)

    def tell(self):
        return self._f.tell()

    def close(self):
        return self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return self._f.__exit__(*a)


class CopyResumableTests(unittest.TestCase):
    """Докачка на уровне функции _copy_file_resumable."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_resum_")
        self.src = os.path.join(self.tmp, "src.bin")
        with open(self.src, "wb") as f:
            f.write(CONTENT)
        os.makedirs(os.path.join(self.tmp, "out"))
        self.dst = os.path.join(self.tmp, "out", "dst.bin")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _progress(self):
        return ops._ProgressHelper(ops.Progress(
            phase="run", current="", done_files=0, total_files=1,
            done_bytes=0, total_bytes=len(CONTENT)), lambda s: None)

    def test_first_attempt_leaves_part_and_second_completes(self):
        real_open = builtins.open
        holder = {"n": 0}

        def flaky_open(file, mode="r", *a, **kw):
            if mode == "rb" and file == self.src and holder["n"] == 0:
                holder["n"] += 1
                return _FlakyReader(real_open(file, mode, *a, **kw),
                                    fail_after=400, fail_times=1)
            return real_open(file, mode, *a, **kw)

        helper = self._progress()
        with mock.patch("builtins.open", flaky_open):
            with self.assertRaises(OSError):
                ops._copy_file_resumable(self.src, self.dst, len(CONTENT),
                                         helper, [0], lambda: False)
        part = self.dst + PART_SUFFIX
        self.assertEqual(os.path.getsize(part), 400)  # недокачанные данные целы
        self.assertFalse(os.path.exists(self.dst))
        # вторая попытка: докачка с 400-го байта
        ops._copy_file_resumable(self.src, self.dst, len(CONTENT),
                                 helper, [400], lambda: False)
        self.assertFalse(os.path.exists(part))
        with open(self.dst, "rb") as f:
            self.assertEqual(f.read(), CONTENT)
        self.assertEqual(holder["n"], 1)  # источник открывался дважды, падал один

    def test_zero_size_file(self):
        src = os.path.join(self.tmp, "empty.bin")
        open(src, "wb").close()
        ops._copy_file_resumable(src, self.dst, 0, self._progress(), [0],
                                 lambda: False)
        self.assertTrue(os.path.isfile(self.dst))
        self.assertEqual(os.path.getsize(self.dst), 0)

    def test_size_mismatch_raises(self):
        # источник «короткий»: EOF раньше ожидаемого размера
        src = os.path.join(self.tmp, "short.bin")
        with open(src, "wb") as f:
            f.write(b"123")
        with self.assertRaises(OSError):
            ops._copy_file_resumable(src, self.dst, 100, self._progress(),
                                     [0], lambda: False)
        self.assertFalse(os.path.exists(self.dst))
        os.unlink(self.dst + PART_SUFFIX)  # за собой: тест чистит tmp целиком


class RetryPassTests(unittest.TestCase):
    """Повторный проход по сбойным облачным файлам и запасные инструменты."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_retry_")
        self.src = os.path.join(self.tmp, "src.bin")
        with open(self.src, "wb") as f:
            f.write(CONTENT)
        os.makedirs(os.path.join(self.tmp, "out"))
        self.dst = os.path.join(self.tmp, "out", "dst.bin")
        self._patches = []

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cloud(self, sleep_result: bool = False):
        self._patches = [
            mock.patch.object(ops, "_is_cloud_path", return_value=True),
            mock.patch.object(ops, "_sleep_cancel", return_value=sleep_result),
        ]
        for p in self._patches:
            p.start()

    def _run(self, cancelled=lambda: False):
        return execute_copy_move(
            _plan_one(self.src, self.dst), move=False,
            policy=ops.POLICY_OVERWRITE,
            progress_cb=lambda s: None,
            is_cancelled=cancelled)

    def test_transient_failure_recovered_by_retry(self):
        real_open = builtins.open
        holder = {"n": 0}

        def flaky_open(file, mode="r", *a, **kw):
            if mode == "rb" and file == self.src and holder["n"] == 0:
                holder["n"] += 1
                return _FlakyReader(real_open(file, mode, *a, **kw),
                                    fail_after=400, fail_times=1)
            return real_open(file, mode, *a, **kw)

        self._cloud()
        with mock.patch("builtins.open", flaky_open):
            res = self._run()
        self.assertTrue(res.ok)
        self.assertEqual(res.errors, [])
        self.assertEqual(res.done_files, 1)
        with open(self.dst, "rb") as f:
            self.assertEqual(f.read(), CONTENT)
        self.assertFalse(os.path.exists(self.dst + PART_SUFFIX))

    def test_fallback_recovers_and_reports(self):
        real_open = builtins.open

        def dead_open(file, mode="r", *a, **kw):
            if mode == "rb" and file == self.src:
                raise OSError(5, "Input/output error")
            return real_open(file, mode, *a, **kw)

        def fake_fallback(src, dst, expected, is_cancelled):
            with open(dst, "wb") as f:
                f.write(CONTENT)
            return True, "восстановлено из зеркала (тест)"

        self._cloud()
        with mock.patch("builtins.open", dead_open), \
                mock.patch.object(ops, "cloud_fallback", fake_fallback):
            res = self._run()
        self.assertEqual(res.errors, [])
        self.assertEqual(res.done_files, 1)
        self.assertEqual(len(res.recovered), 1)
        self.assertEqual(res.recovered[0].path, self.dst)
        self.assertIn("зеркала", res.recovered[0].message)
        with open(self.dst, "rb") as f:
            self.assertEqual(f.read(), CONTENT)

    def test_all_instruments_fail_lists_error(self):
        real_open = builtins.open

        def dead_open(file, mode="r", *a, **kw):
            if mode == "rb" and file == self.src:
                raise OSError(5, "Input/output error")
            return real_open(file, mode, *a, **kw)

        self._cloud()
        with mock.patch("builtins.open", dead_open), \
                mock.patch.object(ops, "cloud_fallback",
                                  lambda *a, **k: (False, "сервер молчит")):
            res = self._run()
        self.assertFalse(res.ok)
        self.assertEqual(res.done_files, 0)
        self.assertEqual(len(res.errors), 1)
        self.assertIn("запасные источники", res.errors[0].message)
        self.assertIn("сервер молчит", res.errors[0].message)

    def test_cancel_during_retry_pause(self):
        real_open = builtins.open

        def dead_open(file, mode="r", *a, **kw):
            if mode == "rb" and file == self.src:
                raise OSError(5, "Input/output error")
            return real_open(file, mode, *a, **kw)

        self._cloud(sleep_result=True)  # пауза «истекла с отменой»
        with mock.patch("builtins.open", dead_open):
            res = self._run()
        self.assertTrue(res.cancelled)
        self.assertEqual(res.errors, [])

    def test_zero_progress_goes_straight_to_fallback(self):
        # сбой на первой же порции: повторное чтение с нуля бессмысленно
        real_open = builtins.open
        calls = {"rb": 0}

        def dead_open(file, mode="r", *a, **kw):
            if mode == "rb" and file == self.src:
                calls["rb"] += 1
                raise OSError(5, "Input/output error")
            return real_open(file, mode, *a, **kw)

        self._cloud()
        with mock.patch("builtins.open", dead_open), \
                mock.patch.object(ops, "cloud_fallback",
                                  mock.Mock(return_value=(False, "нет"))):
            res = self._run()
        self.assertEqual(calls["rb"], 1)  # второго чтения с нуля не было
        self.assertEqual(len(res.errors), 1)

    def test_local_files_not_retried(self):
        # сбой локального файла — сразу ошибка, без повторов и запасных
        real_open = builtins.open

        def dead_open(file, mode="r", *a, **kw):
            if mode == "rb" and file == self.src:
                raise OSError(5, "Input/output error")
            return real_open(file, mode, *a, **kw)

        with mock.patch("builtins.open", dead_open), \
                mock.patch.object(ops, "cloud_fallback",
                                  mock.Mock(return_value=(True, "не должно вызываться"))) as fb:
            res = self._run()
        self.assertEqual(len(res.errors), 1)
        self.assertEqual(res.recovered, [])
        fb.assert_not_called()


class RemotePathAndMirrorTests(unittest.TestCase):
    """Отображение путей монтирования и поиск локального зеркала."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_remote_")
        self.mount_root = os.path.join(self.tmp, "mounts")
        os.makedirs(os.path.join(self.mount_root, "yandex",
                                 "Папка-пример", "01.Подкаталог"))
        patcher = mock.patch.object(cloudmount, "MOUNT_ROOT", self.mount_root)
        patcher.start()
        self.addCleanup(patcher.stop)
        # POSIX-раскладка путей (MOUNT_ROOT) закрепляется явно: на
        # windows-раннере ветка облака — буквы дисков, не этот каталог
        patcher = mock.patch.object(cloudmount, "IS_WINDOWS", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_remote_of_path(self):
        src = os.path.join(self.mount_root, "yandex",
                           "Папка-пример", "01.Подкаталог", "f.docx")
        self.assertEqual(
            cloudmount.remote_of_path(src),
            "yandex:Папка-пример/01.Подкаталог/f.docx")
        self.assertEqual(cloudmount.remote_of_path(
            os.path.join(self.mount_root, "yandex")), "yandex:")
        self.assertIsNone(cloudmount.remote_of_path(self.tmp))

    def test_mirror_for_remote_longest_prefix(self):
        sphaera = [{"id": "s1", "local": os.path.join(self.tmp, "mirror"),
                    "remote": "yandex:Папка-пример", "enabled": True}]
        yad = [{"id": "y1", "local": os.path.join(self.tmp, "yad"),
                "remote": "Папка-пример", "enabled": True}]
        with mock.patch.object(cloudsync, "load_pairs",
                               return_value=sphaera), \
                mock.patch.object(cloudsync, "yad_pairs", return_value=yad):
            self.assertEqual(
                cloudsync.mirror_for_remote(
                    "yandex:Папка-пример/01.Подкаталог/f.docx"),
                os.path.join(self.tmp, "mirror", "01.Подкаталог", "f.docx"))
            # 'yad' и 'yandex' — одно семейство: sphaera-пара 'yandex:'
            # отвечает и для запроса 'yad:' (порядок источников устойчив)
            self.assertEqual(
                cloudsync.mirror_for_remote("yad:Папка-пример/другой.pdf"),
                os.path.join(self.tmp, "mirror", "другой.pdf"))
            self.assertIsNone(
                cloudsync.mirror_for_remote("yandex:Чужое/файл.docx"))
            self.assertIsNone(
                cloudsync.mirror_for_remote("gdrive:Папка-пример/f.docx"))
            # пара ya.d отвечает и для запросов через remote 'yandex:'
            with mock.patch.object(cloudsync, "load_pairs", return_value=[]):
                self.assertEqual(
                    cloudsync.mirror_for_remote(
                        "yandex:Папка-пример/ф.pdf"),
                    os.path.join(self.tmp, "yad", "ф.pdf"))


class _FakeProc:
    def __init__(self, returncode=0, stderr="", action=None):
        self._rc = returncode
        self.returncode = returncode
        self._stderr = stderr
        self._action = action
        self.cmd = None
        self.stderr = None
        self.terminated = False

    def poll(self):
        if self._action:
            self._action(self.cmd)
            self._action = None
        return None if self._rc is None else self._rc

    def wait(self, timeout=None):
        return self._rc or 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


class FallbackDownloadTests(unittest.TestCase):
    """cloudmount.fallback_download: зеркало и прямой rclone copyto."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_fb_")
        self.content = b"MIRROR-CONTENT" * 10
        self.mirror = os.path.join(self.tmp, "mirror.bin")
        with open(self.mirror, "wb") as f:
            f.write(self.content)
        self.src = os.path.join(self.tmp, "yandex", "dir", "f.bin")
        os.makedirs(os.path.dirname(self.src))
        open(self.src, "wb").close()  # через FUSE не читается, но путь есть
        self.dst = os.path.join(self.tmp, "out", "f.bin")
        os.makedirs(os.path.dirname(self.dst))
        # MOUNT_ROOT — корень монтирований; src даёт 'yandex:dir/f.bin'
        patcher = mock.patch.object(cloudmount, "MOUNT_ROOT", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(cloudmount, "IS_WINDOWS", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mirror_with_md5_check(self):
        md5 = hashlib.md5(self.content).hexdigest()
        with mock.patch.object(cloudsync, "mirror_for_remote",
                               return_value=self.mirror), \
                mock.patch.object(cloudsync, "remote_meta",
                                  return_value={"size": len(self.content),
                                                "md5": md5}):
            ok, note = cloudmount.fallback_download(
                self.src, self.dst, len(self.content), lambda: False)
        self.assertTrue(ok)
        self.assertIn("зеркала", note)
        with open(self.dst, "rb") as f:
            self.assertEqual(f.read(), self.content)

    def test_stale_mirror_falls_to_copyto_without_rclone(self):
        bad_md5 = hashlib.md5(b"other").hexdigest()
        with mock.patch.object(cloudsync, "mirror_for_remote",
                               return_value=self.mirror), \
                mock.patch.object(cloudsync, "remote_meta",
                                  return_value={"size": len(self.content),
                                                "md5": bad_md5}), \
                mock.patch.object(cloudmount, "rclone_bin", return_value=None):
            ok, note = cloudmount.fallback_download(
                self.src, self.dst, len(self.content), lambda: False)
        self.assertFalse(ok)
        self.assertIn("зеркало", note)
        self.assertIn("rclone", note)
        self.assertFalse(os.path.exists(self.dst))

    def test_direct_copyto_command_and_result(self):
        captured = {}

        def make_proc(cmd, **kw):
            captured["cmd"] = cmd
            tmp = cmd[3]  # [0]=rclone [1]=copyto [2]=remote [3]=tmp
            with open(tmp, "wb") as f:
                f.write(b"DIRECT" * 20)
            return _FakeProc(returncode=0)

        with mock.patch.object(cloudmount, "rclone_bin",
                               return_value="/bin/rclone"), \
                mock.patch.object(cloudmount.subprocess, "Popen",
                                  side_effect=make_proc):
            ok, note = cloudmount.fallback_download(
                self.src, self.dst, 120, lambda: False)
        self.assertTrue(ok)
        self.assertIn("rclone", note)
        cmd = captured["cmd"]
        self.assertEqual(cmd[:3], ["/bin/rclone", "copyto",
                                   "yandex:dir/f.bin"])
        self.assertIn("--retries", cmd)
        with open(self.dst, "rb") as f:
            self.assertEqual(f.read(), b"DIRECT" * 20)

    def test_copyto_size_mismatch_rejected(self):
        def make_proc(cmd, **kw):
            with open(cmd[3], "wb") as f:
                f.write(b"short")
            return _FakeProc(returncode=0)

        with mock.patch.object(cloudmount, "rclone_bin",
                               return_value="/bin/rclone"), \
                mock.patch.object(cloudmount.subprocess, "Popen",
                                  side_effect=make_proc):
            ok, note = cloudmount.fallback_download(
                self.src, self.dst, 1000, lambda: False)
        self.assertFalse(ok)
        self.assertIn("байт", note)

    def test_cancel_during_copyto(self):
        proc = _FakeProc(returncode=None)

        def never(_cmd, **kw):
            return proc

        with mock.patch.object(cloudmount, "rclone_bin",
                               return_value="/bin/rclone"), \
                mock.patch.object(cloudmount.subprocess, "Popen",
                                  side_effect=never):
            ok, _note = cloudmount.fallback_download(
                self.src, self.dst, -1, lambda: True)
        self.assertFalse(ok)
        self.assertTrue(proc.terminated)


if __name__ == "__main__":
    unittest.main()
