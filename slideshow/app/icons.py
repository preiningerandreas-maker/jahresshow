"""Einfarbige Linien-Symbole (Fluent UI System Icons, MIT, siehe assets/icons/LICENSE.txt).
Die SVG-Dateien bleiben unverändert; eingefärbt wird beim Laden in einer Theme-Farbe."""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from slideshow.render.cache import atomic_write_text

ICON_DIR = Path(__file__).resolve().parents[1] / "assets" / "icons"
SIZES = (16, 20, 24, 32, 40, 48)  # Pixel; Qt wählt je nach Skalierung die passende


def svg_file(name: str, color: str) -> Path:
    """Eingefärbte SVG-Datei für Stylesheets (QSS braucht einen Pfad, z. B. für Häkchen)."""
    folder = Path(tempfile.gettempdir()) / "jahresshow-icons"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{name}-{color.lstrip('#').lower()}.svg"
    if not out.is_file():
        atomic_write_text(out, svg_data(name, color).decode("utf-8"))
    return out


def svg_data(name: str, color: str) -> bytes:
    """SVG mit Füllfarbe (die Dateien selbst haben keine Farbangabe)."""
    raw = (ICON_DIR / f"{name}.svg").read_text("utf-8")
    return raw.replace("<svg ", f'<svg fill="{color}" ', 1).encode("utf-8")


def _pixmap(data: bytes, size: int) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(data))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    renderer.render(p)
    p.end()
    return pm


@lru_cache(maxsize=128)
def icon(name: str, color: str = "text") -> QIcon:
    """Symbol in einer Theme-Farbe (Token-Name); inaktiv im gedämpften Ton."""
    from slideshow.app.theme import TOKENS  # hier, weil theme.py svg_file verwendet

    normal = svg_data(name, TOKENS[color])
    disabled = svg_data(name, TOKENS["muted"])
    ic = QIcon()
    for size in SIZES:
        ic.addPixmap(_pixmap(normal, size), QIcon.Mode.Normal)
        ic.addPixmap(_pixmap(disabled, size), QIcon.Mode.Disabled)
    return ic


def available() -> list[str]:
    return sorted(p.stem for p in ICON_DIR.glob("*.svg"))
