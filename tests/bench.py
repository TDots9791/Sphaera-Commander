"""Бенчмарк регрессий производительности — мандат 0.19.0 («самые быстрые
и лёгкие»). Замеряет и печатает:

  startup_s   — старт MainWindow (offscreen) до готовности панелей
  rss_mib     — RSS процесса сразу после старта
  scan_s      — scan_directory + sort_entries на 100 000 файлов
  mark_s      — mark_mask на 10 000 строках
  data_s      — 1000 вызовов FileTableModel.data (DisplayRole)

Гейты 0.19.0: старт <= 0.5 с, RSS <= 80 МиБ, scan <= 1.5 с, mark <= 0.1 с.
Запуск: .venv/bin/python tests/bench.py [--json]

RSS меряется как пик процесса (ru_maxrss); старт — отдельным процессом,
чтобы не тащить импорты этого скрипта в замер."""

from __future__ import annotations

import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="sc_bench_"))

GATES = {"startup_s": 0.5, "rss_mib": 80.0, "scan_s": 1.5, "mark_s": 0.1}

STARTUP_PROBE = r"""
import os, resource, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("XDG_CONFIG_HOME", {config_home!r})
sys.path.insert(0, {root!r})
t0 = time.perf_counter()
from PySide6.QtWidgets import QApplication
app = QApplication([])
t1 = time.perf_counter()
from sphaera_commander.app import MainWindow
win = MainWindow()
t2 = time.perf_counter()
for pnl in (win.left, win.right):
    pnl.wait_loaded()
t3 = time.perf_counter()
rss_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
print("PROBE", round(t1 - t0, 3), round(t2 - t1, 3), round(t3 - t2, 3),
      round(rss_mib, 1))
"""


def measure_startup(root: str, config_home: str) -> dict:
    code = STARTUP_PROBE.format(root=root, config_home=config_home)
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, timeout=120)
    for line in out.stdout.splitlines():
        if line.startswith("PROBE"):
            _, qt_s, app_s, panels_s, rss = line.split()
            return {"startup_s": round(float(qt_s) + float(app_s)
                                       + float(panels_s), 3),
                    "qt_import_s": float(qt_s),
                    "app_import_s": float(app_s),
                    "panels_s": float(panels_s),
                    "rss_mib": float(rss)}
    raise RuntimeError("зонд старта не отчитался: " + out.stderr[-400:])


def build_tree(root: str, n_files: int, dirs: int = 1) -> list:
    """Плоское дерево из n_files файлов (панель читает один уровень —
    гейт мерит один каталог с 100 тыс. записей)."""
    from sphaera_commander.fsmodel import FileEntry

    per_dir = max(1, n_files // max(1, dirs))
    for d in range(dirs):
        sub = root if dirs == 1 else os.path.join(root, f"d{d:03d}")
        os.makedirs(sub, exist_ok=True)
        for i in range(per_dir):
            with open(os.path.join(sub, f"f{d:03d}_{i:05d}.dat"), "wb") as f:
                f.write(b"x" * 64)
    entries = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        entries.append(FileEntry(name=name, path=path, is_dir=True,
                                 is_link=False, size=0,
                                 mtime=os.path.getmtime(path), mode=0o755))
    return entries


def measure_scan(root: str) -> dict:
    from sphaera_commander.fsmodel import scan_directory, sort_entries

    t0 = time.perf_counter()
    entries = scan_directory(root, show_hidden=False)
    sort_entries(entries, 0, False)
    dt = time.perf_counter() - t0
    return {"scan_s": round(dt, 3), "scan_files": len(entries)}


def measure_mark_and_data(root: str) -> dict:
    from sphaera_commander.fsmodel import FileTableModel, scan_directory
    from PySide6.QtCore import Qt

    model = FileTableModel()
    model.entries = scan_directory(root, show_hidden=False)
    # mark_mask на первых 10 000 строках
    model.entries = model.entries[:10000]
    t0 = time.perf_counter()
    model.mark_mask("*.dat", True)
    mark_s = time.perf_counter() - t0
    # data(): 1000 вызовов DisplayRole по имени
    idx = model.index(1, 0)
    t0 = time.perf_counter()
    for i in range(1000):
        model.data(model.index(1 + i % max(1, model.rowCount() - 1), 0),
                   Qt.DisplayRole)
    data_s = time.perf_counter() - t0
    return {"mark_s": round(mark_s, 4), "data_s": round(data_s, 4),
            "marked": len(model.marked)}


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result: dict = {"python": sys.version.split()[0]}
    result.update(measure_startup(root, os.environ["XDG_CONFIG_HOME"]))

    tmp = tempfile.mkdtemp(prefix="sc_bench_tree_")
    try:
        entries = build_tree(tmp, 100_000)
        result.update(measure_scan(tmp))
        shutil.rmtree(tmp, ignore_errors=True)
        small = tempfile.mkdtemp(prefix="sc_bench_small_")
        build_tree(small, 10_000)
        result.update(measure_mark_and_data(small))
        shutil.rmtree(small, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    gates = {k: ("PASS" if result[k] <= v else "FAIL")
             for k, v in GATES.items() if k in result}
    result["gates"] = gates
    if "--json" in sys.argv:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"старт MainWindow:  {result['startup_s']:.3f} с "
              f"(Qt {result['qt_import_s']:.2f} + импорт {result['app_import_s']:.2f}"
              f" + панели {result['panels_s']:.2f})  гейт {gates.get('startup_s')}")
        print(f"RSS после старта:  {result['rss_mib']:.1f} МиБ  "
              f"гейт {gates.get('rss_mib')}")
        print(f"scan 100k + сорт:  {result['scan_s']:.3f} с "
              f"({result['scan_files']} записей)  гейт {gates.get('scan_s')}")
        print(f"mark_mask 10k:     {result['mark_s']:.4f} с  "
              f"гейт {gates.get('mark_s')}")
        print(f"data() x1000:      {result['data_s']:.4f} с")
    bad = [k for k, v in gates.items() if v == "FAIL"]
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
