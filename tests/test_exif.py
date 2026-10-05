"""Тесты EXIF-парсера (свой, без зависимостей): синтетический JPEG
с APP1/Exif, даты съёмки, откат на mtime, битые файлы."""

import os
import shutil
import struct
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sphaera_commander import exif  # noqa: E402


def build_jpeg(path: str, datetime_original: str | None,
               datetime_ifd0: str | None = None, byte_order: str = "II") -> None:
    """Минимальный JPEG с APP1/Exif: IFD0 (указатель Exif-IFD и/или DateTime)
    → Exif-IFD (DateTimeOriginal) → строковые данные."""
    end = "<" if byte_order == "II" else ">"

    def pack(fmt, *vals):
        return struct.pack(end + fmt, *vals)

    has_exif_ifd = datetime_original is not None
    ifd0_entries = []
    if has_exif_ifd:
        ifd0_entries.append((0x8769, 4, 1, None))  # LONG, офсет Exif-IFD
    ifd0_data = None
    if datetime_ifd0 is not None:
        ifd0_data = datetime_ifd0.encode("ascii") + b"\x00"
        ifd0_entries.append((0x0132, 2, len(ifd0_data), ifd0_data))

    ifd0_size = 2 + len(ifd0_entries) * 12 + 4
    exif_off = 8 + ifd0_size
    exif_size = (2 + 1 * 12 + 4) if has_exif_ifd else 0
    data_base = exif_off + exif_size
    dto_raw = (datetime_original.encode("ascii") + b"\x00") \
        if has_exif_ifd else b""

    tiff = byte_order.encode("ascii") + pack("H", 42) + pack("I", 8)
    tiff += pack("H", len(ifd0_entries))
    exif_pointer_slot = None
    for tag, typ, count, value in ifd0_entries:
        if value is None:
            exif_pointer_slot = len(tiff) + 8
            tiff += pack("HHI", tag, typ, count) + b"\x00\x00\x00\x00"
        elif len(value) <= 4:
            tiff += pack("HHI", tag, typ, count) + value.ljust(4, b"\x00")
        else:
            tiff += pack("HHI", tag, typ, count) + pack("I", data_base)
    tiff += pack("I", 0)
    if has_exif_ifd:
        tiff += pack("H", 1)
        tiff += pack("HHI", 0x9003, 2, len(dto_raw)) + pack("I", data_base)
        tiff += pack("I", 0)
    if ifd0_data is not None:
        tiff += ifd0_data
    if has_exif_ifd:
        tiff += dto_raw
    if exif_pointer_slot is not None:
        tiff = (tiff[:exif_pointer_slot] + pack("I", exif_off)
                + tiff[exif_pointer_slot + 4:])

    app1 = b"Exif\x00\x00" + tiff
    seg = struct.pack(">HH", 0xFFE1, len(app1) + 2) + app1
    with open(path, "wb") as f:
        f.write(b"\xff\xd8" + seg + b"\xff\xda" + b"\x00\x01rest")


class ExifTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_exif_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_datetime_original_little_endian(self):
        path = os.path.join(self.tmp, "le.jpg")
        build_jpeg(path, "2023:07:14 09:05:33")
        self.assertEqual(exif.exif_datetime_original(path),
                         "2023:07:14 09:05:33")
        self.assertEqual(exif.exif_stamp(path), "2023-07-14_090533")

    def test_datetime_original_big_endian(self):
        path = os.path.join(self.tmp, "be.jpg")
        build_jpeg(path, "2021:01:02 03:04:05", byte_order="MM")
        self.assertEqual(exif.exif_datetime_original(path),
                         "2021:01:02 03:04:05")

    def test_fallback_to_ifd0_datetime(self):
        path = os.path.join(self.tmp, "ifd0.jpg")
        build_jpeg(path, None, datetime_ifd0="2020:12:31 23:59:01")
        self.assertEqual(exif.exif_datetime_original(path),
                         "2020:12:31 23:59:01")

    def test_no_exif_falls_back_to_mtime(self):
        path = os.path.join(self.tmp, "plain.jpg")
        with open(path, "wb") as f:
            f.write(b"\xff\xd8\xff\xd9")
        stamp = time.strftime("%Y-%m-%d_%H%M%S",
                              time.localtime(os.path.getmtime(path)))
        self.assertEqual(exif.exif_stamp(path), stamp)

    def test_not_jpeg_mtime_fallback(self):
        path = os.path.join(self.tmp, "notes.txt")
        with open(path, "w") as f:
            f.write("x")
        self.assertIsNotNone(exif.exif_stamp(path))  # mtime — ок для не-jpeg

    def test_broken_exif_raises_valueerror(self):
        path = os.path.join(self.tmp, "broken.jpg")
        with open(path, "wb") as f:
            f.write(b"\xff\xd8\xff\xe1\x00\x0aExif\x00\x00XX\xff\xd9")
        with self.assertRaises(ValueError):
            exif.exif_datetime_original(path)

    def test_is_jpeg(self):
        self.assertTrue(exif.is_jpeg("/x/IMG.JPG"))
        self.assertFalse(exif.is_jpeg("/x/img.png"))


if __name__ == "__main__":
    unittest.main()
