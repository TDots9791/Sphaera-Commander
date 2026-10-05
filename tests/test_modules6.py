"""Тесты партии 6: syncdirs — синхронизация каталогов (сравнение,
таблица направлений, выполнение копирования через ops); buttonbar —
кнопки под адресной строкой (хранение, плейсхолдеры, запуск); colorize —
раскраска файлов по типу (категории, палитры тем, override-файл)."""

import gc
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import unittest.mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sc_m6_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from sphaera_commander import archives, buttonbar, colorize, hotlist  # noqa: E402
from sphaera_commander.dialogs import HotlistEditor  # noqa: E402
from sphaera_commander.fsmodel import (  # noqa: E402
    FileEntry,
    FileTableModel,
    entry_for,
)
from sphaera_commander.ops import (  # noqa: E402
    POLICY_OVERWRITE,
    execute,
)
from sphaera_commander.plugins import syncdirs  # noqa: E402


def write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def build_pair():
    """Пара каталогов со всеми случаями сравнения; возвращает (a, b)."""
    a = tempfile.mkdtemp(prefix="sc_syn_a_")
    b = tempfile.mkdtemp(prefix="sc_syn_b_")
    now = time.time()
    write(os.path.join(a, "только_слева.txt"), "в левой")
    write(os.path.join(b, "только_справа.txt"), "в правой")
    write(os.path.join(a, "равны.txt"), "одинаково")
    write(os.path.join(b, "равны.txt"), "одинаково")
    write(os.path.join(a, "левый_свежее.txt"), "новее слева")
    write(os.path.join(b, "левый_свежее.txt"), "старее справа")
    write(os.path.join(a, "правый_свежее.txt"), "старее слева")
    write(os.path.join(b, "правый_свежее.txt"), "новее справа")
    write(os.path.join(a, "конфликт.txt"), "длинное содержимое слева")
    write(os.path.join(b, "конфликт.txt"), "коротко")
    os.mkdir(os.path.join(a, "каталог_слева"))
    os.mkdir(os.path.join(a, "каталог_у_обоих"))
    os.mkdir(os.path.join(b, "каталог_у_обоих"))
    write(os.path.join(a, ".скрытый"), "скрытый")
    # у общего каталога разведено время: иначе размер+mtime равны и это "="
    os.utime(os.path.join(a, "каталог_у_обоих"), (int(now) - 30,) * 2)
    # равенство и конфликт — по целой секунде mtime (так сравнивает движок)
    same = int(now) - 60
    for name in ("равны.txt", "конфликт.txt"):
        os.utime(os.path.join(a, name), (same, same))
        os.utime(os.path.join(b, name), (same, same))
    os.utime(os.path.join(a, "левый_свежее.txt"), (now, now))
    os.utime(os.path.join(b, "левый_свежее.txt"), (now - 120,) * 2)
    os.utime(os.path.join(a, "правый_свежее.txt"), (now - 120,) * 2)
    os.utime(os.path.join(b, "правый_свежее.txt"), (now, now))
    return a, b


def directions(rows):
    return {name: direction for name, direction, _le, _re in rows}


class CollectRowsTests(unittest.TestCase):
    def setUp(self):
        self.a, self.b = build_pair()
        self.addCleanup(shutil.rmtree, self.a, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.b, ignore_errors=True)

    def test_all_directions(self):
        rows = directions(syncdirs.collect_rows(self.a, self.b))
        self.assertEqual(rows["только_слева.txt"], syncdirs.ARROW_LR)
        self.assertEqual(rows["только_справа.txt"], syncdirs.ARROW_RL)
        self.assertEqual(rows["равны.txt"], syncdirs.ARROW_EQ)
        self.assertEqual(rows["левый_свежее.txt"], syncdirs.ARROW_LR)
        self.assertEqual(rows["правый_свежее.txt"], syncdirs.ARROW_RL)
        self.assertEqual(rows["конфликт.txt"], syncdirs.ARROW_CONFLICT)
        self.assertEqual(rows["каталог_слева"], syncdirs.ARROW_LR)
        self.assertEqual(rows["каталог_у_обоих"], syncdirs.ARROW_CONFLICT)
        self.assertNotIn(".скрытый", rows)

    def test_show_hidden_includes_dotted(self):
        rows = directions(syncdirs.collect_rows(self.a, self.b, True))
        self.assertEqual(rows[".скрытый"], syncdirs.ARROW_LR)

    def test_identical_dirs_are_all_equal(self):
        c = tempfile.mkdtemp(prefix="sc_syn_c_")
        self.addCleanup(shutil.rmtree, c, ignore_errors=True)
        write(os.path.join(c, "файл.txt"), "содержимое")
        rows = directions(syncdirs.collect_rows(c, c))
        self.assertEqual(rows, {"файл.txt": syncdirs.ARROW_EQ})


