"""Рендер слайдов PPTX в QPixmap собственными силами (без LibreOffice).

Геометрию готовит previewers.pptx_slides_rich; здесь — отрисовка:
фон, прямоугольники/овалы, картинки, таблицы (упрощённо) и текст через
QTextDocument (перенос, кегли, цвета, выравнивание).
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap, QTextDocument

EMU_PER_INCH = 914400
BASE_DPI = 96


def _rgb(value: int | None, fallback: int) -> QColor:
    return QColor(value if value is not None else fallback)


def _int_anchor(value: str) -> int:
    return {"middle": 1, "bottom": 2}.get(value, 0)


def render_slide(deck: dict, slide: dict, scale: float = 1.0) -> QPixmap:
    width_px = max(1, round(deck["width"] / EMU_PER_INCH * BASE_DPI * scale))
    height_px = max(1, round(deck["height"] / EMU_PER_INCH * BASE_DPI * scale))
    pixmap = QPixmap(width_px, height_px)
    pixmap.fill(_rgb(slide.get("bg"), 0xFFFFFF))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    emu = lambda v: v / EMU_PER_INCH * BASE_DPI * scale  # noqa: E731
    for shape in slide["shapes"]:
        rect = QRectF(emu(shape["x"]), emu(shape["y"]),
                      emu(shape["w"]), emu(shape["h"]))
        kind = shape["kind"]
        if kind == "picture":
            if shape["img"]:
                img = QPixmap(shape["img"])
                if not img.isNull():
                    painter.drawPixmap(rect, img, QRectF(img.rect()))
            else:
                painter.fillRect(rect, QColor(220, 220, 220))
            continue
        if kind == "table":
            _draw_table(painter, rect, shape["table"])
            continue
        if kind == "ellipse":
            painter.setPen(QColor(shape["line"] if shape["line"] else 0x404040))
            painter.setBrush(_rgb(shape["fill"], 0xFFFFFF))
            painter.drawEllipse(rect)
        else:  # rect / textbox
            if shape["fill"] is not None:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(_rgb(shape["fill"], 0xFFFFFF))
                painter.drawRect(rect)
            elif kind == "rect":
                painter.setPen(QColor(0xD0D0D0))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(rect)
        if shape["html"]:
            _draw_text(painter, rect, shape["html"], shape["anchor"], scale)
    painter.end()
    return pixmap


def _draw_text(painter: QPainter, rect: QRectF, html: str, anchor: str,
               scale: float) -> None:
    document = QTextDocument()
    document.setDefaultFont(QFont("DejaVu Sans", max(6, round(9 * scale))))
    document.setHtml(html)
    document.setTextWidth(max(10.0, rect.width()))
    height = document.size().height()
    y = rect.top()
    anchor_i = _int_anchor(anchor)
    if anchor_i == 1:
        y += max(0.0, (rect.height() - height) / 2)
    elif anchor_i == 2:
        y += max(0.0, rect.height() - height)
    painter.save()
    painter.translate(QPointF(rect.left(), y))
    document.drawContents(painter, QRectF(0, 0, rect.width(), height))
    painter.restore()


def _draw_table(painter: QPainter, rect: QRectF, table: list[list[str]]) -> None:
    rows = len(table)
    cols = max((len(r) for r in table), default=0)
    if not rows or not cols:
        return
    painter.setPen(QColor(0x606060))
    painter.setBrush(QColor(0xFFFFFF))
    painter.drawRect(rect)
    row_h = rect.height() / rows
    col_w = rect.width() / cols
    font = QFont("DejaVu Sans", 8)
    painter.setFont(font)
    for r, row in enumerate(table):
        for c in range(cols):
            text = row[c] if c < len(row) else ""
            cell = QRectF(rect.left() + c * col_w, rect.top() + r * row_h,
                          col_w, row_h)
            painter.drawRect(cell)
            painter.drawText(cell.adjusted(3, 1, -3, -1),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             text)
