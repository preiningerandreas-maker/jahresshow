"""Eigenschaften der markierten Elemente (Dauer, Text, Formatmodus, Übergang, Titelkarte) und
des ganzen Projekts. Bei mehreren markierten Elementen gilt eine Änderung für alle."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from slideshow.core.chapters import chapter_title, is_chapter_title
from slideshow.core.importer import PHOTO_EXTS, describe_file
from slideshow.core.project import MAX_VOLUME, Item, Project, TextOverlay, readable_text

FIT_LABELS = {
    "blur": "Unscharfer Hintergrund",
    "black": "Schwarze Ränder",
    "crop": "Zuschneiden (füllt das Bild)",
}
FIT_HELP = ("Wie Hochkant-Fotos und Bilder mit anderem Seitenverhältnis gezeigt werden:\n"
            "• Unscharfer Hintergrund: ganzes Bild, daneben eine unscharfe Vergrößerung\n"
            "• Schwarze Ränder: ganzes Bild mit schwarzen Balken\n"
            "• Zuschneiden: füllt den Bildschirm, Ränder werden abgeschnitten")
TRANSITION_LABELS = {
    "fade": "Überblenden",
    "fadeblack": "Über Schwarz blenden",
    "wipeleft": "Wischen",
    "slideleft": "Schieben",
    "none": "Harter Schnitt",
}
POSITION_LABELS = {"top": "Oben", "center": "Mitte", "bottom": "Unten"}
BUNDLED_FONT = "Noto Sans"
OTHER_FONT = "Andere Schrift wählen …"
DEFAULT_TEXT = TextOverlay("")


class MixedCheckBox(QCheckBox):
    """Haken, der bei mehreren Elementen „teils/teils“ zeigen kann. Ein Klick darauf schaltet
    alle ein; erst der nächste Klick schaltet alle aus."""

    def set_values(self, values: list[bool]) -> None:
        mixed = len(set(values)) > 1
        self.setTristate(mixed)
        if mixed:
            self.setCheckState(Qt.CheckState.PartiallyChecked)
        else:
            self.setChecked(bool(values) and values[0])

    def nextCheckState(self) -> None:
        if self.checkState() == Qt.CheckState.PartiallyChecked:
            self.setTristate(False)
            self.setCheckState(Qt.CheckState.Checked)
        else:
            super().nextCheckState()


class ColorButton(QPushButton):
    """Knopf mit Farbfeld; öffnet die Farbauswahl."""

    colorChanged = Signal(str)

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._color = "#FFFFFF"
        self.clicked.connect(self._pick)
        self.set_color(self._color)

    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        self._color = QColor(color).name().upper()
        text = "#000000" if QColor(self._color).lightness() > 140 else "#FFFFFF"
        self.setText(self._color)
        self.setStyleSheet(f"QPushButton {{ background: {self._color}; color: {text}; }}")

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self._color), self, self._title)
        if c.isValid():
            self.set_color(c.name())
            self.colorChanged.emit(self._color)


def form_layout(box: QWidget) -> QFormLayout:
    """Formular für den schmalen Eigenschaftenbereich: wird es eng, steht die Beschriftung
    über dem Feld statt daneben."""
    form = QFormLayout(box)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return form


def combo() -> QComboBox:
    """Auswahlliste, die schmaler sein darf als ihr längster Eintrag (dieser wird gekürzt)."""
    c = QComboBox()
    c.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(8)
    return c


def _spin(lo: float, hi: float, step: float, suffix: str, special: str | None = None,
          decimals: int = 1) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    s.setSuffix(suffix)
    if special:
        s.setSpecialValueText(special)  # wird beim Minimum angezeigt
    s.setKeyboardTracking(False)
    s.setAccelerated(True)
    return s


class ItemPanel(QWidget):
    """Eigenschaften der markierten Elemente."""

    changed = Signal()  # Elemente wurden geändert (Zeitleiste neu zeichnen, Dauer neu)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project: Project | None = None
        self.items: list[Item] = []
        self._loading = False
        # Hat das Video eine Tonspur? Liefert das Hauptfenster (aus der Zeitleiste)
        self.has_track: Callable[[Item], bool] = lambda it: it.kind == "video"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.header = QLabel("Nichts ausgewählt")
        self.header.setWordWrap(True)
        f = self.header.font()
        f.setBold(True)
        self.header.setFont(f)
        outer.addWidget(self.header)
        self.hint = QLabel("Klicken Sie in der Zeitleiste auf ein Foto, Video oder einen "
                           "Titel, um es hier zu bearbeiten. Mit Strg oder Umschalt können "
                           "Sie mehrere markieren.")
        self.hint.setObjectName("propertiesHint")
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)

        # --- Beschädigte Datei ---
        self.damage_box = QGroupBox("Datei beschädigt")
        dlay = QVBoxLayout(self.damage_box)
        dlay.setContentsMargins(0, 4, 0, 0)
        self.damage_label = QLabel("")
        self.damage_label.setWordWrap(True)
        self.damage_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        dlay.addWidget(self.damage_label)
        self.use_damaged = QCheckBox("Trotzdem verwenden")
        self.use_damaged.setToolTip("Den lesbaren Teil des Bildes verwenden. Fehlende Bildteile "
                                    "erscheinen als einfarbige Fläche.")
        dlay.addWidget(self.use_damaged)
        outer.addWidget(self.damage_box)

        # --- Anzeige ---
        self.show_box = QGroupBox("Anzeige")
        form = form_layout(self.show_box)
        self.duration = _spin(0.0, 600.0, 0.5, " s", "Standard")
        self.duration.setToolTip("Wie lange das Element zu sehen ist. „Standard“: Fotos so "
                                 "lange wie im Projekt eingestellt, Videos in voller Länge.")
        form.addRow("Anzeigedauer", self.duration)
        self.start = _spin(0.0, 36000.0, 0.5, " s")
        self.start.setToolTip("Ab welcher Stelle das Video abgespielt wird")
        self.start_label = QLabel("Video ab")
        form.addRow(self.start_label, self.start)
        self.audio = MixedCheckBox("Originalton verwenden")
        self.audio.setToolTip("Ton des Videos abspielen. Die Musik wird währenddessen leiser. "
                              "Ein Strich bedeutet: nur bei einem Teil der markierten Videos.")
        form.addRow(self.audio)
        self.volume = QSpinBox()
        self.volume.setRange(0, round(MAX_VOLUME * 100))
        self.volume.setSingleStep(10)
        self.volume.setSuffix(" %")
        self.volume.setKeyboardTracking(False)
        self.volume.setToolTip("Lautstärke des Originaltons. 100 % = wie aufgenommen; über "
                               "100 % wird lauter, kann aber übersteuern.")
        self.volume_label = QLabel("Lautstärke")
        form.addRow(self.volume_label, self.volume)
        self.audio_note = QLabel("")
        self.audio_note.setObjectName("propertiesHint")
        self.audio_note.setWordWrap(True)
        form.addRow(self.audio_note)
        self.fit = combo()
        for key, label in FIT_LABELS.items():
            self.fit.addItem(label, key)
        self.fit.setToolTip(FIT_HELP)
        self.fit_label = QLabel("Bildformat")
        self.fit_label.setToolTip(FIT_HELP)
        form.addRow(self.fit_label, self.fit)
        self.transition = combo()
        self.transition.addItem("Wie im Projekt", None)
        for key, label in TRANSITION_LABELS.items():
            self.transition.addItem(label, key)
        self.transition.setToolTip("Übergang vom vorherigen Element zu diesem")
        form.addRow("Übergang", self.transition)
        self.transition_duration = _spin(0.0, 5.0, 0.25, " s", "Wie im Projekt", decimals=2)
        form.addRow("Übergangsdauer", self.transition_duration)
        outer.addWidget(self.show_box)

        # --- Titelkarte ---
        self.title_box = QGroupBox("Titel")
        tform = form_layout(self.title_box)
        self.background = ColorButton("Hintergrundfarbe")
        tform.addRow("Hintergrundfarbe", self.background)
        photo_row = QHBoxLayout()
        self.bg_photo = QPushButton("Foto wählen …")
        self.bg_photo.setToolTip("Titel auf einem Foto statt auf einer Farbfläche")
        self.bg_clear = QPushButton("Kein Foto")
        photo_row.addWidget(self.bg_photo)
        photo_row.addWidget(self.bg_clear)
        tform.addRow("Hintergrund", photo_row)
        self.bg_name = QLabel("")
        self.bg_name.setWordWrap(True)
        tform.addRow(self.bg_name)
        outer.addWidget(self.title_box)

        # --- Text ---
        self.text_box = QGroupBox("Text im Bild (optional)")
        xform = form_layout(self.text_box)
        self.text = QPlainTextEdit()
        # Hinweis als Tooltip statt Platzhalter: bleibt sichtbar, auch wenn Text drinsteht
        self.text.setToolTip("Text, der im Bild erscheint. Leer lassen für kein Text.")
        self.text.setTabChangesFocus(True)
        fm = self.text.fontMetrics()
        self.text.setFixedHeight(fm.lineSpacing() * 3 + 12)
        xform.addRow(self.text)
        self.position = combo()
        for key, label in POSITION_LABELS.items():
            self.position.addItem(label, key)
        xform.addRow("Position", self.position)
        self.font_combo = combo()
        self.font_combo.addItem(BUNDLED_FONT, BUNDLED_FONT)
        self.font_combo.addItem(OTHER_FONT, None)
        xform.addRow("Schrift", self.font_combo)
        size_row = QHBoxLayout()
        self.size = QSpinBox()
        self.size.setRange(12, 300)
        self.size.setSuffix(" px")
        self.size.setKeyboardTracking(False)
        self.bold = QCheckBox("Fett")
        size_row.addWidget(self.size, 1)
        size_row.addWidget(self.bold)
        xform.addRow("Größe", size_row)
        self.color = ColorButton("Textfarbe")
        xform.addRow("Farbe", self.color)
        self.bar = QCheckBox("Halbtransparenter Balken hinter dem Text")
        xform.addRow(self.bar)
        bar_row = QHBoxLayout()
        self.bar_color = ColorButton("Farbe des Balkens")
        self.bar_opacity = QSpinBox()
        self.bar_opacity.setRange(0, 100)
        self.bar_opacity.setSuffix(" %")
        self.bar_opacity.setToolTip("Deckkraft des Balkens")
        self.bar_opacity.setKeyboardTracking(False)
        bar_row.addWidget(self.bar_color, 1)
        bar_row.addWidget(self.bar_opacity)
        xform.addRow("Balken", bar_row)
        outer.addWidget(self.text_box)
        outer.addStretch(1)

        self._connect()
        self.set_items(None, [])

    # --- Verbindung zu den Elementen ---------------------------------------------------

    def _connect(self) -> None:
        self.duration.valueChanged.connect(
            lambda v: self._apply(lambda it: setattr(it, "duration", v or None)))
        self.start.valueChanged.connect(
            lambda v: self._apply(lambda it: setattr(it, "start", v), kinds=("video",)))
        self.audio.checkStateChanged.connect(self._on_audio)
        self.volume.valueChanged.connect(
            lambda v: self._apply_sound(lambda it: setattr(it, "volume", v / 100)))
        self.use_damaged.toggled.connect(self._on_use_damaged)
        self.fit.currentIndexChanged.connect(
            lambda _i: self._apply(lambda it: setattr(it, "fit", self.fit.currentData()),
                                   kinds=("photo", "video")))
        self.transition.currentIndexChanged.connect(
            lambda _i: self._apply(
                lambda it: setattr(it, "transition", self.transition.currentData())))
        self.transition_duration.valueChanged.connect(
            lambda v: self._apply(lambda it: setattr(it, "transition_duration", v or None)))
        self.background.colorChanged.connect(
            lambda c: self._apply(lambda it: setattr(it, "background", c), kinds=("title",)))
        self.bg_photo.clicked.connect(self._choose_background)
        self.bg_clear.clicked.connect(
            lambda: self._apply(lambda it: setattr(it, "path", None), kinds=("title",)))
        self.text.textChanged.connect(self._on_text)
        self.position.currentIndexChanged.connect(
            lambda _i: self._apply_text("position", self.position.currentData()))
        self.font_combo.activated.connect(self._on_font)
        self.size.valueChanged.connect(lambda v: self._apply_text("size", v))
        self.bold.toggled.connect(lambda v: self._apply_text("bold", v))
        self.color.colorChanged.connect(lambda c: self._apply_text("color", c))
        self.bar.toggled.connect(lambda v: self._apply_text("bar", v))
        self.bar_color.colorChanged.connect(lambda c: self._apply_text("bar_color", c))
        self.bar_opacity.valueChanged.connect(lambda v: self._apply_text("bar_opacity", v / 100))

    def _apply(self, fn: Callable[[Item], None], kinds: tuple[str, ...] | None = None) -> None:
        if self._loading or not self.items:
            return
        for it in self.items:
            if kinds is None or it.kind in kinds:
                fn(it)
        self._refresh_header()
        self.changed.emit()

    def _apply_sound(self, fn: Callable[[Item], None]) -> None:
        """Nur Videos mit Tonspur; bei den anderen gibt es nichts einzustellen."""
        self._apply(lambda it: fn(it) if self.has_track(it) else None, kinds=("video",))

    def _on_audio(self, state: Qt.CheckState) -> None:
        if state == Qt.CheckState.PartiallyChecked:
            return
        on = state == Qt.CheckState.Checked
        self._apply_sound(lambda it: setattr(it, "audio", on))
        self.volume.setEnabled(on)

    def _load_sound(self) -> None:
        """Haken (an, aus, teils/teils), Lautstärke und Hinweis auf Videos ohne Ton."""
        videos = [it for it in self.items if it.kind == "video"]
        tracks = [it for it in videos if self.has_track(it)]
        self.audio.setEnabled(bool(tracks))
        self.audio.set_values([it.audio for it in tracks])
        self.volume.setValue(round(tracks[0].volume * 100) if tracks else 100)
        self.volume.setEnabled(self.audio.checkState() != Qt.CheckState.Unchecked)
        silent = len(videos) - len(tracks)
        if not silent:
            note = ""
        elif not tracks:
            note = ("Dieses Video hat keinen Ton." if len(videos) == 1
                    else "Die markierten Videos haben keinen Ton.")
        else:
            note = (f"{silent} der {len(videos)} markierten Videos haben keinen Ton, für sie "
                    "ändert sich nichts.")
        self.audio_note.setText(note)
        self.audio_note.setVisible(bool(note))

    def _on_use_damaged(self, on: bool) -> None:
        def apply(it: Item) -> None:
            if it.damage_usable:
                it.use_damaged = on

        self._apply(apply)
        self._load_damage()

    def _load_damage(self) -> None:
        """Hinweis mit Dateiname, Pfad und Grund; „trotzdem verwenden“, wo es möglich ist."""
        damaged = [it for it in self.items if it.damage is not None]
        self.damage_box.setVisible(bool(damaged))
        if not damaged:
            return
        only_minor = all(it.damage_minor for it in damaged)
        self.damage_box.setTitle("Datei leicht beschädigt" if only_minor else "Datei beschädigt")
        lines = []
        for it in damaged[:5]:
            assert self.project is not None and it.path is not None
            if it.damage_minor:
                lines.append(f"{describe_file(self.project.resolve(it.path))}: Datei leicht "
                             "beschädigt, vollständig lesbar. Wird verwendet.")
                continue
            line = (f"{describe_file(self.project.resolve(it.path))}: {it.damage}, "
                    f"{readable_text(it.damage_readable)}.")
            if it.skip_damaged:
                line += " Wird beim Export übersprungen."
            else:
                line += " Wird mit dem lesbaren Teil verwendet."
            lines.append(line)
        if len(damaged) > 5:
            lines.append(f"… und {len(damaged) - 5} weitere")
        usable = [it for it in damaged if it.damage_usable and not it.damage_minor]
        if not usable and not only_minor:
            lines.append("Der lesbare Teil reicht nicht, um die Datei zu verwenden.")
        self.damage_label.setText("\n".join(lines))
        self.use_damaged.setVisible(bool(usable))
        self.use_damaged.setChecked(bool(usable) and all(it.use_damaged for it in usable))

    def _overlay(self, it: Item) -> TextOverlay:
        if it.text is None:
            first = self.items[0].text if self.items and self.items[0].text else DEFAULT_TEXT
            it.text = TextOverlay("", first.position, first.font, first.size, first.color,
                                  first.bold, first.bar, first.bar_color, first.bar_opacity)
        return it.text

    def _apply_text(self, field: str, value: object) -> None:
        self._apply(lambda it: setattr(self._overlay(it), field, value))

    def _on_text(self) -> None:
        text = self.text.toPlainText()
        self._apply(lambda it: setattr(self._overlay(it), "text", text))

    def _on_font(self, _index: int) -> None:
        data = self.font_combo.currentData()
        if data is None:  # eigene Schriftdatei
            path, _ = QFileDialog.getOpenFileName(self, "Schrift wählen", "",
                                                  "Schriften (*.ttf *.otf)")
            if not path:
                self._load()
                return
            if self.font_combo.findData(path) < 0:
                self.font_combo.insertItem(self.font_combo.count() - 1, Path(path).stem, path)
            self.font_combo.setCurrentIndex(self.font_combo.findData(path))
            data = path
        self._apply_text("font", data)

    def _choose_background(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(PHOTO_EXTS))
        path, _ = QFileDialog.getOpenFileName(self, "Hintergrundfoto wählen", "",
                                              f"Fotos ({exts})")
        if path:
            self.set_background_photo(path)

    def set_background_photo(self, path: str | None) -> None:
        self._apply(lambda it: setattr(it, "path", path), kinds=("title",))
        self._load()

    # --- Anzeige -----------------------------------------------------------------------

    def set_items(self, project: Project | None, items: list[Item]) -> None:
        self.project = project
        self.items = items
        self._load()

    def _refresh_header(self) -> None:
        items = self.items
        if not items:
            self.header.setText("Nichts ausgewählt")
        elif len(items) > 1:
            self.header.setText(f"{len(items)} Elemente ausgewählt: Änderungen gelten für alle")
        else:
            it = items[0]
            if is_chapter_title(it):
                assert it.chapter is not None
                self.header.setText(f"Zwischentitel {chapter_title(it.chapter)}")
            elif it.kind == "title":
                self.header.setText("Titelkarte")
            else:
                kind = "Foto" if it.kind == "photo" else "Video"
                self.header.setText(f"{kind}: {Path(it.path or '').name}")

    def _load(self) -> None:
        self._loading = True
        try:
            items = self.items
            self._refresh_header()
            self.hint.setVisible(not items)
            self._load_damage()
            kinds = {it.kind for it in items}
            for box in (self.show_box, self.text_box):
                box.setVisible(bool(items))
            self.title_box.setVisible(bool(items) and kinds == {"title"})
            has_video = "video" in kinds
            for w in (self.start, self.start_label, self.audio, self.volume,
                      self.volume_label):
                w.setVisible(has_video)
            self._load_sound()
            has_media = bool(kinds & {"photo", "video"})
            self.fit.setVisible(has_media)
            self.fit_label.setVisible(has_media)
            if not items:
                return
            it = items[0]
            self.duration.setValue(it.duration or 0.0)
            self.start.setValue(it.start)
            self.fit.setCurrentIndex(max(0, self.fit.findData(it.fit)))
            self.transition.setCurrentIndex(max(0, self.transition.findData(it.transition)))
            self.transition_duration.setValue(it.transition_duration or 0.0)
            self.background.set_color(it.background)
            self.bg_name.setText(f"Foto: {Path(it.path).name}" if it.kind == "title"
                                 and it.path else "")
            ov = it.text or DEFAULT_TEXT
            if self.text.toPlainText() != ov.text:
                self.text.setPlainText(ov.text)
            self.position.setCurrentIndex(max(0, self.position.findData(ov.position)))
            font_idx = self.font_combo.findData(ov.font)
            if font_idx < 0:
                self.font_combo.insertItem(self.font_combo.count() - 1, Path(ov.font).stem,
                                           ov.font)
                font_idx = self.font_combo.findData(ov.font)
            self.font_combo.setCurrentIndex(font_idx)
            self.size.setValue(ov.size)
            self.bold.setChecked(ov.bold)
            self.color.set_color(ov.color)
            self.bar.setChecked(ov.bar)
            self.bar_color.set_color(ov.bar_color)
            self.bar_opacity.setValue(round(ov.bar_opacity * 100))
        finally:
            self._loading = False


class ProjectPanel(QWidget):
    """Einstellungen für die ganze Show."""

    changed = Signal()
    chaptersToggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project: Project | None = None
        self._loading = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QGroupBox("Ganze Show")
        form = form_layout(box)
        self.name = QLineEdit()
        self.name.setToolTip("Name des Projekts, Vorschlag für den Dateinamen beim Export")
        form.addRow("Name", self.name)
        self.photo_duration = _spin(0.5, 60.0, 0.5, " s")
        form.addRow("Fotos zeigen", self.photo_duration)
        self.transition = combo()
        for key, label in TRANSITION_LABELS.items():
            self.transition.addItem(label, key)
        form.addRow("Übergang", self.transition)
        self.transition_duration = _spin(0.25, 5.0, 0.25, " s", decimals=2)
        form.addRow("Übergangsdauer", self.transition_duration)
        self.chapters = QCheckBox("Zwischentitel für jeden Monat")
        self.chapters.setToolTip("Vor jedem neuen Monat erscheint ein Titel wie „März 2026“. "
                                 "Den Text können Sie in der Zeitleiste anklicken und ändern.")
        form.addRow(self.chapters)
        self.resolution = combo()
        self.resolution.addItem("Full HD 1920×1080 (empfohlen)", "1080p")
        self.resolution.addItem("4K 3840×2160 (große Datei, langsam)", "2160p")
        form.addRow("Bildgröße", self.resolution)
        outer.addWidget(box)
        outer.addStretch(1)

        self.name.textEdited.connect(lambda t: self._set(lambda p: setattr(p, "name", t)))
        self.photo_duration.valueChanged.connect(
            lambda v: self._set(lambda p: setattr(p.settings, "photo_duration", v)))
        self.transition.currentIndexChanged.connect(
            lambda _i: self._set(
                lambda p: setattr(p.settings, "transition", self.transition.currentData())))
        self.transition_duration.valueChanged.connect(
            lambda v: self._set(lambda p: setattr(p.settings, "transition_duration", v)))
        self.resolution.currentIndexChanged.connect(
            lambda _i: self._set(
                lambda p: setattr(p.settings, "resolution", self.resolution.currentData())))
        self.chapters.toggled.connect(self._on_chapters)

    def _set(self, fn: Callable[[Project], None]) -> None:
        if self._loading or self.project is None:
            return
        fn(self.project)
        self.changed.emit()

    def _on_chapters(self, on: bool) -> None:
        if not self._loading:
            self.chaptersToggled.emit(on)

    def set_project(self, project: Project) -> None:
        self.project = project
        self._loading = True
        try:
            s = project.settings
            self.name.setText(project.name)
            self.photo_duration.setValue(s.photo_duration)
            self.transition.setCurrentIndex(max(0, self.transition.findData(s.transition)))
            self.transition_duration.setValue(s.transition_duration)
            self.chapters.setChecked(s.chapters)
            self.resolution.setCurrentIndex(max(0, self.resolution.findData(s.resolution)))
        finally:
            self._loading = False

    def set_chapters_checked(self, on: bool) -> None:
        self._loading = True
        try:
            self.chapters.setChecked(on)
        finally:
            self._loading = False
