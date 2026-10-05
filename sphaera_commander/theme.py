"""Фирменные темы Sphaera Commander — палитра Iustitia.

Тёмная: графит (#090b0c…#1d2324), пергамент (#f4efe6), бирюза (#2f8f8b/#5fbab4),
бронза (#a6784f). Светлая: пергамент с графитовым текстом и теми же акцентами.
Шрифты интерфейса системные, дисплейный — с засечками (PT Serif) в диалогах
«О программе».
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

# светлая пергаментная — те же акценты, светлые поверхности
PERGAMENT_WINDOW = "#f4efe6"
PERGAMENT_BASE = "#faf6ee"
PERGAMENT_ALT = "#efe8d9"
PERGAMENT_BTN = "#ece4d2"
PERGAMENT_BORDER = "#d8cdb6"
PERGAMENT_TEXT = "#1d2324"
PERGAMENT_MUTED = "#5a5245"
PERGAMENT_FAINT = "#958c7c"

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

LIGHT_STYLESHEET = f"""
QMainWindow, QDialog {{ background: {PERGAMENT_WINDOW}; }}
QMenuBar {{ background: {PERGAMENT_BTN}; color: {PERGAMENT_TEXT}; }}
QMenuBar::item:selected {{ background: {PERGAMENT_ALT}; }}
QMenu {{ background: {PERGAMENT_BASE}; color: {PERGAMENT_TEXT};
         border: 1px solid {PERGAMENT_BORDER}; }}
QMenu::item:selected {{ background: {TEAL}; color: {PARCHMENT}; }}
QMenu::separator {{ height: 1px; background: {PERGAMENT_BORDER};
                    margin: 4px 8px; }}
QSplitter::handle {{ background: {PERGAMENT_BORDER}; }}
QSplitter::handle:hover {{ background: {TEAL}; }}
QLineEdit, QComboBox, QSpinBox {{
    background: {PERGAMENT_BASE}; color: {PERGAMENT_TEXT};
    border: 1px solid {PERGAMENT_BORDER}; border-radius: 2px;
    padding: 2px 4px; selection-background-color: {TEAL};
}}
QComboBox::drop-down {{ border: none; }}
QComboBox QAbstractItemView {{
    background: {PERGAMENT_BASE}; color: {PERGAMENT_TEXT};
    selection-background-color: {TEAL};
}}
QPushButton {{
    background: {PERGAMENT_BTN}; color: {PERGAMENT_TEXT};
    border: 1px solid {PERGAMENT_BORDER}; border-radius: 2px;
    padding: 3px 10px;
}}
QPushButton:hover {{ border-color: {TEAL}; color: {TEAL}; }}
QPushButton:pressed, QPushButton:checked {{ background: {PERGAMENT_ALT};
                                            color: {TEAL}; }}
QPushButton:default {{ border-color: {BRONZE}; }}
QPlainTextEdit, QTextBrowser, QTextEdit {{
    background: {PERGAMENT_BASE}; color: {PERGAMENT_TEXT};
    border: 1px solid {PERGAMENT_BORDER};
    selection-background-color: {TEAL};
}}
QTableView {{ background: {PERGAMENT_WINDOW}; alternate-background-color: {PERGAMENT_BASE};
              color: {PERGAMENT_TEXT}; gridline-color: {PERGAMENT_BORDER};
              selection-background-color: {TEAL}; selection-color: {PARCHMENT}; }}
QHeaderView::section {{
    background: {PERGAMENT_ALT}; color: {PERGAMENT_MUTED};
    border: none; border-right: 1px solid {PERGAMENT_BORDER};
    border-bottom: 1px solid {PERGAMENT_BORDER}; padding: 3px 6px;
}}
QTreeWidget {{ background: {PERGAMENT_BASE}; color: {PERGAMENT_TEXT};
               alternate-background-color: {PERGAMENT_ALT};
               selection-background-color: {TEAL}; }}
QLabel {{ color: {PERGAMENT_TEXT}; }}
QStatusBar {{ background: {PERGAMENT_BTN}; color: {PERGAMENT_MUTED}; }}
QScrollBar:vertical {{ background: {PERGAMENT_ALT}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {PERGAMENT_BORDER};
                               border-radius: 2px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {TEAL}; }}
QScrollBar:horizontal {{ background: {PERGAMENT_ALT}; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {PERGAMENT_BORDER};
                                 border-radius: 2px; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {TEAL}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QToolTip {{ background: {PERGAMENT_BTN}; color: {PERGAMENT_TEXT};
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


def apply_light_theme(app: QApplication) -> None:
    """Светлая пергаментная тема (те же акценты Iustitia)."""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(PERGAMENT_WINDOW))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(PERGAMENT_TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(PERGAMENT_BASE))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(PERGAMENT_ALT))
    palette.setColor(QPalette.ColorRole.Text, QColor(PERGAMENT_TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(PERGAMENT_BTN))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(PERGAMENT_TEXT))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(PERGAMENT_BTN))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(PERGAMENT_TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(TEAL))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(PARCHMENT))
    palette.setColor(QPalette.ColorRole.Link, QColor(TEAL))
    palette.setColor(QPalette.ColorRole.PlaceholderText,
                     QColor(PERGAMENT_FAINT))
    palette.setColor(QPalette.ColorGroup.Disabled,
                     QPalette.ColorRole.Text, QColor(PERGAMENT_FAINT))
    palette.setColor(QPalette.ColorGroup.Disabled,
                     QPalette.ColorRole.ButtonText, QColor(PERGAMENT_FAINT))
    app.setPalette(palette)
    app.setStyleSheet(LIGHT_STYLESHEET)


def apply_theme(app: QApplication, mode: str) -> None:
    """Тема по имени: dark (Iustitia) | light (пергамент) | system."""
    if mode == "light":
        apply_light_theme(app)
    elif mode == "dark":
        apply_brand_theme(app)
    else:
        revert_theme(app)


def current_mode() -> str:
    """Выбранная тема из config; легаси view/brand_theme уважается."""
    from sphaera_commander import config

    mode = config.qsettings().value("view/theme", "")
    if mode in ("dark", "light", "system"):
        return mode
    legacy = config.qsettings().value("view/brand_theme", "true")
    return "dark" if legacy in (True, "true", "1") else "system"


def revert_theme(app: QApplication) -> None:
    """Вернуть системную тему."""
    app.setPalette(app.style().standardPalette())
    app.setStyleSheet("")
