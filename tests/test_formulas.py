"""Тесты движка пересчёта формул xlsx (formulas.py) — чистое ядро без Qt."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sphaera_commander import formulas as F  # noqa: E402


def ev(formula: str, rows, sheet: str = "L"):
    """Вычислить одну формулу над листом rows (сырые строки)."""
    ctx = F.Context({sheet: []})
    vals, _n, _failed, _un = F.evaluate_sheet([["x"]], ctx, sheet)
    # подменить строки листа на данные и вычислить напрямую
    ctx.sheets[sheet] = [[F.cell_value(v) for v in row] for row in rows]
    return F._Eval(ctx).value(F.parse_formula(formula[1:]))


class RefTests(unittest.TestCase):
    def test_parse_ref(self):
        self.assertEqual(F.parse_ref("A1"), (None, 0, 0))
        self.assertEqual(F.parse_ref("$B$3"), (None, 2, 1))
        self.assertEqual(F.parse_ref("AA10"), (None, 9, 26))
        self.assertEqual(F.parse_ref("Лист2!B3"), ("Лист2", 2, 1))
        self.assertEqual(F.parse_ref("'Мой лист'!C4"), ("Мой лист", 3, 2))
        self.assertIsNone(F.parse_ref("не ссылка"))

    def test_col_name(self):
        self.assertEqual(F.col_name(0), "A")
        self.assertEqual(F.col_name(25), "Z")
        self.assertEqual(F.col_name(26), "AA")


class EvalTests(unittest.TestCase):
    ROWS = [["1"], ["2"], ["3"]]  # столбец A1:A3

    def test_arithmetic(self):
        cases = [
            ("=1+2*3", 7.0), ("=(1+2)*3", 9.0), ("=2^3^2", 512.0),
            ("=-2^2", 4.0), ("=50%", 0.5), ("=7/2", 3.5),
            ("=A1+A2+A3", 6.0), ("=A1*A3", 3.0), ("=-A1+10", 9.0),
        ]
        for formula, expected in cases:
            self.assertEqual(ev(formula, self.ROWS), expected, formula)

    def test_compare_concat(self):
        self.assertEqual(ev('=A1&"-"&A2', self.ROWS), "1-2")
        self.assertEqual(ev("=A1>=1", self.ROWS), True)
        self.assertEqual(ev("=1>2", self.ROWS), False)
        self.assertEqual(ev('="a"&"b"', self.ROWS), "ab")

    def test_errors(self):
        self.assertEqual(ev("=1/0", self.ROWS), F.ERR_DIV0)
        self.assertEqual(ev("=NOPE(1)", self.ROWS), F.ERR_NAME)
        self.assertEqual(ev('=A1+"текст"', self.ROWS), F.ERR_VALUE)

    def test_math_functions(self):
        cases = [
            ("=SQRT(16)", 4.0), ("=POWER(2,10)", 1024.0), ("=MOD(7,3)", 1.0),
            ("=ABS(-5)", 5.0), ("=SIGN(-5)", -1.0), ("=INT(2.9)", 2.0),
            ("=ROUND(2.5,0)", 3.0), ("=ROUND(-2.5,0)", -3.0),
            ("=ROUND(3.14159,2)", 3.14), ("=ROUNDUP(1.21,1)", 1.3),
            ("=ROUNDDOWN(1.29,1)", 1.2), ("=PRODUCT(A1:A3)", 6.0),
            ("=MEDIAN(A1:A3)", 2.0), ("=MIN(A1:A3)", 1.0), ("=MAX(A1:A3)", 3.0),
            ("=AVERAGE(A1:A3)", 2.0), ("=SUM(A1:A3)", 6.0),
        ]
        for formula, expected in cases:
            self.assertEqual(ev(formula, self.ROWS), expected, formula)

    def test_sum_skips_text(self):
        # Excel: текст и логические в диапазоне SUM не считает
        rows = [["1", "текст", "TRUE", "4"]]
        self.assertEqual(ev("=SUM(A1:D1)", rows), 5.0)
        self.assertEqual(ev("=COUNT(A1:D1)", rows), 2)
        self.assertEqual(ev("=COUNTA(A1:D1)", rows), 4)

    def test_logic(self):
        self.assertEqual(ev('=IF(1=1,"ok","no")', self.ROWS), "ok")
        self.assertEqual(ev('=IFERROR(1/0,"деление")', self.ROWS), "деление")
        self.assertEqual(ev("=AND(TRUE,1)", self.ROWS), True)
        self.assertEqual(ev("=OR(FALSE,0)", self.ROWS), False)
        self.assertEqual(ev("=NOT(1)", self.ROWS), False)

    def test_text_functions(self):
        cases = [
            ('=UPPER("привет")', "ПРИВЕТ"), ('=LOWER("ABC")', "abc"),
            ('=LEN("abc")', 3), ('=LEFT("hello",2)', "he"),
            ('=RIGHT("hello",2)', "lo"), ('=MID("hello",2,3)', "ell"),
            ('=TRIM("  a   b  ")', "a b"),
            ('=SUBSTITUTE("aaa","a","b",2)', "aba"),
            ('=REPLACE("hello",2,3,"XY")', "hXYo"),
            ('=FIND("b","abc")', 2), ('=SEARCH("B","abc")', 2),
            ('=REPT("ab",3)', "ababab"),
            ('=PROPER("маша и паша")', "Маша И Паша"),
            ('=CONCATENATE("a",1)', "a1"),
        ]
        for formula, expected in cases:
            self.assertEqual(ev(formula, self.ROWS), expected, formula)

    def test_dates(self):
        self.assertEqual(ev("=YEAR(DATE(2024,3,5))", self.ROWS), 2024.0)
        self.assertEqual(ev("=MONTH(DATE(2024,3,5))", self.ROWS), 3.0)
        self.assertEqual(ev("=DAY(DATE(2024,3,5))", self.ROWS), 5.0)
        self.assertIsInstance(ev("=TODAY()", self.ROWS), float)

    def test_conditional_counts(self):
        grid = [["1", "4"], ["2", "5"], ["3", "6"]]
        self.assertEqual(ev('=COUNTIF(A1:A3,">1")', grid), 2)
        self.assertEqual(ev('=SUMIF(A1:A3,">1",B1:B3)', grid), 11.0)
        self.assertEqual(ev('=COUNTIF(A1:A3,2)', grid), 1)
        self.assertEqual(ev('=AVERAGEIF(A1:A3,">1")', grid), 2.5)

    def test_lookup(self):
        grid = [["хлеб", "50"], ["молоко", "90"]]
        self.assertEqual(ev('=VLOOKUP("молоко",A1:B2,2,FALSE)', grid), 90)
        self.assertEqual(ev('=MATCH("хлеб",A1:A2,0)', grid), 1)
        self.assertEqual(ev("=INDEX(A1:B2,2,1)", grid), "молоко")
        nums = [["1", "4"], ["2", "5"], ["3", "6"]]
        self.assertEqual(ev("=VLOOKUP(2,A1:B3,2,FALSE)", nums), 5.0)
        self.assertEqual(ev("=HLOOKUP(4,A1:B1,1,FALSE)", nums), 4.0)

    def test_info_functions(self):
        rows = [[F.cell_value(v)] for v in ["1", "2", "3"]]
        self.assertEqual(ev("=ISNUMBER(A1)", rows), True)
        self.assertEqual(ev("=ISTEXT(A1)", rows), False)
        self.assertEqual(ev("=ISBLANK(B3)", rows), True)
        self.assertEqual(ev("=N(TRUE)", rows), 1.0)
        self.assertEqual(ev("=ROW(B2)", rows), 2.0)
        self.assertEqual(ev("=COLUMN(B2)", rows), 2.0)
        self.assertEqual(ev("=ROWS(A1:B3)", rows), 3.0)
        self.assertEqual(ev("=COLUMNS(A1:B3)", rows), 2.0)


class SheetTests(unittest.TestCase):
    def test_evaluate_sheet_values(self):
        rows = [["10", "20", "=SUM(A1:B1)", '=A1*B1&" руб"'],
                ["x", "y", '=IF(A2="x","да","нет")', '=B2&"!"']]
        vals, n, failed, unstable = F.evaluate_sheet(
            rows, F.Context({"Лист1": []}), "Лист1")
        self.assertEqual(vals[(0, 2)], 30.0)
        self.assertEqual(vals[(0, 3)], "200 руб")
        self.assertEqual(vals[(1, 2)], "да")
        self.assertEqual(vals[(1, 3)], "y!")
        self.assertEqual(failed, set())
        self.assertEqual(unstable, set())

    def test_forward_chain(self):
        vals, _n, _f, unstable = F.evaluate_sheet(
            [["=B1+1", "=5"]], F.Context({"s": []}), "s")
        self.assertEqual(vals[(0, 0)], 6.0)
        self.assertEqual(unstable, set())

    def test_mutual_cycle(self):
        vals, _n, _f, unstable = F.evaluate_sheet(
            [["=B1", "=A1"]], F.Context({"s": []}), "s")
        self.assertEqual(unstable, {(0, 0), (0, 1)})
        self.assertNotIn((0, 0), vals)

    def test_self_cycle(self):
        _vals, _n, _f, unstable = F.evaluate_sheet(
            [["=A1+1", "5"]], F.Context({"s": []}), "s")
        self.assertEqual(unstable, {(0, 0)})

    def test_seed_kept_for_failed_parse(self):
        # '=1+' не разбирается — прежний кэш сохраняется
        vals, _n, failed, _un = F.evaluate_sheet(
            [["=1+"]], F.Context({"s": []}), "s", seed={(0, 0): "старое"})
        self.assertEqual(failed, {(0, 0)})
        self.assertEqual(vals[(0, 0)], "старое")

    def test_unknown_function_is_name_error(self):
        # в Excel неизвестная функция — значение #NAME?, а не ошибка разбора
        vals, _n, failed, _un = F.evaluate_sheet(
            [["=СУМ(B1:B2)", "1", "2"]], F.Context({"s": []}), "s")
        self.assertEqual(failed, set())
        self.assertEqual(vals[(0, 0)], F.ERR_NAME)

    def test_range_over_own_cell_is_cycle(self):
        # =SUM(A1:A2) в ячейке A1 — циклическая ссылка, как и в Excel
        _vals, _n, _f, unstable = F.evaluate_sheet(
            [["=SUM(A1:A2)", "5"]], F.Context({"s": []}), "s")
        self.assertEqual(unstable, {(0, 0)})

    def test_cross_sheet(self):
        ctx = F.Context({"Итог": [], "Данные": [["хлеб", "50"], ["молоко", "90"]]})
        vals, _n, failed, unstable = F.evaluate_sheet(
            [["=Данные!B2"]], ctx, "Итог")
        self.assertEqual(vals[(0, 0)], 90.0)
        self.assertEqual(failed, set())
        self.assertEqual(unstable, set())

    def test_cross_sheet_quoted(self):
        ctx = F.Context({"И": [], "Мой лист": [["21"]]})
        vals, _n, _f, _u = F.evaluate_sheet(
            [["='Мой лист'!A1*2"]], ctx, "И")
        self.assertEqual(vals[(0, 0)], 42.0)

    def test_missing_sheet_is_ref_error(self):
        vals, _n, _f, _u = F.evaluate_sheet(
            [["=Нет!A1"]], F.Context({"И": []}), "И")
        self.assertEqual(vals[(0, 0)], F.ERR_REF)


class FormatTests(unittest.TestCase):
    def test_fmt_value(self):
        self.assertEqual(F.fmt_value(6.0), "6")
        self.assertEqual(F.fmt_value(0.5), "0.5")
        self.assertEqual(F.fmt_value(True), "TRUE")
        self.assertEqual(F.fmt_value(None), "")
        self.assertEqual(F.fmt_value(F.ERR_DIV0), "#DIV/0!")
        self.assertEqual(F.fmt_value("текст"), "текст")

    def test_cell_value_coercion(self):
        self.assertEqual(F.cell_value("42"), 42)
        self.assertEqual(F.cell_value("2.5"), 2.5)
        self.assertEqual(F.cell_value("текст"), "текст")
        self.assertIsNone(F.cell_value(""))
        self.assertEqual(F.cell_value("TRUE"), True)
        self.assertEqual(F.cell_value("=A1"), "=A1")

    def test_formula_error_raises(self):
        with self.assertRaises(F.FormulaError):
            F.parse_formula("1 +")
        with self.assertRaises(F.FormulaError):
            F.parse_formula("ЕЩЁ(непонятно")


if __name__ == "__main__":
    unittest.main()
