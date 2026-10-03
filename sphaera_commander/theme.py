"""Фирменная тема Sphaera Commander — палитра Iustitia.

Графит (#090b0c…#1d2324), пергамент (#f4efe6), бирюза (#2f8f8b/#5fbab4),
бронза (#a6784f). Шрифты интерфейса системные, дисплейный — с засечками
(PT Serif) в диалогах «О программе».
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# палитра Iustitia (assets/css/design-system.css)
GRAPHITE_950 = "#090b0c"
GRAPHITE_925 = "#0d1011"
GRAPHITE_900 = "#111516"
GRAPHITE_850 = "#171c1d"
GRAPHITE_800 = "#1d2324"
GRAPHITE_700 = "#2a3133"
PARCHMENT = "#f4efe6"
PARCHMENT_MUTED = "#c6baa6"
PARCHMENT_FAINT = "#958c7c"
TEAL = "#2f8f8b"
TEAL_STRONG = "#5fbab4"
BRONZE = "#a6784f"

STYLESHEET = f"""
QMainWindow, QDialog {{ background: {GRAPHITE_925}; }}
QMenuBar {{ background: {GRAPHITE_950}; color: {PARCHMENT}; }}
QMenuBar::item:selected {{ background: {GRAPHITE_800}; }}
QMenu {{ background: {GRAPHITE_900}; color: {PARCHMENT};
         border: 1px solid {GRAPHITE_700}; }}
QMenu::item:selected {{ background: {TEAL}; color: {PARCHMENT}; }}
QMenu::separator {{ height: 1px; background: {GRAPHITE_700};
                    margin: 4px 8px; }}
QSplitter::handle {{ background: {GRAPHITE_800}; }}
QSplitter::handle:hover {{ background: {TEAL}; }}
QLineEdit, QComboBox, QSpinBox {{
    background: {GRAPHITE_900}; color: {PARCHMENT};
    border: 1px solid {GRAPHITE_700}; border-radius: 2px;
    padding: 2px 4px; selection-background-color: {TEAL};
}}
QComboBox::drop-down {{ border: none; }}
QComboBox QAbstractItemView {{
    background: {GRAPHITE_900}; color: {PARCHMENT};
    selection-background-color: {TEAL};
}}
QPushButton {{
    background: {GRAPHITE_800}; color: {PARCHMENT};
    border: 1px solid {GRAPHITE_700}; border-radius: 2px;
    padding: 3px 10px;
}}
QPushButton:hover {{ border-color: {TEAL}; color: {TEAL_STRONG}; }}
QPushButton:pressed, QPushButton:checked {{ background: {GRAPHITE_700};
                                             color: {TEAL_STRONG}; }}
QPushButton:default {{ border-color: {BRONZE}; }}
QPlainTextEdit, QTextBrowser, QTextEdit {{
    background: {GRAPHITE_900}; color: {PARCHMENT};
    border: 1px solid {GRAPHITE_700};
    selection-background-color: {TEAL};
}}
QTableView {{ background: {GRAPHITE_925}; alternate-background-color: {GRAPHITE_900};
              color: {PARCHMENT}; gridline-color: {GRAPHITE_800};
              selection-background-color: {TEAL}; selection-color: {PARCHMENT}; }}
QHeaderView::section {{
    background: {GRAPHITE_850}; color: {PARCHMENT_MUTED};
    border: none; border-right: 1px solid {GRAPHITE_700};
    border-bottom: 1px solid {GRAPHITE_700}; padding: 3px 6px;
}}
QTreeWidget {{ background: {GRAPHITE_900}; color: {PARCHMENT};
               alternate-background-color: {GRAPHITE_850};
               selection-background-color: {TEAL}; }}
QLabel {{ color: {PARCHMENT}; }}
QStatusBar {{ background: {GRAPHITE_950}; color: {PARCHMENT_MUTED}; }}
QScrollBar:vertical {{ background: {GRAPHITE_950}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {GRAPHITE_700};
                               border-radius: 2px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {TEAL}; }}
QScrollBar:horizontal {{ background: {GRAPHITE_950}; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {GRAPHITE_700};
                                 border-radius: 2px; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {TEAL}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QToolTip {{ background: {GRAPHITE_800}; color: {PARCHMENT};
            border: 1px solid {TEAL}; }}
"""


def apply_brand_theme(app: QApplication) -> None:
    """Применить палитру Iustitia и фирменные стили."""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(GRAPHITE_925))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(PARCHMENT))
    palette.setColor(QPalette.ColorRole.Base, QColor(GRAPHITE_900))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(GRAPHITE_850))
    palette.setColor(QPalette.ColorRole.Text, QColor(PARCHMENT))
    palette.setColor(QPalette.ColorRole.Button, QColor(GRAPHITE_800))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(PARCHMENT))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(GRAPHITE_800))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(PARCHMENT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(TEAL))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(PARCHMENT))
    palette.setColor(QPalette.ColorRole.Link, QColor(TEAL_STRONG))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(PARCHMENT_FAINT))
    palette.setColor(QPalette.ColorGroup.Disabled,
                     QPalette.ColorRole.Text, QColor(PARCHMENT_FAINT))
    palette.setColor(QPalette.ColorGroup.Disabled,
                     QPalette.ColorRole.ButtonText, QColor(PARCHMENT_FAINT))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)


def revert_theme(app: QApplication) -> None:
    """Вернуть системную тему."""
    app.setPalette(app.style().standardPalette())
    app.setStyleSheet("")
