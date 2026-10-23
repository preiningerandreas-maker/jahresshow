"""Dunkles Erscheinungsbild: alle Farben als Tokens an dieser einen Stelle, daraus entstehen
Palette und Stylesheet (QSS). Ruhig gehalten: dünne Linien statt Rahmen, keine Schatten,
keine Verläufe, keine Animationen. Blau nur für Auswahl, aktive Reiter und den Hauptknopf.
Gelb nur für das Fortschrittsfenster von Export und Vorschau, darauf dunkle Schrift.

Lesbarkeit (WCAG AA, Text mindestens 4.5:1): Gedämpfter Text erreicht auf Hintergrund,
Bereichen und Eingabefeldern mindestens 4.5:1, auf Blau steht weiße Schrift (5.1:1), siehe
TEXT_ON und tests/test_theme.py."""

from __future__ import annotations

import ctypes
import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from slideshow.app.icons import svg_file

TOKENS = {
    "bg": "#1E1E1E",  # Hintergrund (Fenster, Raster, Monitor, Statusleiste)
    "area": "#252525",  # Bereiche (Werkzeugleiste, Eigenschaften, Aufgabenzeile)
    "input": "#2E2E2E",  # Eingabefelder und Knöpfe
    "line": "#3A3A3A",  # Trennlinien
    "text": "#D4D4D4",  # Text
    "muted": "#969696",  # gedämpfter Text (Hinweise, Statusleiste, Monitor-Titel)
    "accent": "#1F6FC5",  # Auswahl, aktiver Reiter, Hauptknopf
    "on_accent": "#FFFFFF",  # Schrift auf Blau
    "popup": "#F2C230",  # Fortschrittsfenster von Export und Vorschau
    "on_popup": "#1A1A1A",  # Schrift, Rand und Balkenfüllung auf Gelb
    "popup_track": "#FBE7A1",  # leerer Teil des Balkens im Fortschrittsfenster
}

# Textfarbe → Hintergründe, auf denen sie vorkommt (für den Kontrast-Test)
TEXT_ON = {
    "text": ("bg", "area", "input", "line"),
    "muted": ("bg", "area", "input"),
    "on_accent": ("accent",),  # Hauptknopf, markierte Menü- und Listeneinträge
    "on_popup": ("popup",),  # Fortschrittsfenster (Weiß auf Gelb wäre unlesbar)
}

RADIUS = 4  # Kacheln und Knöpfe leicht abgerundet


def color(name: str, alpha: int = 255) -> QColor:
    c = QColor(TOKENS[name])
    c.setAlpha(alpha)
    return c


