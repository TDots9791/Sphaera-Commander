"""Текстовые утилиты: определение бинарности, кодировки, hex, превью.

Общий код просмотрщика и поиска; без виджетов Qt.
"""

from __future__ import annotations

TEXT_LIMIT = 16 * 1024 * 1024   # показываем не больше 16 МиБ текста
HEX_LIMIT = 2 * 1024 * 1024     # и 2 МиБ hex-обзора
HEX_ROW = 16


def looks_binary(raw: bytes) -> bool:
    chunk = raw[:8192]
    if not chunk:
        return False
    # NUL и управляющие байты (кроме \t \n \f \r) — признак бинарных данных
    suspicious = sum(1 for b in chunk if b < 9 or 14 <= b <= 31 or b == 11 or b == 12)
    return suspicious / len(chunk) > 0.05


def detect_decode(raw: bytes) -> tuple[str, str]:
    """(текст, имя кодировки)."""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16"), "utf-16"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp1251"), "cp1251"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def hexdump(raw: bytes) -> str:
    lines = []
    for off in range(0, len(raw), HEX_ROW):
        chunk = raw[off:off + HEX_ROW]
        hex_part = " ".join(f"{b:02x}" for b in chunk).ljust(HEX_ROW * 3 - 1)
        text = "".join(chr(b) if 32 <= b < 127 else "·" for b in chunk)
        lines.append(f"{off:08x}  {hex_part}  |{text}|")
    return "\n".join(lines)


def text_preview(path: str, limit: int = TEXT_LIMIT) -> tuple[str, str, bool]:
    """(содержимое, кодировка, обрезано ли)."""
    with open(path, "rb") as f:
        raw = f.read(limit + 1)
    truncated = len(raw) > limit
    raw = raw[:limit]
    text, enc = detect_decode(raw)
    if truncated:
        text += (f"\n\n[… показаны первые {limit // (1024 * 1024)} МиБ "
                 f"файла — остальное скрыто …]")
    return text, enc, truncated
