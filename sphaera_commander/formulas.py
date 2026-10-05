"""Движок пересчёта формул xlsx — свой, без внешних зависимостей.

Поддержка: арифметика (+ - * / ^ %), сравнения, конкатенация (&), ссылки
A1/$A$1/Лист1!A1/'Мой лист'!A1, диапазоны A1:B2, литералы строк и логические.
Функции (~50): SUM/AVERAGE/MEDIAN/MIN/MAX/COUNT/COUNTA/COUNTBLANK,
ROUND/ROUNDUP/ROUNDDOWN/ABS/SIGN/INT/MOD/SQRT/POWER/EXP/LN/LOG/LOG10/PI,
IF/IFERROR/AND/OR/NOT/TRUE/FALSE, CONCAT/CONCATENATE/LEFT/RIGHT/MID/LEN/
LOWER/UPPER/PROPER/TRIM/SUBSTITUTE/REPLACE/FIND/SEARCH/REPT/VALUE,
TODAY/NOW/YEAR/MONTH/DAY/DATE, COUNTIF/SUMIF/AVERAGEIF,
VLOOKUP/HLOOKUP/INDEX/MATCH, ROW/COLUMN/ROWS/COLUMNS,
ISNUMBER/ISTEXT/ISBLANK/ISERROR/N.

Ячейка с формулой, которую движок не осилил, не подменяется: сохраняется
прежнее кэшированное значение файла (см. evaluate_sheet — честная деградация).

Чистое ядро без Qt — тестируется без GUI.
"""

from __future__ import annotations

import datetime as _dt
import math
import re


class ExcelError:
    """Значение-ошибка Excel (#DIV/0! и т.п.); участвует в вычислениях как значение."""

    __slots__ = ("code",)

    def __init__(self, code: str):
        self.code = code

    def __repr__(self):
        return self.code

    def __eq__(self, other):
        return isinstance(other, ExcelError) and other.code == self.code

    def __hash__(self):
        return hash(("err", self.code))


ERR_DIV0 = ExcelError("#DIV/0!")
ERR_VALUE = ExcelError("#VALUE!")
ERR_REF = ExcelError("#REF!")
ERR_NAME = ExcelError("#NAME?")
ERR_NA = ExcelError("#N/A")
ERR_NUM = ExcelError("#NUM!")


class FormulaError(Exception):
    """Формула не разбирается (сохраняется без пересчёта)."""


# ---------------------------------------------------------------- ссылки

_REF_RE = re.compile(
    r"^(?:(?:'((?:[^']|'')+)'!|([^\W\d][\w. ]*)!)?"
    r"\$?([^\W\d]{1,3})\$?(\d{1,7}))$", re.UNICODE)


def parse_ref(text: str):
    """'B3'/'Лист2!B3'/'Мой лист'!B3' → (лист|None, строка, колонка) 0-based."""
    m = _REF_RE.match(text.strip())
    if not m:
        return None
    sheet = m.group(1).replace("''", "'") if m.group(1) is not None \
        else (m.group(2) or None)
    col = 0
    for ch in m.group(3).upper():
        col = col * 26 + (ord(ch) - 64)
    return (sheet, int(m.group(4)) - 1, col - 1)


def col_name(col: int) -> str:
    name = ""
    col += 1
    while col:
        col, rem = divmod(col - 1, 26)
        name = chr(65 + rem) + name
    return name


# ---------------------------------------------------------------- токены

_TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<sq>'(?:[^']|'')+'!)
  | (?P<ref>(?:[^\W\d][\w. ]*\!)?\$?[^\W\d]{1,3}\$?\d{1,7})
  | (?P<func>[^\W\d][\w.]*)(?=\s*\()
  | (?P<bool>TRUE|FALSE)(?![\w.])
  | (?P<ident>[^\W\d][\w.]*)
  | (?P<op><>|<=|>=|[=<>+\-*/^&%(),:])
""", re.X | re.UNICODE)


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if m is None:
            raise FormulaError(
                f"непонятный символ {text[pos]!r} на позиции {pos + 1}")
        pos = m.end()
        kind = m.lastgroup
        value = m.group()
        if kind == "sq":
            tokens.append(("sheet_prefix", value[:-1]))
        elif kind == "str":
            tokens.append(("str", value[1:-1].replace('""', '"')))
        elif kind == "num":
            tokens.append(("num", value))
        elif kind == "func":
            tokens.append(("func", value.upper()))
        elif kind == "bool":
            tokens.append(("bool", value))
        elif kind == "ident":
            tokens.append(("ident", value))
        else:
            tokens.append((kind, value))
    return tokens

# ---------------------------------------------------------------- AST

class Node:
    __slots__ = ()


class Num(Node):
    __slots__ = ("v",)

    def __init__(self, v):
        self.v = v


class Str(Node):
    __slots__ = ("v",)

    def __init__(self, v):
        self.v = v


class Bool(Node):
    __slots__ = ("v",)

    def __init__(self, v):
        self.v = v


class Ref(Node):
    __slots__ = ("sheet", "row", "col")

    def __init__(self, sheet, row, col):
        self.sheet, self.row, self.col = sheet, row, col


class Range(Node):
    __slots__ = ("sheet", "r1", "c1", "r2", "c2")

    def __init__(self, sheet, r1, c1, r2, c2):
        self.sheet, self.r1, self.c1, self.r2, self.c2 = sheet, r1, c1, r2, c2


class Bin(Node):
    __slots__ = ("op", "a", "b")

    def __init__(self, op, a, b):
        self.op, self.a, self.b = op, a, b


class Un(Node):
    __slots__ = ("op", "a")

    def __init__(self, op, a):
        self.op, self.a = op, a


class Call(Node):
    __slots__ = ("name", "args")

    def __init__(self, name, args):
        self.name, self.args = name, args


# ---------------------------------------------------------------- парсер

class _Parser:
    def __init__(self, tokens):
        self.toks = tokens
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def next(self):
        tok = self.peek()
        self.i += 1
        return tok

    def expect_op(self, op):
        kind, value = self.next()
        if kind != "op" or value != op:
            raise FormulaError(f"ожидалось {op!r}")

    def parse(self):
        node = self.expr()
        if self.i != len(self.toks):
            raise FormulaError("лишние символы в конце формулы")
        return node

    def expr(self):
        node = self.concat()
        while True:
            kind, value = self.peek()
            if kind == "op" and value in ("=", "<>", "<", ">", "<=", ">="):
                self.next()
                node = Bin(value, node, self.concat())
            else:
                return node

    def concat(self):
        node = self.add()
        while self.peek() == ("op", "&"):
            self.next()
            node = Bin("&", node, self.add())
        return node

    def add(self):
        node = self.mul()
        while True:
            kind, value = self.peek()
            if kind == "op" and value in ("+", "-"):
                self.next()
                node = Bin(value, node, self.mul())
            else:
                return node

    def mul(self):
        node = self.power()
        while True:
            kind, value = self.peek()
            if kind == "op" and value in ("*", "/"):
                self.next()
                node = Bin(value, node, self.power())
            else:
                return node

    def power(self):
        node = self.unary()
        if self.peek() == ("op", "^"):
            self.next()
            return Bin("^", node, self.power())  # правая ассоциативность
        return node

    def unary(self):
        kind, value = self.peek()
        if kind == "op" and value in ("+", "-"):
            self.next()
            return Un(value, self.unary())
        return self.postfix()

    def postfix(self):
        node = self.primary()
        while self.peek() == ("op", "%"):
            self.next()
            node = Un("%", node)
        return node

    def primary(self):
        kind, value = self.next()
        if kind == "num":
            return Num(float(value))
        if kind == "str":
            return Str(value)
        if kind == "bool":
            return Bool(value == "TRUE")
        if kind == "op" and value == "(":
            node = self.expr()
            self.expect_op(")")
            return node
        if kind == "sheet_prefix":
            # 'Имя листа'!A1 (или диапазон)
            return self._ref_with_sheet(value)
        if kind == "ref":
            first = parse_ref(value)
            if first is None:
                raise FormulaError(f"плохая ссылка {value!r}")
            if self.peek() == ("op", ":"):
                self.next()
                kind2, value2 = self.next()
                second = parse_ref(value2) if kind2 == "ref" else None
                if second is None:
                    raise FormulaError("диапазон: ожидалась ссылка после ':'")
                sheet = first[0] if first[0] is not None else second[0]
                return Range(sheet, first[1], first[2], second[1], second[2])
            return Ref(*first)
        if kind == "func":
            self.expect_op("(")
            args = []
            if self.peek() != ("op", ")"):
                args.append(self.expr())
                while self.peek() in (("op", ","), ("op", ";")):
                    self.next()
                    args.append(self.expr())
            self.expect_op(")")
            return Call(value, args)
        raise FormulaError("неожиданный элемент формулы")

    def _ref_with_sheet(self, sheet):
        # sheet — уже с кавычками ('Лист 1'), как в исходнике
        kind, value = self.next()
        if kind != "ref":
            raise FormulaError("после имени листа ожидалась ссылка")
        first = parse_ref(sheet + "!" + value)
        if first is None:
            raise FormulaError(f"плохая ссылка {value!r}")
        if self.peek() == ("op", ":"):
            self.next()
            kind2, value2 = self.next()
            second = parse_ref(sheet + "!" + value2) if kind2 == "ref" else None
            if second is None:
                raise FormulaError("диапазон: ожидалась ссылка после ':'")
            return Range(first[0], first[1], first[2], second[1], second[2])
        return Ref(*first)


def parse_formula(text: str) -> Node:
    """Тело формулы без ведущего '=' → AST; FormulaError при ошибке разбора."""
    return _Parser(_tokenize(text)).parse()


# ---------------------------------------------------------------- значения

_DATE_RX = re.compile(
    r"^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4}|\d{2})$|^(\d{4})-(\d{1,2})-(\d{1,2})$")


def _date_serial_ymd(y: int, m: int, d: int) -> float:
    try:
        return float(_date_serial(y, m, d))
    except ValueError:
        return ERR_NUM


def parse_date_text(s: str) -> float | ExcelError:
    """Текст-дата (05.03.2024 / 2024-03-05 / 05.03.24) → serial или ERR_VALUE."""
    m = _DATE_RX.match(s.strip())
    if not m:
        return ERR_VALUE
    if m.group(1) is not None:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000 if y < 30 else 1900
    else:
        y, mo, d = int(m.group(4)), int(m.group(5)), int(m.group(6))
    return _date_serial_ymd(y, mo, d)


def to_number(v) -> float:
    """Приведение к числу в духе Excel; ExcelError при неудаче.
    Текст-дата приводится к последовательной дате (A1+1 для дат работает)."""
    if isinstance(v, ExcelError):
        return v
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if v is None:
        return 0.0
    s = str(v).strip().replace(" ", "").replace(",", ".")
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        date_v = parse_date_text(str(v))
        return date_v


def to_text(v) -> str:
    if isinstance(v, ExcelError):
        return v.code
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def to_bool(v) -> bool:
    if isinstance(v, ExcelError):
        return v
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if v is None:
        return False
    s = str(v).strip().upper()
    if s == "TRUE":
        return True
    if s == "FALSE":
        return False
    return ERR_VALUE


def cell_value(raw):
    """Сырое значение ячейки (строка из сетки) → значение движка."""
    if raw is None:
        return None
    if isinstance(raw, str):
        if raw.startswith("="):
            return raw  # формула как есть — вызывающий сам решает
        s = raw.strip()
        if s == "":
            return None
        if s.upper() == "TRUE":
            return True
        if s.upper() == "FALSE":
            return False
        num = s.replace(" ", "").replace(",", ".")
        try:
            f = float(num)
            return int(f) if f.is_integer() and abs(f) < 1e15 else f
        except ValueError:
            return raw
    return raw


def fmt_value(v) -> str:
    """Формат отображения (общий, в духе Excel): без хвостовых нулей."""
    if isinstance(v, ExcelError):
        return v.code
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        if math.isfinite(v) and v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        s = f"{v:.10g}"
        return s
    return str(v)


def fmt_number_for_xml(v) -> str:
    """Число для <v> в XML листа."""
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    return repr(float(v))


# ---------------------------------------------------------------- контекст

class Context:
    """Значения листов для вычисления: name → rows (списки строк значений).
    Активный лист (ссылки без имени) задаётся evaluate_sheet через active —
    порядок вставки словаря решать не должен (гонки загрузки листов)."""

    def __init__(self, sheets: dict[str, list]):
        self.sheets = sheets
        self.active: str | None = None

    def rows(self, sheet: str | None):
        if sheet is None:
            sheet = self.active or next(iter(self.sheets))
        rows = self.sheets.get(sheet)
        if rows is None:
            return ERR_REF
        return rows

    def cell(self, sheet, row, col):
        rows = self.rows(sheet)
        if isinstance(rows, ExcelError):
            return rows
        if 0 <= row < len(rows):
            r = rows[row]
            if 0 <= col < len(r):
                v = r[col]
                # чужой лист может быть ещё сырым — привести на чтении
                return cell_value(v) if isinstance(v, str) else v
        return None


# ---------------------------------------------------------------- вычисление

MAX_RANGE_CELLS = 2_000_000
MAX_PASSES = 24


class _Eval:
    def __init__(self, ctx: Context):
        self.ctx = ctx

    def value(self, node: Node):
        if isinstance(node, (Num, Str, Bool)):
            return node.v
        if isinstance(node, Ref):
            return self.ctx.cell(node.sheet, node.row, node.col)
        if isinstance(node, Range):
            return self.range_values(node)
        if isinstance(node, Un):
            return self.unary(node)
        if isinstance(node, Bin):
            return self.binary(node)
        if isinstance(node, Call):
            return self.call(node)
        return ERR_VALUE

    def range_values(self, node: Range):
        rows = self.ctx.rows(node.sheet)
        if isinstance(rows, ExcelError):
            return rows
        r1, r2 = sorted((node.r1, node.r2))
        c1, c2 = sorted((node.c1, node.c2))
        if (r2 - r1 + 1) * (c2 - c1 + 1) > MAX_RANGE_CELLS:
            return ERR_VALUE
        out = []
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                out.append(rows[r][c] if r < len(rows) and c < len(rows[r]) else None)
        return out

    def unary(self, node: Un):
        v = self.value(node.a)
        if node.op == "%":
            n = to_number(v)
            return n if isinstance(n, ExcelError) else n / 100.0
        n = to_number(v)
        if isinstance(n, ExcelError):
            return n
        return -n if node.op == "-" else n

    def binary(self, node: Bin):
        a = self.value(node.a)
        b = self.value(node.b)
        for v in (a, b):
            if isinstance(v, ExcelError):
                return v
        op = node.op
        if op == "&":
            return to_text(a) + to_text(b)
        if op in ("=", "<>", "<", ">", "<=", ">="):
            return self.compare(op, a, b)
        x = to_number(a)
        y = to_number(b)
        for v in (x, y):
            if isinstance(v, ExcelError):
                return v
        try:
            if op == "+":
                return x + y
            if op == "-":
                return x - y
            if op == "*":
                return x * y
            if op == "/":
                if y == 0:
                    return ERR_DIV0
                return x / y
            if op == "^":
                if x == 0 and y < 0:
                    return ERR_DIV0
                r = math.pow(x, y)
        except (OverflowError, ValueError):
            return ERR_NUM
        if isinstance(r, complex):
            return ERR_NUM
        return r

    @staticmethod
    def compare(op, a, b):
        if isinstance(a, str) or isinstance(b, str):
            x, y = to_text(a).lower(), to_text(b).lower()
        else:
            x, y = a if a is not None else 0, b if b is not None else 0
            if isinstance(x, bool) != isinstance(y, bool):
                x, y = to_bool(a), to_bool(b)
                if isinstance(x, ExcelError) or isinstance(y, ExcelError):
                    return ERR_VALUE
        if op == "=":
            return x == y
        if op == "<>":
            return x != y
        try:
            if op == "<":
                return x < y
            if op == ">":
                return x > y
            if op == "<=":
                return x <= y
            return x >= y
        except TypeError:
            return ERR_VALUE

    # -- функции -----------------------------------------------------------

    def call(self, node: Call):
        fn = _FUNCS.get(node.name)
        if fn is None:
            return ERR_NAME
        try:
            return fn(self, node.args)
        except ZeroDivisionError:
            return ERR_DIV0
        except (ValueError, OverflowError):
            return ERR_NUM
        except (TypeError, IndexError):
            return ERR_VALUE

    def scalar(self, arg):
        """Значение аргумента; диапазон запрещён."""
        v = self.value(arg)
        if isinstance(v, list):
            return ERR_VALUE
        return v

    def flat(self, args, skip_empty: bool = False):
        """Плоский список значений всех аргументов (диапазоны разворачиваются)."""
        out = []
        for arg in args:
            v = self.value(arg)
            if isinstance(v, ExcelError):
                return v
            if isinstance(v, list):
                out.extend(v)
            else:
                out.append(v)
        if skip_empty:
            out = [v for v in out if v is not None and v != ""]
        return out

    def numbers(self, args):
        vals = self.flat(args, skip_empty=True)
        if isinstance(vals, ExcelError):
            return vals
        nums = []
        for v in vals:
            if isinstance(v, str) or isinstance(v, bool):
                continue  # Excel: текст и логические в SUM не считаются
            n = to_number(v)
            if isinstance(n, ExcelError):
                continue  # ошибки в ячейках диапазона игнорируются
            nums.append(n)
        return nums

    def criteria_match(self, v, crit) -> bool:
        if isinstance(crit, str):
            s = crit.strip()
            for op in ("<>", "<=", ">=", "=", "<", ">"):
                if s.startswith(op):
                    rest = s[len(op):]
                    target = cell_value(rest)
                    return _CriteriaOps(op)(v, target)
            target = cell_value(s)
            if isinstance(target, str):
                pat = s.replace("*", "\x00").replace("?", "\x01")
                rx = re.compile(
                    "".join(".*" if ch == "\x00" else "." if ch == "\x01"
                            else re.escape(ch) for ch in pat) + r"\Z",
                    re.S | re.I)
                return bool(rx.match(to_text(v)))
            return _CriteriaOps("=")(v, target)
        return _CriteriaOps("=")(v, crit)

    def cond_values(self, range_node, crit):
        vals = self.value(range_node)
        if not isinstance(vals, list):
            vals = [vals]
        return [v for v in vals if self.criteria_match(v, crit)]


class _CriteriaOps:
    def __init__(self, op):
        self.op = op

    def __call__(self, v, target) -> bool:
        if isinstance(v, str) and isinstance(target, str):
            x, y = v.lower(), target.lower()
        else:
            x, y = to_number(v), to_number(target)
            if isinstance(x, ExcelError) or isinstance(y, ExcelError):
                xv, yv = to_text(v), to_text(target)
                return {"=": xv == yv, "<>": xv != yv}.get(self.op, False)
        try:
            return {"=": x == y, "<>": x != y, "<": x < y,
                    ">": x > y, "<=": x <= y, ">=": x >= y}[self.op]
        except TypeError:
            return False


def _num1(ev: _Eval, args, fn):
    n = to_number(ev.scalar(args[0]))
    if isinstance(n, ExcelError):
        return n
    return fn(n)


def _args_min(ev: _Eval, args, n: int):
    if len(args) < n:
        return ExcelError("#ARGS!")
    return None


# --- математика ---

def _f_sum(ev, args):
    nums = ev.numbers(args)
    return nums if isinstance(nums, ExcelError) else math.fsum(nums)


def _f_product(ev, args):
    nums = ev.numbers(args)
    if isinstance(nums, ExcelError):
        return nums
    out = 1.0
    for n in nums:
        out *= n
    return out


def _f_average(ev, args):
    nums = ev.numbers(args)
    if isinstance(nums, ExcelError):
        return nums
    return ERR_DIV0 if not nums else math.fsum(nums) / len(nums)


def _f_median(ev, args):
    nums = sorted(ev.numbers(args))
    if isinstance(nums, ExcelError):
        return nums
    if not nums:
        return ERR_NUM
    mid = len(nums) // 2
    return nums[mid] if len(nums) % 2 else (nums[mid - 1] + nums[mid]) / 2


def _f_min(ev, args):
    nums = ev.numbers(args)
    return nums if isinstance(nums, ExcelError) else (min(nums) if nums else 0)


def _f_max(ev, args):
    nums = ev.numbers(args)
    return nums if isinstance(nums, ExcelError) else (max(nums) if nums else 0)


def _f_count(ev, args):
    vals = ev.flat(args)
    if isinstance(vals, ExcelError):
        return vals
    return sum(1 for v in vals
               if isinstance(v, (int, float)) and not isinstance(v, bool))


def _f_counta(ev, args):
    vals = ev.flat(args)
    if isinstance(vals, ExcelError):
        return vals
    return sum(1 for v in vals if v is not None and v != "")


def _f_countblank(ev, args):
    vals = ev.flat(args)
    if isinstance(vals, ExcelError):
        return vals
    return sum(1 for v in vals if v is None or v == "")


def _f_round(ev, args):
    err = _args_min(ev, args, 1)
    if err:
        return err
    n = to_number(ev.scalar(args[0]))
    digits = int(to_number(ev.scalar(args[1]))) if len(args) > 1 else 0
    for v in (n, to_number(digits)):
        if isinstance(v, ExcelError):
            return v
    factor = 10.0 ** digits
    # Excel округляет половину от нуля прочь
    scaled = n * factor
    r = math.floor(abs(scaled) + 0.5)
    return math.copysign(r, scaled) / factor


def _f_roundup(ev, args):
    n = to_number(ev.scalar(args[0]))
    digits = int(to_number(ev.scalar(args[1]))) if len(args) > 1 else 0
    factor = 10.0 ** digits
    return math.copysign(math.ceil(abs(n * factor) - 1e-12), n) / factor


def _f_rounddown(ev, args):
    n = to_number(ev.scalar(args[0]))
    digits = int(to_number(ev.scalar(args[1]))) if len(args) > 1 else 0
    factor = 10.0 ** digits
    return math.copysign(math.floor(abs(n * factor) + 1e-12), n) / factor


def _f_mod(ev, args):
    a, b = to_number(ev.scalar(args[0])), to_number(ev.scalar(args[1]))
    for v in (a, b):
        if isinstance(v, ExcelError):
            return v
    if b == 0:
        return ERR_DIV0
    return a - b * math.floor(a / b)


def _f_log(ev, args):
    n = to_number(ev.scalar(args[0]))
    if isinstance(n, ExcelError):
        return n
    base = to_number(ev.scalar(args[1])) if len(args) > 1 else 10.0
    if isinstance(base, ExcelError):
        return base
    if n <= 0 or base <= 0 or base == 1:
        return ERR_NUM
    return math.log(n, base)


def _f_if(ev, args):
    if len(args) < 2:
        return ERR_VALUE
    cond = to_bool(ev.scalar(args[0]))
    if isinstance(cond, ExcelError):
        return cond
    if cond:
        return ev.scalar(args[1])
    return ev.scalar(args[2]) if len(args) > 2 else False


def _f_iferror(ev, args):
    v = ev.scalar(args[0])
    if isinstance(v, ExcelError):
        return ev.scalar(args[1])
    return v


def _f_and(ev, args):
    vals = ev.flat(args, skip_empty=True)
    if isinstance(vals, ExcelError):
        return vals
    result = True
    for v in vals:
        b = to_bool(v)
        if isinstance(b, ExcelError):
            return b
        result = result and b
    return result


def _f_or(ev, args):
    vals = ev.flat(args, skip_empty=True)
    if isinstance(vals, ExcelError):
        return vals
    result = False
    for v in vals:
        b = to_bool(v)
        if isinstance(b, ExcelError):
            return b
        result = result or b
    return result


def _f_not(ev, args):
    b = to_bool(ev.scalar(args[0]))
    return b if isinstance(b, ExcelError) else not b


# --- текст ---

def _f_concat(ev, args):
    vals = ev.flat(args)
    if isinstance(vals, ExcelError):
        return vals
    return "".join(to_text(v) for v in vals)


def _f_left(ev, args):
    s = to_text(ev.scalar(args[0]))
    n = int(to_number(ev.scalar(args[1]))) if len(args) > 1 else 1
    return s[:max(0, n)]


def _f_right(ev, args):
    s = to_text(ev.scalar(args[0]))
    n = int(to_number(ev.scalar(args[1]))) if len(args) > 1 else 1
    return s[len(s) - max(0, n):] if n else ""


def _f_mid(ev, args):
    s = to_text(ev.scalar(args[0]))
    start = int(to_number(ev.scalar(args[1])))
    n = int(to_number(ev.scalar(args[2])))
    if start < 1 or n < 0:
        return ERR_VALUE
    return s[start - 1:start - 1 + n]


def _f_len(ev, args):
    return len(to_text(ev.scalar(args[0])))


def _f_lower(ev, args):
    return to_text(ev.scalar(args[0])).lower()


def _f_upper(ev, args):
    return to_text(ev.scalar(args[0])).upper()


def _f_proper(ev, args):
    s = to_text(ev.scalar(args[0]))
    out, prev_alpha = [], False
    for ch in s:
        out.append(ch.lower() if prev_alpha else ch.upper())
        prev_alpha = ch.isalpha()
    return "".join(out)


def _f_trim(ev, args):
    # Excel TRIM: убирает и повторные пробелы внутри
    return re.sub(r" +", " ", to_text(ev.scalar(args[0])).strip(" "))


def _f_substitute(ev, args):
    s, old = to_text(ev.scalar(args[0])), to_text(ev.scalar(args[1]))
    new = to_text(ev.scalar(args[2]))
    if not old:
        return s
    if len(args) > 3:
        which = int(to_number(ev.scalar(args[3])))
        if which < 1:
            return ERR_VALUE
        idx = -1
        for _ in range(which):
            idx = s.find(old, idx + 1)
            if idx < 0:
                return s
        return s[:idx] + new + s[idx + len(old):]
    return s.replace(old, new)


def _f_replace(ev, args):
    s = to_text(ev.scalar(args[0]))
    start = int(to_number(ev.scalar(args[1])))
    n = int(to_number(ev.scalar(args[2])))
    new = to_text(ev.scalar(args[3]))
    if start < 1 or n < 0:
        return ERR_VALUE
    return s[:start - 1] + new + s[start - 1 + n:]


def _find_impl(s: str, frag: str, start: int):
    if start < 1:
        return ERR_VALUE
    idx = s.find(frag, start - 1)
    return idx + 1 if idx >= 0 else ERR_VALUE


def _f_find(ev, args):
    # Excel: FIND(искомое, где искать, [с какой позиции])
    return _find_impl(to_text(ev.scalar(args[1])),
                      to_text(ev.scalar(args[0])),
                      int(to_number(ev.scalar(args[2]))) if len(args) > 2 else 1)


def _f_search(ev, args):
    return _find_impl(to_text(ev.scalar(args[1])).lower(),
                      to_text(ev.scalar(args[0])).lower(),
                      int(to_number(ev.scalar(args[2]))) if len(args) > 2 else 1)


def _f_rept(ev, args):
    s = to_text(ev.scalar(args[0]))
    n = int(to_number(ev.scalar(args[1])))
    if n < 0 or n * len(s) > 100_000:
        return ERR_VALUE
    return s * n


def _f_value(ev, args):
    return to_number(ev.scalar(args[0]))


_DATE_TOKENS = (("ГГГГ", "%Y"), ("YYYY", "%Y"), ("ГГ", "%y"), ("YY", "%y"),
                ("ММ", "%m"), ("MM", "%m"), ("ДД", "%d"), ("DD", "%d"),
                ("ЧЧ", "%H"), ("HH", "%H"), ("М", "%m"), ("M", "%m"),
                ("Д", "%d"), ("D", "%d"))


def _f_text(ev, args):
    """TEXT: числовые маски (0, 0.00, #,##0, 0%) и дата-маски (ДД.ММ.ГГГГ)."""
    v = ev.scalar(args[0])
    if isinstance(v, ExcelError):
        return v
    fmt = to_text(ev.scalar(args[1]))
    if re.search(r"[ДГМЧDYM]", fmt):  # дата-маска
        n = to_number(v)
        if isinstance(n, ExcelError):
            return n
        pattern = fmt
        for token, code in _DATE_TOKENS:
            pattern = pattern.replace(token, code)
        try:
            return _from_serial(n).strftime(pattern)
        except (ValueError, OverflowError):
            return ERR_VALUE
    # числовая маска: 0 / 0.00 / #,##0 / 0%
    n = to_number(v)
    if isinstance(n, ExcelError):
        return n
    percent = "%" in fmt
    if percent:
        n *= 100.0
    integer_part, _, frac_part = fmt.partition(".")
    decimals = len("".join(ch for ch in frac_part if ch in "0#"))
    thousands = "#,##" in fmt or ",#" in fmt
    body = f"{abs(n):.{decimals}f}"
    if decimals:
        whole, _, frac = body.partition(".")
    else:
        whole, frac = body, ""
    if thousands:
        whole = f"{int(whole):,}".replace(",", " ")
    sign = "-" if n < 0 else ""
    out = whole + ("." + frac if decimals else "")
    return sign + out + ("%" if percent else "")


# --- даты ---

def _f_today(_ev, args):
    today = _dt.date.today()
    return float(_date_serial(today.year, today.month, today.day))


def _f_now(_ev, args):
    now = _dt.datetime.now()
    base = _date_serial(now.year, now.month, now.day)
    frac = (now.hour * 3600 + now.minute * 60 + now.second) / 86400.0
    return base + frac


def _date_serial(y, m, d) -> int:
    # последовательная дата Excel (1900-система, с «висячей» високосной 1900)
    days = (_dt.date(y, m, d) - _dt.date(1899, 12, 30)).days
    return days + 2 if days >= 60 else days


def _from_serial(serial: float) -> _dt.date:
    days = int(serial) - 2 if int(serial) >= 61 else int(serial)
    return _dt.date(1899, 12, 30) + _dt.timedelta(days=days)


def _f_year(ev, args):
    return _from_serial(to_number(ev.scalar(args[0]))).year


def _f_month(ev, args):
    return _from_serial(to_number(ev.scalar(args[0]))).month


def _f_day(ev, args):
    return _from_serial(to_number(ev.scalar(args[0]))).day


def _f_date(ev, args):
    y = int(to_number(ev.scalar(args[0])))
    m = int(to_number(ev.scalar(args[1])))
    d = int(to_number(ev.scalar(args[2])))
    if m < 1:
        y += (m - 12) // 12
        m = m % 12 or 12
    elif m > 12:
        y += (m - 1) // 12
        m = (m - 1) % 12 + 1
    try:
        return float(_date_serial(y, m, d))
    except ValueError:
        return ERR_NUM


# --- условия/поиск ---

def _f_countif(ev, args):
    vals = ev.cond_values(args[0], ev.scalar(args[1]))
    return len(vals)


def _f_sumif(ev, args):
    crit_vals = ev.value(args[0])
    if not isinstance(crit_vals, list):
        crit_vals = [crit_vals]
    matched_idx = [i for i, v in enumerate(crit_vals)
                   if ev.criteria_match(v, ev.scalar(args[1]))]
    sum_vals = ev.value(args[2]) if len(args) > 2 else crit_vals
    if not isinstance(sum_vals, list):
        sum_vals = [sum_vals]
    total = 0.0
    for i in matched_idx:
        if i < len(sum_vals):
            n = to_number(sum_vals[i])
            if not isinstance(n, ExcelError):
                total += n
    return total


def _f_averageif(ev, args):
    crit_vals = ev.value(args[0])
    if not isinstance(crit_vals, list):
        crit_vals = [crit_vals]
    matched_idx = [i for i, v in enumerate(crit_vals)
                   if ev.criteria_match(v, ev.scalar(args[1]))]
    sum_vals = ev.value(args[2]) if len(args) > 2 else crit_vals
    if not isinstance(sum_vals, list):
        sum_vals = [sum_vals]
    nums = []
    for i in matched_idx:
        if i < len(sum_vals):
            n = to_number(sum_vals[i])
            if not isinstance(n, ExcelError):
                nums.append(n)
    return ERR_DIV0 if not nums else math.fsum(nums) / len(nums)


def _f_vlookup(ev, args):
    key = ev.scalar(args[0])
    col = int(to_number(ev.scalar(args[2])))
    approx = to_bool(ev.scalar(args[3])) if len(args) > 3 else True
    if isinstance(col, ExcelError) or isinstance(approx, ExcelError):
        return ERR_VALUE
    grid = ev.value(args[1])
    if isinstance(grid, ExcelError):
        return grid
    if isinstance(args[1], Range):
        r1, r2 = sorted((args[1].r1, args[1].r2))
        c1, c2 = sorted((args[1].c1, args[1].c2))
        grid = [[ev.ctx.cell(args[1].sheet, r, c) for c in range(c1, c2 + 1)]
                for r in range(r1, r2 + 1)]
    elif not isinstance(grid, list):
        grid = [[grid]]
    if not grid or col < 1 or col > len(grid[0]):
        return ERR_REF
    if not approx:
        for row in grid:
            if _cell_eq(row[0], key):
                return row[col - 1]
        return ERR_NA
    best = None
    for row in grid:
        if isinstance(row[0], str) or isinstance(row[0], bool):
            continue
        n1 = to_number(row[0])
        if isinstance(n1, ExcelError):
            continue
        n2 = to_number(key)
        if not isinstance(n2, ExcelError) and n1 <= n2:
            best = row
    return best[col - 1] if best else ERR_NA


def _cell_eq(a, b) -> bool:
    if isinstance(a, str) != isinstance(b, str):
        return False
    if isinstance(a, str):
        return a.lower() == b.lower()
    na, nb = to_number(a), to_number(b)
    if isinstance(na, ExcelError) or isinstance(nb, ExcelError):
        return False
    return na == nb


def _f_hlookup(ev, args):
    key = ev.scalar(args[0])
    rng = ev.value(args[1])
    grid = rng if isinstance(rng, list) else [[rng]]
    if isinstance(args[1], Range):
        r1, r2 = sorted((args[1].r1, args[1].r2))
        c1, c2 = sorted((args[1].c1, args[1].c2))
        grid = [[ev.ctx.cell(args[1].sheet, r, c) for c in range(c1, c2 + 1)]
                for r in range(r1, r2 + 1)]
    row_i = int(to_number(ev.scalar(args[2])))
    approx = to_bool(ev.scalar(args[3])) if len(args) > 3 else True
    if isinstance(row_i, ExcelError) or isinstance(approx, ExcelError):
        return ERR_VALUE
    if not grid or row_i < 1 or row_i > len(grid):
        return ERR_REF
    header = grid[0]
    if not approx:
        for c, v in enumerate(header):
            if _cell_eq(v, key) and c < len(grid[row_i - 1]):
                return grid[row_i - 1][c]
        return ERR_NA
    best_c = None
    for c, v in enumerate(header):
        n1 = to_number(v)
        if isinstance(n1, ExcelError) or isinstance(v, str):
            continue
        n2 = to_number(key)
        if not isinstance(n2, ExcelError) and n1 <= n2:
            best_c = c
    if best_c is None:
        return ERR_NA
    return grid[row_i - 1][best_c] if best_c < len(grid[row_i - 1]) else ERR_REF


def _f_index(ev, args):
    rng = ev.value(args[0])
    grid = rng if isinstance(rng, list) else [[rng]]
    if isinstance(args[0], Range):
        r1, r2 = sorted((args[0].r1, args[0].r2))
        c1, c2 = sorted((args[0].c1, args[0].c2))
        grid = [[ev.ctx.cell(args[0].sheet, r, c) for c in range(c1, c2 + 1)]
                for r in range(r1, r2 + 1)]
    if not grid:
        return ERR_REF
    row_n = int(to_number(ev.scalar(args[1]))) if len(args) > 1 else 0
    col_n = int(to_number(ev.scalar(args[2]))) if len(args) > 2 else 0
    for n in (row_n, col_n):
        if isinstance(to_number(n), ExcelError):
            return ERR_VALUE
    if row_n == 0 and col_n == 0:
        return ERR_VALUE
    if row_n == 0:
        return grid[0][col_n - 1] if grid and col_n - 1 < len(grid[0]) else ERR_REF
    if col_n == 0:
        col_n = 1
    if row_n < 1 or row_n > len(grid) or col_n < 1 or col_n > len(grid[row_n - 1]):
        return ERR_REF
    return grid[row_n - 1][col_n - 1]


def _f_match(ev, args):
    key = ev.scalar(args[0])
    vals = ev.value(args[1])
    if not isinstance(vals, list):
        vals = [vals]
    mtype = int(to_number(ev.scalar(args[2]))) if len(args) > 2 else 1
    if isinstance(mtype, ExcelError):
        return ERR_VALUE
    if mtype == 0:
        for i, v in enumerate(vals):
            if _cell_eq(v, key):
                return i + 1
        return ERR_NA
    best = None
    if mtype > 0:
        nk = to_number(key)
        if isinstance(nk, ExcelError):
            nk = to_text(key).lower()
            for i, v in enumerate(vals):
                if isinstance(v, str) and v.lower() <= nk:
                    best = i
        else:
            for i, v in enumerate(vals):
                if isinstance(v, str) or isinstance(v, bool):
                    continue
                nv = to_number(v)
                if not isinstance(nv, ExcelError) and nv <= nk:
                    best = i
    else:
        nk = to_number(key)
        for i, v in enumerate(vals):
            if isinstance(v, str) or isinstance(v, bool):
                continue
            nv = to_number(v)
            if not isinstance(nv, ExcelError) and nv >= nk:
                best = i
            elif nv < nk:
                break
    return best + 1 if best is not None else ERR_NA


def _f_row(ev, args):
    if not args:
        return ERR_VALUE
    node = args[0]
    if isinstance(node, Ref):
        return node.row + 1
    if isinstance(node, Range):
        return node.r1 + 1
    return ERR_VALUE


def _f_column(ev, args):
    if not args:
        return ERR_VALUE
    node = args[0]
    if isinstance(node, Ref):
        return node.col + 1
    if isinstance(node, Range):
        return node.c1 + 1
    return ERR_VALUE


def _f_rows(_ev, args):
    node = args[0]
    if isinstance(node, Range):
        return abs(node.r2 - node.r1) + 1
    return 1


def _f_columns(_ev, args):
    node = args[0]
    if isinstance(node, Range):
        return abs(node.c2 - node.c1) + 1
    return 1


def _f_isnumber(ev, args):
    v = ev.scalar(args[0])
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _f_istext(ev, args):
    return isinstance(ev.scalar(args[0]), str)


def _f_isblank(ev, args):
    v = ev.scalar(args[0])
    return v is None or v == ""


def _f_iserror(ev, args):
    return isinstance(ev.scalar(args[0]), ExcelError)


def _f_n(ev, args):
    v = ev.scalar(args[0])
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return v
    return 0.0


def _power_fn(ev, args):
    return ev.binary(Bin("^", args[0], args[1]))


_FUNCS: dict[str, object] = {
    "SUM": _f_sum, "PRODUCT": _f_product, "AVERAGE": _f_average,
    "MEDIAN": _f_median, "MIN": _f_min, "MAX": _f_max,
    "COUNT": _f_count, "COUNTA": _f_counta, "COUNTBLANK": _f_countblank,
    "ROUND": _f_round, "ROUNDUP": _f_roundup, "ROUNDDOWN": _f_rounddown,
    "ABS": lambda ev, a: _num1(ev, a, abs),
    "SIGN": lambda ev, a: _num1(ev, a, lambda n: (n > 0) - (n < 0)),
    "INT": lambda ev, a: _num1(ev, a, math.floor),
    "MOD": _f_mod,
    "SQRT": lambda ev, a: _num1(ev, a, math.sqrt),
    "POWER": _power_fn,
    "EXP": lambda ev, a: _num1(ev, a, math.exp),
    "LN": lambda ev, a: _num1(ev, a, math.log),
    "LOG": _f_log,
    "LOG10": lambda ev, a: _num1(ev, a, math.log10),
    "PI": lambda ev, a: math.pi,
    "IF": _f_if, "IFERROR": _f_iferror,
    "AND": _f_and, "OR": _f_or, "NOT": _f_not,
    "TRUE": lambda ev, a: True, "FALSE": lambda ev, a: False,
    "CONCAT": _f_concat, "CONCATENATE": _f_concat,
    "LEFT": _f_left, "RIGHT": _f_right, "MID": _f_mid, "LEN": _f_len,
    "LOWER": _f_lower, "UPPER": _f_upper, "PROPER": _f_proper,
    "TRIM": _f_trim, "SUBSTITUTE": _f_substitute, "REPLACE": _f_replace,
    "FIND": _f_find, "SEARCH": _f_search, "REPT": _f_rept,
    "VALUE": _f_value, "TEXT": _f_text,
    "TODAY": _f_today, "NOW": _f_now,
    "YEAR": _f_year, "MONTH": _f_month, "DAY": _f_day, "DATE": _f_date,
    "COUNTIF": _f_countif, "SUMIF": _f_sumif, "AVERAGEIF": _f_averageif,
    "VLOOKUP": _f_vlookup, "HLOOKUP": _f_hlookup,
    "INDEX": _f_index, "MATCH": _f_match,
    "ROW": _f_row, "COLUMN": _f_column, "ROWS": _f_rows, "COLUMNS": _f_columns,
    "ISNUMBER": _f_isnumber, "ISTEXT": _f_istext, "ISBLANK": _f_isblank,
    "ISERROR": _f_iserror, "N": _f_n,
}


# ---------------------------------------------------------------- листы

def evaluate_sheet(rows, ctx: Context, sheet_name: str,
                   seed: dict | None = None) -> tuple[dict, int, int, set]:
    """Пересчитать все формулы листа.

    rows — сырые строки листа (строки-списки, формулы строками "=…");
    ctx — значения всех листов (для межлистовых ссылок); sheet_name — имя
    этого листа в ctx; seed — прежние кэшированные значения {(r,c): v}.

    Возвращает (values, computed, failed, unstable):
      values   — {(r,c): значение} пересчитанных формул (последнее приближение);
      computed — число формул в values;
      failed   — {(r,c)} формулы, которые не разобрались (прежний кэш
                 сохраняется вызывающим);
      unstable — {(r,c)} циклы/нестабильные формулы: их value не годится,
                 вызывающий подставляет прежний кэш из seed.
    """
    ctx.sheets[sheet_name] = rows = [
        [cell_value(v) for v in row] for row in rows]
    ctx.active = sheet_name  # ссылки без имени листа — на этот лист
    raw_formula_coords = {
        (r, c) for r, row in enumerate(rows)
        for c, v in enumerate(row)
        if isinstance(v, str) and v.startswith("=")}
    formulas: dict[tuple, Node] = {}
    failed: set = set()
    for r, row in enumerate(rows):
        for c, raw in enumerate(row):
            if not (isinstance(raw, str) and raw.startswith("=")):
                continue
            try:
                formulas[(r, c)] = parse_formula(raw[1:])
            except FormulaError:
                failed.add((r, c))
    # структурные циклы (A1=A2, A2=A1) численным повтором не ловятся
    cyclic = _find_cycles(formulas, sheet_name)
    unstable: set = set(cyclic)
    for coord in cyclic:
        del formulas[coord]
    values: dict[tuple, object] = dict(seed or {})
    if formulas:
        for _pass in range(MAX_PASSES):
            ctx.sheets[sheet_name] = _merged_rows(rows, values, set(formulas))
            changed = 0
            for coord, ast in formulas.items():
                v = _Eval(ctx).value(ast)
                if isinstance(v, float) and not math.isfinite(v):
                    v = ERR_NUM
                if v != values.get(coord):
                    changed += 1
                values[coord] = v
            if changed == 0:
                break
        if changed:  # не сошлось (цикл между листами) — прежний кэш надёжнее
            unstable.update(formulas)
            for coord in formulas:
                values.pop(coord, None)
    # циклы не записываем — вызывающий сохранит прежний кэш (seed);
    # seed'ы не-формульных ячеек наружу не отдаём
    for coord in unstable:
        values.pop(coord, None)
    values = {k: v for k, v in values.items() if k in raw_formula_coords}
    return values, len(values), failed, unstable


def _merged_rows(rows, values: dict, formula_coords: set) -> list:
    """Строки с подставленными вычисленными значениями формул (для ссылок).
    Seed-кэши неконформульных ячеек в values не пробрасываются в строки —
    иначе они затирали бы свежие правки данных."""
    out = [list(r) for r in rows]
    if formula_coords:
        max_r = max(r for r, _c in formula_coords)
        while len(out) <= max_r:
            out.append([])
        for r, c in formula_coords:
            while len(out[r]) <= c:
                out[r].append(None)
        for r, c in formula_coords:
            out[r][c] = values.get((r, c))
    return out


def _iter_refs(node: Node):
    """Ссылки AST: Ref-координаты и прямоугольники Range (лист — не фильтр)."""
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, Ref):
            yield (n.sheet, n.row, n.col, n.row, n.col)
        elif isinstance(n, Range):
            yield (n.sheet, min(n.r1, n.r2), min(n.c1, n.c2),
                   max(n.r1, n.r2), max(n.c1, n.c2))
        elif isinstance(n, Bin):
            stack += (n.a, n.b)
        elif isinstance(n, Un):
            stack.append(n.a)
        elif isinstance(n, Call):
            stack += n.args


MAX_GRAPH_RANGE = 4096  # огромные диапазоны в граф зависимостей не кладём


def _find_cycles(formulas: dict, sheet_name: str) -> set:
    """Координаты формул, входящих в циклы ссылок этого же листа."""
    deps: dict[tuple, set] = {}
    for coord, ast in formulas.items():
        out = set()
        for sheet, r1, c1, r2, c2 in _iter_refs(ast):
            if (sheet is not None and sheet != sheet_name) or \
                    (r2 - r1 + 1) * (c2 - c1 + 1) > MAX_GRAPH_RANGE:
                continue
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    out.add((r, c))
        deps[coord] = out & formulas.keys()
    color: dict[tuple, int] = {}  # 1 — в стеке, 2 — готово
    cyclic: set = set()

    for start in deps:
        if color.get(start):
            continue
        stack = [(start, iter(sorted(deps[start])))]
        color[start] = 1
        path = [start]
        while stack:
            node, it = stack[-1]
            advanced = False
            for child in it:
                if child not in deps:
                    continue
                state = color.get(child, 0)
                if state == 1:  # цикл: всё от child до вершины стека
                    idx = path.index(child)
                    cyclic.update(path[idx:])
                    break
                if state == 0:
                    color[child] = 1
                    path.append(child)
                    stack.append((child, iter(sorted(deps[child]))))
                    advanced = True
                    break
            if advanced:
                continue
            color[node] = 2
            path.pop()
            stack.pop()
    return cyclic


def display_value(raw, values: dict, row: int, col: int) -> str:
    """Что показать в ячейке: вычисленное значение формулы или сырое."""
    v = values.get((row, col))
    if v is not None or (row, col) in values:
        return fmt_value(v)
    return raw