def relative_luminance(hex_color: str) -> float:
    """Relative Leuchtdichte nach WCAG 2.x."""
    h = hex_color.lstrip("#")
    channels = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: str, bg: str) -> float:
    """Kontrastverhältnis nach WCAG (1 bis 21)."""
    hi, lo = sorted((relative_luminance(fg), relative_luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def palette() -> QPalette:
    t = {k: QColor(v) for k, v in TOKENS.items()}
    p = QPalette()
    R = QPalette.ColorRole
    for role, name in ((R.Window, "bg"), (R.WindowText, "text"), (R.Base, "input"),
                       (R.AlternateBase, "area"), (R.Text, "text"), (R.Button, "input"),
                       (R.ButtonText, "text"), (R.BrightText, "text"),
                       (R.Highlight, "accent"), (R.HighlightedText, "on_accent"),
                       (R.PlaceholderText, "muted"), (R.ToolTipBase, "area"),
                       (R.ToolTipText, "text"), (R.Light, "line"), (R.Midlight, "line"),
                       (R.Mid, "line"), (R.Dark, "bg"), (R.Shadow, "bg"),
                       (R.Link, "text"), (R.LinkVisited, "text")):
        p.setColor(role, t[name])
    disabled = QPalette.ColorGroup.Disabled
    for role in (R.WindowText, R.Text, R.ButtonText):
        p.setColor(disabled, role, t["muted"])  # inaktive Elemente sind von WCAG ausgenommen
    return p


QSS_TEMPLATE = """
QWidget {{ color: {text}; }}
QMainWindow, QDialog, QMessageBox {{ background: {bg}; }}
QLabel {{ background: transparent; }}
QToolTip {{ background: {area}; color: {text}; border: 1px solid {line}; padding: 3px; }}

QMenuBar {{ background: {area}; color: {text}; border-bottom: 1px solid {line}; }}
QMenuBar::item {{ background: transparent; padding: 4px 10px; }}
QMenuBar::item:selected {{ background: {input}; color: {text}; }}
QMenu {{ background: {area}; color: {text}; border: 1px solid {line}; padding: 3px; }}
QMenu::item {{ padding: 4px 22px 4px 10px; }}
QMenu::item:selected {{ background: {accent}; color: {on_accent}; }}
QMenu::separator {{ height: 1px; background: {line}; margin: 3px 6px; }}

QToolBar {{ background: {area}; border: none; border-bottom: 1px solid {line};
           padding: 3px 6px; spacing: 2px; }}
QToolBar::separator {{ background: {line}; width: 1px; margin: 4px 6px; }}
QToolBar QToolButton {{ background: {area}; color: {text}; border: 1px solid transparent;
                       border-radius: {radius}px; padding: 3px 7px; }}
QToolBar QToolButton:hover {{ border-color: {line}; }}
QToolBar QToolButton:pressed {{ background: {input}; }}
QToolButton#primaryButton {{ background: {accent}; color: {on_accent}; font-weight: 600;
                            border: none; border-radius: {radius}px; padding: 4px 12px; }}
QToolButton#primaryButton:hover {{ border: none; }}
QToolButton#primaryButton:disabled {{ background: {line}; color: {muted}; }}

QSplitter::handle {{ background: {line}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QWidget#rasterPane, QWidget#monitorPane {{ background: {bg}; }}
QWidget#propertiesPane {{ background: {area}; }}
QLabel#monitorTitle {{ color: {muted}; background: {bg}; padding: 4px 8px; }}
QLabel#emptyHint {{ color: {muted}; background: {bg}; }}
QLabel#propertiesHint {{ color: {muted}; background: {area}; }}

QTabWidget::pane {{ border: none; border-top: 1px solid {line}; background: {area}; }}
QTabBar {{ background: {area}; }}
QTabBar::tab {{ background: {area}; color: {text}; padding: 6px 12px; border: none;
               border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ border-bottom: 2px solid {accent}; }}
QTabBar::tab:!selected:hover {{ background: {input}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: {area}; border: none; }}

QGroupBox {{ background: {area}; border: none; border-top: 1px solid {line};
            margin-top: 16px; padding-top: 4px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 0px; padding: 0 6px 0 0;
                   color: {text}; background: {area}; }}
QCheckBox {{ background: transparent; spacing: 6px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {muted};
                       border-radius: 3px; background: {input}; }}
QCheckBox::indicator:hover {{ border-color: {text}; }}
QCheckBox::indicator:checked {{ image: url("{check}"); }}
QCheckBox::indicator:disabled {{ border-color: {line}; }}

QLineEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox {{
    background: {input}; color: {text}; border: 1px solid {line}; border-radius: 3px;
    padding: 2px 4px; selection-background-color: {accent}; selection-color: {on_accent}; }}
QLineEdit:focus, QPlainTextEdit:focus, QAbstractSpinBox:focus, QComboBox:focus {{
    border-color: {text}; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
    subcontrol-origin: border; width: 16px; border: none; background: transparent; }}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; }}
QAbstractSpinBox::up-arrow {{ image: url("{up}"); width: 10px; height: 10px; }}
QAbstractSpinBox::down-arrow, QComboBox::down-arrow {{
    image: url("{down}"); width: 10px; height: 10px; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {area}; color: {text}; border: 1px solid {line};
                              selection-background-color: {accent}; selection-color: {on_accent}; }}

QPushButton {{ background: {input}; color: {text}; border: 1px solid {line};
              border-radius: 3px; padding: 4px 10px; }}
QPushButton:hover {{ border-color: {text}; }}
QPushButton:pressed, QPushButton:checked {{ background: {line}; }}
QPushButton:disabled {{ color: {muted}; }}

QListView#timeline {{ background: {bg}; border: none; color: {text}; }}
QListWidget {{ background: {input}; color: {text}; border: 1px solid {line}; }}
QListWidget::item:selected {{ background: {accent}; color: {on_accent}; }}

QScrollBar:vertical {{ background: {bg}; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: {bg}; height: 10px; margin: 0; }}
QScrollBar::handle {{ background: {line}; border-radius: 4px; min-height: 24px;
                     min-width: 24px; margin: 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QProgressBar {{ background: {input}; border: 1px solid {line}; border-radius: 3px;
               min-height: 8px; max-height: 8px; }}
QProgressBar::chunk {{ background: {muted}; border-radius: 2px; }}

QDialog#renderDialog {{ background: {popup}; border: 1px solid {on_popup}; }}
QDialog#renderDialog QLabel {{ color: {on_popup}; background: transparent; }}
QLabel#renderTitle {{ font-size: 12pt; font-weight: 600; }}
QProgressBar#renderBar {{ background: {popup_track}; border: 1px solid {on_popup};
                         border-radius: 3px; min-height: 14px; max-height: 14px; }}
QProgressBar#renderBar::chunk {{ background: {on_popup}; border-radius: 2px; }}
QSlider::groove:horizontal {{ background: {line}; height: 2px; }}
QSlider::sub-page:horizontal {{ background: {muted}; height: 2px; }}
QSlider::handle:horizontal {{ background: {text}; width: 10px; height: 10px;
                             margin: -4px 0; border-radius: 5px; }}

QFrame#noticeBar {{ background: {area}; border: 1px solid {line}; border-radius: {radius}px; }}
QFrame#activityPanel {{ background: {area}; border: none; border-top: 1px solid {line}; }}
QStatusBar {{ background: {bg}; border-top: 1px solid {line}; }}
QStatusBar QLabel {{ color: {muted}; background: {bg}; }}
QStatusBar::item {{ border: none; }}
"""


def stylesheet() -> str:
    files = {key: svg_file(name, TOKENS["text"]).as_posix()
             for key, name in (("check", "checkmark"), ("up", "chevron_up"),
                               ("down", "chevron_down"))}
    return QSS_TEMPLATE.format(radius=RADIUS, **files, **TOKENS)


def apply_theme(app: QApplication) -> None:
    """Einheitliches Aussehen unter Windows 10 und 11 (Fusion als Grundlage)."""
    app.setStyle("Fusion")
    app.setPalette(palette())
    app.setStyleSheet(stylesheet())


def dark_title_bar(widget: QWidget) -> None:
    """Dunkle Titelleiste unter Windows 10 (ab 20H1) und 11; sonst bleibt sie hell."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(widget.winId())
        on = ctypes.c_int(1)
        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))
    except (AttributeError, OSError):
        pass
