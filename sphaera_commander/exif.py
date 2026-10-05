"""Чтение EXIF у JPEG своими силами, без внешних зависимостей.

Достаточно для переименования фото по дате съёмки: APP1-сегмент → TIFF-заголовок
→ IFD0 → ExifIFD (tag 0x8769) → DateTimeOriginal (0x9003)/DateTime (0x0132).
Строковые теги читаются как ASCII; прочие типы пропускаются. Ошибка разбора
— ValueError; «тега нет» — None.
"""

from __future__ import annotations

import os
import struct

JPG_EXTS = (".jpg", ".jpeg")

SOI = 0xFFD8
APP1 = 0xFFE1
SOS = 0xFFDA

TYPE_ASCII = 2
TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}


def is_jpeg(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in JPG_EXTS


def _app1_segment(data: bytes) -> bytes | None:
    """Полезная нагрузка APP1 (Exif) из головы JPEG; None, если нет."""
    if len(data) < 4 or struct.unpack(">H", data[:2])[0] != SOI:
        raise ValueError("не JPEG")
    pos = 2
    while pos + 4 <= len(data):
        marker, length = struct.unpack(">HH", data[pos:pos + 4])
        if marker == SOS or marker == 0xFFD9:
            return None  # до данных изображения EXIF не встретился
        if not 0xFFE0 <= marker <= 0xFFEF:
            pos += 2
            continue
        seg = data[pos + 4:pos + 2 + length]
        if marker == APP1 and seg.startswith(b"Exif\x00\x00"):
            return seg[6:]
        pos += 2 + length
    return None


def _ifd_entries(tiff: bytes, ifd_offset: int, le: bool):
    """Записи IFD: [(tag, typ, count, value_bytes)] (до 4 байт — прямо тут)."""
    end = "<" if le else ">"
    if ifd_offset + 2 > len(tiff):
        return
    (count,) = struct.unpack(end + "H", tiff[ifd_offset:ifd_offset + 2])
    entry_base = ifd_offset + 2
    for i in range(min(count, 512)):
        off = entry_base + i * 12
        if off + 12 > len(tiff):
            return
        tag, typ, nvals = struct.unpack(end + "HHI", tiff[off:off + 8])
        size = nvals * TYPE_SIZES.get(typ, 1)
        if size <= 4:
            value = tiff[off + 8:off + 8 + size]
        else:
            (value_off,) = struct.unpack(end + "I", tiff[off + 8:off + 12])
            value = tiff[value_off:value_off + size]
        yield tag, typ, nvals, value


def _ifd_strings(tiff: bytes, ifd_offset: int, wanted: set[int],
                 le: bool) -> dict[int, str]:
    """Строковые (ASCII) теги одного IFD: {tag: значение}."""
    out: dict[int, str] = {}
    for tag, typ, nvals, value in _ifd_entries(tiff, ifd_offset, le):
        if typ == TYPE_ASCII and tag in wanted:
            out[tag] = value.split(b"\x00", 1)[0].decode("ascii",
                                                         errors="replace")
    return out


def _sub_ifd_offset(tiff: bytes, ifd_offset: int, le: bool) -> int | None:
    """Смещение Exif-под-IFD (tag 0x8769, тип LONG) или None."""
    end = "<" if le else ">"
    for tag, typ, _n, value in _ifd_entries(tiff, ifd_offset, le):
        if tag == 0x8769 and typ == 4 and len(value) >= 4:
            (off,) = struct.unpack(end + "I", value[:4])
            return off
    return None


def exif_datetime_original(path: str) -> str | None:
    """'YYYY:MM:DD HH:MM:SS' даты съёмки или None (тега/EXIF нет).

    ValueError — файл не JPEG или EXIF битый (вызывающий откатывается на mtime).
    """
    with open(path, "rb") as f:
        head = f.read(256 * 1024)  # EXIF живёт в первых сегментах
    tiff = _app1_segment(head)
    if tiff is None:
        return None
    if len(tiff) < 8:
        raise ValueError("битый TIFF-заголовок EXIF")
    if tiff[:2] == b"II":
        le = True
    elif tiff[:2] == b"MM":
        le = False
    else:
        raise ValueError("битый TIFF-заголовок EXIF")
    (ifd0_off,) = struct.unpack("<I" if le else ">I", tiff[4:8])
    strings: dict[int, str] = {}
    sub_off = _sub_ifd_offset(tiff, ifd0_off, le)
    if sub_off is not None:
        # DateTimeOriginal, DateTimeDigitized (Exif-IFD)
        strings.update(_ifd_strings(tiff, sub_off,
                                    {0x9003, 0x9004, 0x0132}, le))
    # некоторые камеры пишут DateTime прямо в IFD0
    strings.update(_ifd_strings(tiff, ifd0_off, {0x9003, 0x0132}, le))
    for tag in (0x9003, 0x9004, 0x0132):  # съёмка → оцифровка → изменение
        value = strings.get(tag)
        if value and len(value) >= 19:
            return value[:19]
    return None


def exif_stamp(path: str, fmt: str = "%Y-%m-%d_%H%M%S",
               fallback_mtime: bool = True) -> str | None:
    """Дата съёмки в виде строки шаблона; без EXIF — mtime (или None)."""
    import time as _time

    raw = None
    if is_jpeg(path) and os.path.exists(path):
        try:
            raw = exif_datetime_original(path)
        except (ValueError, OSError):
            raw = None
    if raw is None:
        if not fallback_mtime:
            return None
        stamp = _time.localtime(os.path.getmtime(path))
    else:
        stamp = _time.strptime(raw, "%Y:%m:%d %H:%M:%S")
    return _time.strftime(fmt, stamp)
