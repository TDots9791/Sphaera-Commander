"""Наследуемые форматы: RTF, XLS (Excel 97-2003), DOC (Word 97-2003).

Библиотеки — свободные (MIT): striprtf (чтение RTF), xlrd (чтение XLS),
xlwt (запись XLS). Двоичный DOC читается внешними свободными утилитами
antiword/catdoc (GPL, вызываются процессом); файлы Word, сохранённые как
RTF (начинаются с {\rtf), читаются встроенно. Запись двоичного DOC без
LibreOffice невозможна — сохранение идёт в RTF/DOCX по выбору.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from .textutil import text_preview

OLE_MAGIC = b"\xd0\xcf\x11\xe0"


def rtf_to_text(path: str) -> str:
    from striprtf.striprtf import rtf_to_text as _rtf_to_text

    with open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1251", errors="replace")
    return _rtf_to_text(text)


def save_rtf(path: str, paragraphs: list[str]) -> None:
    """Простой RTF-писатель: абзацы + юникод-эскейпы (форматирование упрощается)."""
    def esc(text: str) -> str:
        out = []
        for ch in text:
            if ch in "\\{}":
                out.append("\\" + ch)
            elif ord(ch) > 127:
                out.append(f"\\u{ord(ch)}?")
            else:
                out.append(ch)
        return "".join(out)

    body = "\n".join(r"\pard " + esc(line) + r"\par" for line in paragraphs)
    content = (r"{\rtf1\ansi\deff0{\fonttbl{\f0 Arial;}}"
               r"\f0\fs22 " + body + "\n}")
    fd_tmp = path + ".sc_tmp"
    with open(fd_tmp, "w", encoding="ascii", errors="replace") as f:
        f.write(content)
    os.replace(fd_tmp, path)


def doc_to_text(path: str) -> str:
    """Текст DOC: RTF-файлы — встроенно; двоичные — antiword/catdoc."""
    with open(path, "rb") as f:
        head = f.read(8)
    if head.startswith(b"{\\rtf"):
        return rtf_to_text(path)
    if not head.startswith(OLE_MAGIC):
        # не OLE и не RTF — пробуем как текст
        text, _enc = text_preview(path, 16 * 1024 * 1024)
        return text
    tool = shutil.which("antiword") or shutil.which("catdoc")
    if not tool:
        raise ValueError(
            "двоичный DOC: не найден antiword или catdoc "
            "(например: sudo dnf install antiword); "
            "либо сохраните документ как .docx/.rtf")
    cmd = [tool]
    if tool.endswith("catdoc"):
        cmd += ["-d", "utf-8"]
    cmd += ["--", path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"{os.path.basename(tool)}: {exc}") from exc
    if proc.returncode != 0:
        raise ValueError((proc.stderr or proc.stdout).strip()
                         or "не удалось прочитать DOC")
    return proc.stdout


def xls_sheets(path: str) -> list[tuple[str, list[list[str]]]]:
    """Листы XLS: строки значений (формулы — кэшированными значениями,
    даты — в виде даты/времени)."""
    import xlrd

    book = xlrd.open_workbook(path)
    try:
        sheets = []
        for ws in book.sheets():
            rows: list[list[str]] = []
            for r in range(ws.nrows):
                row = []
                for c in range(ws.ncols):
                    cell = ws.cell(r, c)
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        try:
                            dt = xlrd.xldate.xldate_as_datetime(
                                cell.value, book.datemode)
                            row.append(dt.strftime("%d.%m.%Y %H:%M:%S")
                                       .replace(" 00:00:00", ""))
                        except (ValueError, OverflowError):
                            row.append(str(cell.value))
                    elif cell.ctype == xlrd.XL_CELL_NUMBER:
                        value = cell.value
                        row.append(str(int(value))
                                   if float(value).is_integer() else str(value))
                    elif cell.ctype == xlrd.XL_CELL_EMPTY:
                        row.append("")
                    else:
                        row.append(str(cell.value))
                while row and row[-1] == "":
                    row.pop()
                rows.append(row)
            sheets.append((ws.name, rows))
        return sheets
    finally:
        book.release_resources()


def xls_save(path: str, sheets: list[tuple[str, list[list[str]]]]) -> None:
    """Перезаписать XLS значениями (формулы и стили не сохраняются)."""
    import xlwt

    book = xlwt.Workbook()
    for name, rows in sheets:
        safe_name = (name or "Лист")[:31].replace("[", "(").replace("]", ")")
        ws = book.add_sheet(safe_name)
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                ws.write(r, c, value)
    tmp = path + ".sc_tmp"
    book.save(tmp)
    os.replace(tmp, path)
