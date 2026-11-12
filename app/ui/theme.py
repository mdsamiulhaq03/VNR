"""Modern dark theme for the whole app."""
from __future__ import annotations

import hashlib

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

BG = "#111217"
SURFACE = "#1a1c23"
SURFACE_2 = "#22252e"
BORDER = "#2c303b"
TEXT = "#e8eaf0"
MUTED = "#8b90a0"
ACCENT = "#7c9cff"
ACCENT_HOVER = "#93aeff"
DANGER = "#ff6b7a"
FEMALE = "#ff8fc7"
MALE = "#6fb6ff"
UNKNOWN = "#b39cff"

AVATAR_COLORS = ["#ff8fa3", "#ffb86b", "#ffd86b", "#8be28b", "#6be2d0", "#6fb6ff",
                 "#9c9cff", "#d08bff", "#ff8fd8", "#b0bec5"]


def avatar_color(name: str) -> str:
    h = int(hashlib.md5(name.encode("utf-8")).hexdigest()[:6], 16)
    return AVATAR_COLORS[h % len(AVATAR_COLORS)]


def gender_color(g: str) -> str:
    return {"female": FEMALE, "male": MALE}.get(g, UNKNOWN)


def gender_icon(g: str) -> str:
    return {"female": "♀", "male": "♂"}.get(g, "?")


QSS = f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif; font-size: 10pt;
     color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#page, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER};
            padding: 4px 6px; border-radius: 6px; }}

QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#hero {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2a2f52, stop:1 #3a2547);
               border: 1px solid #3b3f5c; border-radius: 14px; }}
QLabel#heroTitle {{ font-size: 18pt; font-weight: 800; color: #ffffff; }}
QFrame#row {{ background: transparent; border-radius: 8px; }}
QFrame#row:hover {{ background: {SURFACE_2}; }}
QWidget#cardInner {{ background: transparent; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: #2f3a5c; }}
QMenu::item:disabled {{ color: {MUTED}; }}
QLabel#cardTitle {{ color: {MUTED}; font-size: 8.5pt; font-weight: 600;
                    letter-spacing: 1px; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#title {{ font-size: 15pt; font-weight: 700; }}
QLabel#speaker {{ font-size: 13pt; font-weight: 700; }}
QLabel#dialogue {{ font-size: 14pt; line-height: 140%; }}
QLabel#pill {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 10px;
               padding: 2px 10px; color: {MUTED}; font-size: 9pt; }}
QLabel#pillOk {{ background: #17301f; border: 1px solid #2d5a3a; border-radius: 10px;
                 padding: 2px 10px; color: #8be28b; font-size: 9pt; }}
QLabel#pillWarn {{ background: #33261a; border: 1px solid #5a432d; border-radius: 10px;
                   padding: 2px 10px; color: #ffb86b; font-size: 9pt; }}

QPushButton {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px;
               padding: 7px 14px; }}
QPushButton:hover {{ background: #2a2e39; border-color: #3a3f4d; }}
QPushButton:pressed {{ background: #1e2129; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton#primary {{ background: {ACCENT}; color: #0d1020; border: none;
                       font-size: 11.5pt; font-weight: 700; padding: 11px; border-radius: 10px; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#danger {{ background: {DANGER}; color: #200d10; border: none;
                      font-size: 11.5pt; font-weight: 700; padding: 11px; border-radius: 10px; }}
QPushButton#icon {{ padding: 6px; min-width: 34px; font-size: 12pt; }}
QPushButton#seg {{ padding: 4px 10px; border-radius: 0; min-width: 30px; font-size: 11pt; }}
QPushButton#seg:checked {{ background: {ACCENT}; color: #0d1020; border-color: {ACCENT}; }}
QPushButton#segL {{ padding: 4px 10px; border-top-left-radius: 8px;
                    border-bottom-left-radius: 8px; border-top-right-radius: 0;
                    border-bottom-right-radius: 0; min-width: 30px; font-size: 11pt; }}
QPushButton#segR {{ padding: 4px 10px; border-top-right-radius: 8px;
                    border-bottom-right-radius: 8px; border-top-left-radius: 0;
                    border-bottom-left-radius: 0; min-width: 30px; font-size: 11pt; }}
QPushButton#segL:checked, QPushButton#segR:checked {{ background: {ACCENT}; color: #0d1020;
                                                      border-color: {ACCENT}; }}

QLineEdit, QComboBox, QKeySequenceEdit, QTextEdit, QSpinBox {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 5px 8px; selection-background-color: {ACCENT}; selection-color: #0d1020; }}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER};
    selection-background-color: {ACCENT}; selection-color: #0d1020; outline: none; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px;
                        border: 1px solid #454b5a; background: {SURFACE_2}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px;
                           border: 1px solid #454b5a; background: {SURFACE_2}; }}
QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; height: 14px;
                              margin: -6px 0; border-radius: 7px; }}

QTabWidget::pane {{ border: none; top: 4px; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 7px 13px; margin: 4px 1px;
                border-radius: 14px; font-weight: 600; }}
QTabBar::tab:selected {{ background: {SURFACE_2}; color: {TEXT}; }}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}

QListWidget, QTableWidget {{ background: {SURFACE}; border: 1px solid {BORDER};
    border-radius: 10px; alternate-background-color: #1e2028; gridline-color: {BORDER}; }}
QListWidget::item {{ padding: 6px 8px; border-radius: 6px; }}
QListWidget::item:selected, QTableWidget::item:selected {{ background: #2f3a5c; color: {TEXT}; }}
QHeaderView::section {{ background: {SURFACE_2}; color: {MUTED}; border: none;
                        padding: 6px; font-weight: 600; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #3a3f4d; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #3a3f4d; border-radius: 4px; min-width: 30px; }}

QStatusBar {{ background: {BG}; color: {MUTED}; border-top: 1px solid {BORDER}; }}
QStatusBar::item {{ border: none; }}
QGroupBox {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px;
             margin-top: 14px; padding: 14px 10px 10px 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {MUTED}; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle(QStyleFactory.create("Fusion"))
    pal = QPalette()
    for role, col in [(QPalette.Window, BG), (QPalette.Base, SURFACE_2),
                      (QPalette.AlternateBase, SURFACE), (QPalette.Text, TEXT),
                      (QPalette.WindowText, TEXT), (QPalette.Button, SURFACE_2),
                      (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT),
                      (QPalette.HighlightedText, "#0d1020"), (QPalette.PlaceholderText, MUTED),
                      (QPalette.ToolTipBase, SURFACE_2), (QPalette.ToolTipText, TEXT)]:
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(QSS)
