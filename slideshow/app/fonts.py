"""Schriftauswahl: installierte Windows-Schriften mit Suche und Vorschau. Die Vorschau zeichnet
dieselbe Funktion wie der Export (Pillow), sie sieht also aus wie im fertigen Video.
Gespeichert wird der Pfad der Schriftdatei, weil der Export die Datei braucht."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from slideshow.app.theme import color
from slideshow.render.text import load_font

FONT_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
FONT_EXTS = {".ttf", ".otf", ".ttc"}
# Symbolschriften: zeigen Zeichen statt Buchstaben, für Texte in der Show unbrauchbar
SYMBOL_FONTS = ("Marlett", "Webdings", "Wingdings", "Symbol", "MDL2 Assets", "Fluent Icons",
                "Emoji", "HoloLens", "MT Extra")
SAMPLE_TEXT = "Urlaub Wörthersee 2026"
PREVIEW_SIZE = (440, 90)
PREVIEW_PT = 40


def font_name(registry_name: str) -> str:
    """„Cambria & Cambria Math (TrueType)“ -> „Cambria“ (bei Sammlungen die erste Schrift,
    die Pillow ohne Index lädt)."""
    name = re.sub(r"\s*\((TrueType|OpenType)\)\s*$", "", registry_name)
    return name.split(" & ")[0].strip()


def installed_fonts() -> dict[str, Path]:
    """Installierte Schriften (für alle Benutzer und nur für diesen), Name -> Datei,
    alphabetisch. Jede Schnittvariante (z. B. „Arial Bold“) ist ein eigener Eintrag."""
    fonts: dict[str, Path] = {}
    if sys.platform != "win32":
        return fonts
    import winreg

    windir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(root, FONT_KEY)
        except OSError:
            continue
        with key:
            i = 0
            while True:
                try:
                    raw, value, _kind = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                if not isinstance(value, str):
                    continue
                path = Path(value)
                if not path.is_absolute():
                    path = windir / path
                name = font_name(raw)
                if (not name or path.suffix.lower() not in FONT_EXTS
                        or any(s in name for s in SYMBOL_FONTS) or not path.is_file()):
                    continue
                fonts.setdefault(name, path)
    return dict(sorted(fonts.items(), key=lambda kv: kv[0].lower()))


def font_label(value: str, fonts: dict[str, Path] | None = None) -> str:
    """Anzeigename einer gespeicherten Schrift: Name der installierten Schrift, sonst der
    Dateiname ohne Endung."""
    path = Path(value)
    fonts = installed_fonts() if fonts is None else fonts
    for name, file in fonts.items():
        if os.path.normcase(str(file)) == os.path.normcase(str(path)):
            return name
    return path.stem


def preview_image(font: str, text: str, size: tuple[int, int] = PREVIEW_SIZE) -> QImage:
    """Mustertext in der Schrift, gezeichnet wie im Export; lange Texte kleiner, damit sie
    ganz zu sehen sind."""
    w, h = size
    img = Image.new("RGB", (w, h), color("input").name())
    pt = PREVIEW_PT
    try:
        pil_font = load_font(font, pt)
        while pt > 12 and pil_font.getlength(text) > w - 24:
            pt -= 2
            pil_font = load_font(font, pt)
    except OSError:  # Datei lässt sich nicht als Schrift lesen
        return QImage()
    ImageDraw.Draw(img).text((12, h // 2), text, font=pil_font, fill=color("text").name(),
                             anchor="lm")
    return QImage(img.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class FontDialog(QDialog):
    """Liste der installierten Schriften mit Suchfeld und Vorschau; Schriftdatei als Ausweg
    für nicht installierte Schriften."""

    def __init__(self, current: str | None = None, sample: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Schrift wählen")
        self.sample = sample.strip().splitlines()[0] if sample.strip() else SAMPLE_TEXT
        self.chosen: str | None = None
        self.fonts = installed_fonts()

        lay = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Schrift suchen …")
        self.search.setClearButtonEnabled(True)
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.setMinimumHeight(260)
        for name, path in self.fonts.items():
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.list.addItem(item)
        lay.addWidget(self.list, 1)
        self.preview = QLabel()
        self.preview.setObjectName("fontPreview")
        self.preview.setFixedSize(*PREVIEW_SIZE)
        lay.addWidget(self.preview)
        self.empty = QLabel("Keine installierte Schrift gefunden.")
        self.empty.setVisible(not self.fonts)
        lay.addWidget(self.empty)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Übernehmen")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        self.file_button = QPushButton("Schriftdatei wählen …")
        self.file_button.setToolTip("Schrift, die nicht installiert ist (.ttf, .otf)")
        buttons.addButton(self.file_button, QDialogButtonBox.ButtonRole.ActionRole)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        lay.addWidget(buttons)

        self.search.textChanged.connect(self._filter)
        self.list.currentItemChanged.connect(self._on_current)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        self.file_button.clicked.connect(self._choose_file)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        self._select(current)
        self.search.setFocus()

    def _select(self, current: str | None) -> None:
        row = 0
        if current:
            for i in range(self.list.count()):
                if os.path.normcase(self.list.item(i).data(Qt.ItemDataRole.UserRole)) == \
                        os.path.normcase(current):
                    row = i
                    break
        if self.list.count():
            self.list.setCurrentRow(row)
            self.list.scrollToItem(self.list.item(row),
                                   QListWidget.ScrollHint.PositionAtCenter)
        else:
            self._on_current(None)

    def _filter(self, text: str) -> None:
        needle = text.strip().lower()
        first = None
        for i in range(self.list.count()):
            item = self.list.item(i)
            hidden = needle not in item.text().lower()
            item.setHidden(hidden)
            if not hidden and first is None:
                first = item
        current = self.list.currentItem()
        if current is None or current.isHidden():
            self.list.setCurrentItem(first)
            if first is None:
                self._on_current(None)

    def _on_current(self, item: QListWidgetItem | None) -> None:
        path = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        self.ok_button.setEnabled(path is not None)
        self.preview.setPixmap(QPixmap.fromImage(preview_image(path, self.sample))
                               if path else QPixmap())

    def selected_path(self) -> str | None:
        item = self.list.currentItem()
        if item is None or item.isHidden():
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def accept(self) -> None:
        self.chosen = self.selected_path()
        if self.chosen is not None:
            super().accept()

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Schriftdatei wählen", "",
                                              "Schriften (*.ttf *.otf *.ttc)")
        if path:
            self.chosen = path
            super().accept()

    @classmethod
    def choose(cls, parent: QWidget | None, current: str | None, sample: str) -> str | None:
        """Gewählte Schriftdatei oder None (abgebrochen)."""
        dlg = cls(current, sample, parent)
        return dlg.chosen if dlg.exec() == QDialog.DialogCode.Accepted else None