class FakeApp(QLabel):
    """Минимум MainWindow для диалога: QWidget-родитель + приём операций."""

    def __init__(self):
        super().__init__()
        self.ops = []
        self.refreshed = 0

    def _fs_fn(self, kind, plan, sources):
        def fn(progress_cb, is_cancelled):
            return execute(kind, plan, sources, POLICY_OVERWRITE,
                           progress_cb, is_cancelled)
        return fn

    def _enqueue_op(self, title, fn, after=None):
        self.ops.append((title, fn, after))

    def refresh_all(self):
        self.refreshed += 1


class _GcAfterTest:
    """Циклы «виджет↔фейк-родитель» собираются циклическим GC в произвольный
    момент — в том числе внутри processEvents ЧУЖОГО теста, где уничтожение
    C++-объектов даёт сегфолт (доказано: gc.disable() даёт 6/6 зелёных).
    Собираем острова в контролируемый момент — сразу после своего теста."""

    def tearDown(self):
        gc.collect()


class SyncDialogTests(_GcAfterTest, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.a, self.b = build_pair()
        self.addCleanup(shutil.rmtree, self.a, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.b, ignore_errors=True)

    def _dialog(self, app=None):
        dlg = syncdirs.SyncDirsDialog(app, self.a, self.b)
        # Конструкторный поток шлёт rowsReady через очередь событий: выкачиваем
        # её, пока диалог жив, иначе отложенный вызов стреляет в уничтоженный
        # объект при следующем прогоне событийного цикла (сегфолт, пойман
        # faulthandler'ом).
        deadline = time.monotonic() + 5
        while "→" not in dlg.status.text() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        dlg._thread.join(5)
        self.app.processEvents()
        # close немедленно, с выкачкой очереди: отложенные события закрытого
        # диалога не должны стрелять в чужом processEvents следующего теста
        def _close_and_drain():
            dlg.close()
            self.app.processEvents()
        self.addCleanup(_close_and_drain)
        return dlg

    def test_table_fill_and_default_checks(self):
        dlg = self._dialog()
        self.assertEqual(dlg.table.rowCount(), 8)
        checked, unchecked = [], []
        for r in range(dlg.table.rowCount()):
            arrow = dlg.table.item(r, 1)
            target = checked if arrow.checkState() == Qt.Checked else unchecked
            target.append(arrow.text())
        # стрелки в обе стороны отмечены; «=» и «≠» — нет
        self.assertEqual(sorted(checked), ["←", "←", "→", "→", "→"])
        self.assertEqual(sorted(unchecked), ["=", "≠", "≠"])
        self.assertIn("≠", dlg.status.text())

    def test_sync_selected_copies_both_ways(self):
        fake = FakeApp()
        dlg = self._dialog(fake)
        dlg._sync_selected()
        self.assertEqual(len(fake.ops), 2)
        for _title, fn, _after in fake.ops:
            fn(lambda p: None, lambda: False)
        # обновление панелей назначено after-колбэком первой операции
        self.assertIsNotNone(fake.ops[0][2])
        fake.ops[0][2]()
        self.assertEqual(fake.refreshed, 1)
        # только_слева уехал вправо, только_справа — влево; свежие перезаписали
        self.assertTrue(os.path.exists(os.path.join(self.b, "только_слева.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.a, "только_справа.txt")))
        with open(os.path.join(self.b, "левый_свежее.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "новее слева")
        with open(os.path.join(self.a, "правый_свежее.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "новее справа")
        # конфликт и равные не тронуты
        with open(os.path.join(self.b, "конфликт.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "коротко")

    def test_sync_without_marks_reports(self):
        fake = FakeApp()
        dlg = self._dialog(fake)
        for r in range(dlg.table.rowCount()):
            dlg.table.item(r, 1).setCheckState(Qt.Unchecked)
        dlg._sync_selected()
        self.assertEqual(fake.ops, [])
        self.assertIn("Нет отмеченных", dlg.status.text())


class PluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_create_and_id(self):
        self.assertEqual(syncdirs.Plugin.id, "syncdirs")
        plugin = syncdirs.create(FakeApp())
        self.assertEqual(plugin.id, "syncdirs")
        actions = plugin.tools_actions()
        self.assertEqual(len(actions), 1)
        self.assertIn("Синхронизация", actions[0][0])


class ButtonBarLogicTests(unittest.TestCase):
    """Хранение и сборка команды — без GUI."""

    def setUp(self):
        self._patch = unittest.mock.patch.object(
            buttonbar, "BUTTONS_FILE",
            os.path.join(tempfile.mkdtemp(prefix="sc_bb_"), "buttons.json"))
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_roundtrip_and_defaults(self):
        self.assertEqual(buttonbar.load_buttons(), [])
        buttonbar.save_buttons([{"title": "Терминал", "cmd": "xterm -e sh -c %d"}])
        self.assertEqual(buttonbar.load_buttons(),
                         [{"title": "Терминал", "cmd": "xterm -e sh -c %d"}])
        # битый файл — пустой список, не падение
        with open(buttonbar.BUTTONS_FILE, "w", encoding="utf-8") as f:
            f.write("{битый")
        self.assertEqual(buttonbar.load_buttons(), [])

    def test_build_command_quoting(self):
        built = buttonbar.build_command(
            "prog -f %f -d %d", "/dir with space",
            "/dir with space/файл.txt")
        self.assertEqual(
            built,
            "prog -f '/dir with space/файл.txt' -d '/dir with space'")
        # нет файла под курсором — %f превращается в пустой аргумент
        self.assertEqual(
            buttonbar.build_command("prog %f", "/d", None),
            "prog ''")

    def test_run_command_executes_detached(self):
        marker = tempfile.mktemp(prefix="sc_bb_marker_")
        self.addCleanup(lambda: os.path.exists(marker)
                        and os.remove(marker))
        buttonbar.run_command(f"echo ok > {shq(marker)}", "/tmp")
        deadline = time.monotonic() + 5
        while not os.path.exists(marker) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(os.path.exists(marker))


def shq(path):
    import shlex

    return shlex.quote(path)


def drain_delete(widget):
    """deleteLater с немедленной выкачкой очереди: отложенное удаление
    родителя-осироты срабатывало в чужом processEvents следующего теста
    (плавающий сегфолт, пойман faulthandler'ом)."""
    widget.deleteLater()
    QApplication.instance().processEvents()


class FakePanel(QLabel):
    """Минимум панели для ButtonBar: курсор, каталог, флаг VFS."""

    def __init__(self):
        super().__init__()
        self._cwd = os.path.expanduser("~")
        self._entry = None
        self.is_vfs = False

    def current_path(self):
        return self._cwd

    def current_entry(self):
        return self._entry


class ButtonBarWidgetTests(_GcAfterTest, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._patch = unittest.mock.patch.object(
            buttonbar, "BUTTONS_FILE",
            os.path.join(tempfile.mkdtemp(prefix="sc_bbw_"), "buttons.json"))
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_bar_renders_buttons_and_gear(self):
        buttonbar.save_buttons([{"title": "A", "cmd": "a"},
                                {"title": "B", "cmd": "b"}])
        panel = FakePanel()
        bar = buttonbar.ButtonBar(panel)
        self.addCleanup(drain_delete, bar)
        texts = [bar._row.itemAt(i).widget().text()
                 for i in range(bar._row.count())
                 if bar._row.itemAt(i).widget() is not None]
        self.assertEqual(texts, ["A", "B", "⚙"])

    def test_bar_empty_still_shows_gear(self):
        bar = buttonbar.ButtonBar(FakePanel())
        self.addCleanup(drain_delete, bar)
        texts = [bar._row.itemAt(i).widget().text()
                 for i in range(bar._row.count())
                 if bar._row.itemAt(i).widget() is not None]
        self.assertEqual(texts, ["⚙"])

    def test_editor_roundtrip(self):
        editor = buttonbar.ButtonsEditor(
            None, [{"title": "A", "cmd": "a"}, {"title": "B", "cmd": "b"}])
        self.addCleanup(drain_delete, editor)
        editor.list.setCurrentRow(1)
        editor._delete()
        self.assertEqual(editor.result_buttons(), [{"title": "A", "cmd": "a"}])
        editor._buttons.append({"title": "C", "cmd": "c"})
        editor._refresh()
        editor.list.setCurrentRow(1)  # C в конце
        editor._move(-1)              # C поднимается выше A
        self.assertEqual([b["title"] for b in editor.result_buttons()],
                         ["C", "A"])


class ColorizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        colorize.reset_cache()
        self.addCleanup(colorize.reset_cache)
        self._patch = unittest.mock.patch.object(
            colorize, "OVERRIDES_FILE",
            os.path.join(tempfile.mkdtemp(prefix="sc_col_"), "colorize.json"))
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_categories(self):
        self.assertEqual(colorize.category_of("книга.zip"), "archive")
        self.assertEqual(colorize.category_of("фото.PNG"), "image")
        self.assertEqual(colorize.category_of("клип.mkv"), "video")
        self.assertEqual(colorize.category_of("песня.flac"), "audio")
        self.assertIsNone(colorize.category_of("текст.txt"))
        self.assertIsNone(colorize.category_of("каталог"))

    def test_palette_both_schemes_differ(self):
        dark = colorize.brush_for("архив.zip", True)
        light = colorize.brush_for("архив.zip", False)
        self.assertIsNotNone(dark)
        self.assertIsNotNone(light)
        self.assertNotEqual(dark, light)
        self.assertIsNotNone(colorize.brush_for("картинка.png", True))
        self.assertIsNone(colorize.brush_for("док.txt", True))

    def test_overrides_file(self):
        with open(colorize.OVERRIDES_FILE, "w", encoding="utf-8") as f:
            json.dump({"categories": {"code": [".py"]},
                       "colors": {"dark": {"code": "#7f8c99"}}}, f)
        colorize.reset_cache()
        self.assertEqual(colorize.category_of("модуль.py"), "code")
        self.assertIsNotNone(colorize.brush_for("модуль.py", True))
        # битый override — молча дефолты: .py в них нет, категория исчезает
        with open(colorize.OVERRIDES_FILE, "w", encoding="utf-8") as f:
            f.write("{нет")
        colorize.reset_cache()
        self.assertIsNone(colorize.category_of("модуль.py"))
        self.assertIsNone(colorize.brush_for("модуль.py", True))

    def test_enabled_flag_and_model_role(self):
        from sphaera_commander import colorize as col

        col.set_enabled(True)
        model = FileTableModel()
        model.entries = [
            FileEntry(name="архив.zip", path="/t/архив.zip", is_dir=False,
                      is_link=False, size=1, mtime=0.0, mode=0o644),
            FileEntry(name="текст.txt", path="/t/текст.txt", is_dir=False,
                      is_link=False, size=1, mtime=0.0, mode=0o644),
        ]
        idx_zip = model.index(1, 0)
        idx_txt = model.index(2, 0)
        self.assertIsNotNone(model.data(idx_zip, Qt.ForegroundRole))
        self.assertIsNone(model.data(idx_txt, Qt.ForegroundRole))
        # отмеченный объект важнее раскраски
        model.marked.add("архив.zip")
        marked = model.data(idx_zip, Qt.ForegroundRole)
        col.set_enabled(True)
        self.assertIsNotNone(marked)
        # выключено — раскраски нет
        col.set_enabled(False)
        model.marked.clear()
        self.assertIsNone(model.data(idx_zip, Qt.ForegroundRole))
        col.set_enabled(True)


class HotlistTests(_GcAfterTest, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._patch = unittest.mock.patch.object(
            hotlist, "HOTLIST_FILE",
            os.path.join(tempfile.mkdtemp(prefix="sc_hl_"), "hotlist.json"))
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_roundtrip_and_add(self):
        self.assertEqual(hotlist.load_hotlist(), [])
        items = hotlist.add_current("/tmp/папка с пробелом", [])
        self.assertEqual(items[0]["title"], "папка с пробелом")
        again = hotlist.add_current("/tmp/папка с пробелом/", items)
        self.assertEqual(len(again), 1)  # дубль не добавляется
        other = hotlist.add_current("/home", again)
        self.assertEqual(len(other), 2)
        hotlist.save_hotlist(other)
        self.assertEqual(hotlist.load_hotlist(), other)
        # битый файл — пустой список
        with open(hotlist.HOTLIST_FILE, "w", encoding="utf-8") as f:
            f.write("нет")
        self.assertEqual(hotlist.load_hotlist(), [])

    def test_editor_add_delete(self):
        dlg = HotlistEditor(None, [{"title": "A", "path": "/a"}], "/tmp/текущая")
        self.addCleanup(drain_delete, dlg)
        dlg._add_current()
        titles = [i["path"] for i in dlg.result_items()]
        self.assertEqual(titles, ["/a", "/tmp/текущая"])
        dlg.list.setCurrentRow(0)
        dlg._delete()
        self.assertEqual([i["path"] for i in dlg.result_items()],
                         ["/tmp/текущая"])


class SevenZipRarTests(unittest.TestCase):
    """7z/RAR через внешние утилиты: живой 7z (если установлен),
    graceful-ветка без утилиты, запрет записи RAR."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_7z_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        write(os.path.join(self.tmp, "один.txt"), "первый файл")
        os.mkdir(os.path.join(self.tmp, "под"))
        write(os.path.join(self.tmp, "под", "два.txt"), "второй файл")

    def test_format_detection(self):
        self.assertEqual(archives.archive_format("x.7z"), "7z")
        self.assertEqual(archives.archive_format("x.rar"), "rar")
        self.assertIsNone(archives.archive_format("x.txt"))

    @unittest.skipUnless(shutil.which("7z"), "7z не установлен")
    def test_7z_pack_list_unpack(self):
        out = os.path.join(self.tmp, "сборка.7z")
        entries = [entry_for(os.path.join(self.tmp, "один.txt")),
                   entry_for(os.path.join(self.tmp, "под"))]
        res = archives.pack_items(entries, out, "7z",
                                  lambda p: None, lambda: False)
        self.assertFalse(res.errors)
        self.assertTrue(os.path.isfile(out))
        members = {archives.norm_member(n): s
                   for n, s, k in archives.read_members(out)}
        self.assertEqual(members["один.txt"],
                         len("первый файл".encode()))  # байты UTF-8
        self.assertIn("под/два.txt", members)
        # VFS
        browser = archives.ArchiveBrowser(out)
        root = browser.list_dir("")
        self.assertEqual([e.name for e in root if e.is_dir], ["под"])
        dest = os.path.join(self.tmp, "распак")
        res = archives.unpack_archive(out, dest, lambda p: None, lambda: False)
        self.assertFalse(res.errors)
        with open(os.path.join(dest, "один.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "первый файл")
        # выборочное извлечение члена
        target = os.path.join(self.tmp, "вне", "два.txt")
        os.makedirs(os.path.dirname(target))
        archives._extract_file("7z", out, "под/два.txt", target)
        with open(target, encoding="utf-8") as f:
            self.assertEqual(f.read(), "второй файл")
        # правка состава 7z через утилиту: удалить и заменить
        res = browser.delete_members(["один.txt"])
        self.assertFalse(res.errors)
        self.assertEqual(res.skipped, 1)
        members2 = {archives.norm_member(n) for n, _s, _k
                    in archives.read_members(out)}
        self.assertNotIn("один.txt", members2)
        self.assertIn("под/два.txt", members2)
        new_browser = archives.ArchiveBrowser(out)
        local = new_browser.extract_member_to_temp(
            "под/два.txt", self.tmp)
        with open(local, "w", encoding="utf-8") as f:
            f.write("второй файл — обновлён")
        res = new_browser.replace_member("под/два.txt", local)
        self.assertFalse(res.errors)
        final = os.path.join(self.tmp, "финал")
        os.makedirs(final)
        new_browser.extract_members(["под/два.txt"], final,
                                    lambda p: None, lambda: False)
        with open(os.path.join(final, "под", "два.txt"),
                  encoding="utf-8") as f:
            self.assertEqual(f.read(), "второй файл — обновлён")

    @unittest.skipUnless(shutil.which("7z"), "7z не установлен")
    def test_7z_replace_adds_new_member_for_unknown(self):
        out = os.path.join(self.tmp, "заменс.7z")
        entries = [entry_for(os.path.join(self.tmp, "один.txt"))]
        res = archives.pack_items(entries, out, "7z",
                                  lambda p: None, lambda: False)
        self.assertFalse(res.errors)
        browser = archives.ArchiveBrowser(out)
        res = browser.replace_member("нет-такого.txt",
                                     os.path.join(self.tmp, "один.txt"))
        self.assertFalse(res.errors)  # добавление нового члена — валидный случай

    def test_rar_members_vt_parsing(self):
        """Размеры членов RAR из `unrar vt` (мок вывода; unrar на машине
        не установлен — парсер проверяется на реалистичном выводе)."""
        vt_output = (
            "Archive: /tmp/x.rar\n"
            "Details: RAR 5\n"
            "\n"
            " Name:        папка/\n"
            " Type:       Directory\n"
            " mtime:      2024-01-01 10:00:00,000\n"
            "\n"
            " Name:        папка/файл.txt\n"
            " Type:       File\n"
            " Size:       1234\n"
            " Packed size: 1100\n"
            "\n"
            " Name:        корень.bin\n"
            " Type:       File\n"
            " Size:       99\n"
        )

        class FakeProc:
            returncode = 0
            stdout = vt_output
            stderr = ""

        def fake_run(*_a, **_kw):
            return FakeProc()

        with unittest.mock.patch.object(archives, "external_tool",
                                        return_value="/usr/bin/unrar"), \
                unittest.mock.patch.object(archives.subprocess, "run",
                                           fake_run):
            members = archives._rar_members("/tmp/x.rar")
        self.assertIn(("папка/", 0, "dir"), members)
        self.assertIn(("папка/файл.txt", 1234, "file"), members)
        self.assertIn(("корень.bin", 99, "file"), members)

    def test_rar_members_fallback_names_only(self):
        """Старый unrar (нет vt): откат на список имён, размеры 0."""

        class FakeVt:
            returncode = 11
            stdout = ""
            stderr = "unsupported command"

        class FakeLb:
            returncode = 0
            stdout = "папка/\nфайл.txt\n"
            stderr = ""

        responses = [FakeVt(), FakeLb()]

        def fake_run(*_a, **_kw):
            return responses.pop(0)

        with unittest.mock.patch.object(archives, "external_tool",
                                        return_value="/usr/bin/unrar"), \
                unittest.mock.patch.object(archives.subprocess, "run",
                                           fake_run):
            members = archives._rar_members("/tmp/x.rar")
        self.assertEqual(members, [("папка/", 0, "dir"),
                                   ("файл.txt", 0, "file")])

    def test_missing_tool_is_graceful(self):
        arc = os.path.join(self.tmp, "ненастоящий.7z")
        with unittest.mock.patch.object(archives, "external_tool",
                                        return_value=None):
            with self.assertRaises(ValueError) as cm:
                archives.read_members(arc)
            self.assertIn("не установлен", str(cm.exception))

    def test_rar_pack_refused(self):
        out = os.path.join(self.tmp, "x.rar")
        res = archives.pack_items(
            [entry_for(os.path.join(self.tmp, "один.txt"))],
            out, "rar", lambda p: None, lambda: False)
        self.assertTrue(res.errors)
        self.assertIn("RAR", res.errors[0].message)

    def test_rar_edit_refused(self):
        """RAR только чтение: правка состава — честный отказ."""
        out = os.path.join(self.tmp, "x.rar")
        with open(out, "wb") as f:
            f.write(b"Rar!\x1a\x07\x01\x00fake")
        browser = archives.ArchiveBrowser.__new__(archives.ArchiveBrowser)
        browser.format = "rar"
        browser.archive_path = out
        res = browser._rewrite(skip={"a"}, replace={})
        self.assertTrue(res.errors)
        self.assertIn("rar", res.errors[0].message)

    @unittest.skipIf(shutil.which("unrar"), "unrar установлен — проверяем graceful")
    def test_rar_without_unrar_graceful(self):
        with unittest.mock.patch.object(archives, "external_tool",
                                        return_value=None):
            with self.assertRaises(ValueError):
                archives.read_members(os.path.join(self.tmp, "x.rar"))


if __name__ == "__main__":
    unittest.main()
