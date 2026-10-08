#!/usr/bin/env python3
"""Прогон полного набора тестов с выключенным циклическим GC.

PySide уничтожает C++-объекты, когда циклический GC собирает остров
«виджет↔родитель» — в произвольный момент, в том числе при выходе из
интерпретатора, и это даёт сегфолт после «OK» (раннеры, 07–08.10.2026).
Тесты короткие: отключаем циклический GC на время прогона целиком —
острова просто не собираются; объекты с нулевым счётчиком ссылок
освобождаются обычным механизмом. Точечные gc.collect() в tearDown
(test_modules6._GcAfterTest и др.) остаются как есть.

Запуск:  python tests/run_suite.py [каталог-дискавери] [--enable-gc]
"""

import gc
import sys
import unittest


def main() -> int:
    enable_gc = "--enable-gc" in sys.argv
    if enable_gc:
        sys.argv.remove("--enable-gc")
    else:
        gc.disable()
    directory = sys.argv[1] if len(sys.argv) > 1 else "tests"
    suite = unittest.defaultTestLoader.discover(directory)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
