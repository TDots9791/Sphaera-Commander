"""Точки инъекции платформенного слоя (ТЗ §4): по умолчанию выключены —
поведение Linux не меняется; Windows-сборка (win64/run.py) подменяет
реализации до старта приложения."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sphaera_commander import buttonbar, ops


class TestPlatformGate(unittest.TestCase):
    def test_defaults_are_off(self):
        self.assertIsNone(ops.trash_via_platform)
        self.assertIsNone(buttonbar.run_detached_shell)

    def test_execute_trash_delegates_when_injected(self):
        sentinel = ops.OpResult()
        calls = []
        ops.trash_via_platform = lambda *a: calls.append(a) or sentinel
        try:
            returned = ops.execute_trash([], lambda s: None, lambda: False)
        finally:
            ops.trash_via_platform = None
        self.assertIs(returned, sentinel)
        self.assertEqual(len(calls), 1)

    def test_run_command_delegates_when_injected(self):
        calls = []
        buttonbar.run_detached_shell = (
            lambda cmd, cwd, detached=False: calls.append((cmd, cwd, detached)))
        try:
            buttonbar.run_command("echo hi", "/tmp")
        finally:
            buttonbar.run_detached_shell = None
        self.assertEqual(calls, [("echo hi", "/tmp", True)])


if __name__ == "__main__":
    unittest.main()
