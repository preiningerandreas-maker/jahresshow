"""Erscheinungsbild: Farbtokens, Lesbarkeit (WCAG AA), Akzentfarbe nur wo vorgesehen,
Symbole, Aufbau mit Raster | Monitor | Eigenschaften."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QStyle, QStyleOptionViewItem

from slideshow.app import theme
from slideshow.app.icons import ICON_DIR, icon
from slideshow.app.mainwindow import MainWindow
from slideshow.app.theme import TEXT_ON, TOKENS, contrast, stylesheet

T = TOKENS


def test_tokens_as_specified() -> None:
    assert T == {"bg": "#1E1E1E", "area": "#252525", "input": "#2E2E2E", "line": "#3A3A3A",
                 "text": "#D4D4D4", "muted": "#8A8A8A", "accent": "#2D8CEB"}


def test_contrast_formula_matches_wcag_examples() -> None:
    assert contrast("#000000", "#FFFFFF") == pytest.approx(21.0)
    # bekanntes Grenzbeispiel: #777777 auf Weiß liegt knapp unter 4.5
    assert contrast("#777777", "#FFFFFF") == pytest.approx(4.48, abs=0.01)


@pytest.mark.parametrize(("fg", "bg"), [(fg, bg) for fg, bgs in TEXT_ON.items() for bg in bgs])
def test_text_contrast_at_least_aa(fg: str, bg: str) -> None:
    """Jede verwendete Kombination Text/Hintergrund erreicht mindestens 4.5:1."""
    ratio = contrast(T[fg], T[bg])
    assert ratio >= 4.5, f"{fg} auf {bg}: {ratio:.2f}"


def test_combinations_below_aa_are_not_declared() -> None:
    """Dokumentiert, warum gedämpfter Text nur auf dem Hintergrund steht und auf Blau dunkle
    Schrift: die übrigen Kombinationen erreichen 4.5:1 nicht."""
    assert contrast(T["muted"], T["area"]) < 4.5
    assert contrast(T["muted"], T["input"]) < 4.5
    assert contrast("#FFFFFF", T["accent"]) < 4.5
    assert "area" not in TEXT_ON["muted"] and "input" not in TEXT_ON["muted"]


def rules(qss: str) -> list[tuple[str, dict[str, str]]]:
    """QSS in (Selektor, {Eigenschaft: Wert}) zerlegen."""
    out = []
    for block in qss.split("}"):
        if "{" not in block:
            continue
        sel, body = block.split("{", 1)
        props = {}
        for decl in body.split(";"):
            if ":" in decl:
                k, v = decl.split(":", 1)
                props[k.strip()] = v.strip()
        out.append((" ".join(sel.split()), props))
    return out


def test_stylesheet_uses_only_tokens() -> None:
    colors = {c.upper() for c in re.findall(r"#[0-9A-Fa-f]{6}\b", stylesheet())}
    assert colors <= set(T.values())


def test_accent_only_for_selection_tabs_and_main_button() -> None:
    for sel, props in rules(stylesheet()):
        for key, value in props.items():
            if T["accent"] not in value:
                continue
            allowed = (key == "selection-background-color" or ":selected" in sel
                       or "#primaryButton" in sel)
            assert allowed, f"Blau in {sel} {{ {key} }}"
            if key in ("background", "selection-background-color"):
                # Schrift auf Blau ist dunkel (4.8:1); weiß hätte nur 3.5:1
                text_key = "color" if key == "background" else "selection-color"
                assert props.get(text_key) == T["bg"], sel


def test_muted_text_only_on_background_or_disabled() -> None:
    for sel, props in rules(stylesheet()):
        if props.get("color") != T["muted"]:
            continue
        assert ":disabled" in sel or props.get("background") == T["bg"], sel


def test_no_placeholder_text_in_inputs(qtbot, tmp_path: Path) -> None:  # noqa: ANN001
    """Platzhalter erscheinen gedämpft im Eingabefeld (nur 3.9:1), daher keine."""
    w = MainWindow(cache_dir=tmp_path / "cache", encoder="libx264")
    qtbot.addWidget(w)
    try:
        for edit in w.findChildren(QLineEdit) + w.findChildren(QPlainTextEdit):
            assert not edit.placeholderText(), edit.objectName()
    finally:
        w.shutdown()


def test_icons_are_free_and_recolored(qtbot) -> None:  # noqa: ANN001
    assert "MIT License" in (ICON_DIR / "LICENSE.txt").read_text("utf-8")
    assert "fluentui" in (ICON_DIR / "HERKUNFT.txt").read_text("utf-8")
    for svg in ICON_DIR.glob("*.svg"):
        text = svg.read_text("utf-8")
        assert "<script" not in text and "href" not in text
        img = icon(svg.stem).pixmap(48, 48).toImage()
        opaque = [img.pixelColor(x, y) for x in range(48) for y in range(48)
                  if img.pixelColor(x, y).alpha() == 255]
        assert opaque, svg.name
        assert {c.name().upper() for c in opaque} == {T["text"]}, svg.name  # einfarbig


@pytest.fixture
def themed_window(qtbot, tmp_path: Path):  # noqa: ANN001, ANN201
    w = MainWindow(cache_dir=tmp_path / "cache", encoder="libx264")
    qtbot.addWidget(w)
    w.resize(1280, 640)
    w.show()
    qtbot.wait(50)
    yield w
    w.shutdown()


def test_layout_raster_monitor_properties(themed_window: MainWindow) -> None:
    w = themed_window
    s = w.splitter
    names = [s.widget(i).objectName() for i in range(s.count())]
    assert names == ["rasterPane", "monitorPane", "propertiesPane"]
    sizes = s.sizes()
    assert sizes[1] == max(sizes)  # der Monitor nimmt den meisten Platz ein
    assert not s.childrenCollapsible()
    # Trennbalken verschiebbar
    s.setSizes([sizes[0] + 80, sizes[1] - 80, sizes[2]])
    assert s.sizes()[0] == sizes[0] + 80
    # Monitor zeigt 16:9
    r = w.still.frame_rect()
    assert abs(r.width() * 9 - r.height() * 16) <= 16


def test_toolbar_icons_short_texts_and_single_primary_button(themed_window: MainWindow) -> None:
    w = themed_window
    tb = w.toolbar
    buttons = [a for a in tb.actions() if not a.isSeparator() and tb.widgetForAction(a)]
    texts = [a.iconText() for a in buttons if a.iconText()]
    assert texts[:7] == ["Öffnen", "Speichern", "Ordner", "Dateien", "Titel", "Musik",
                         "Vorschau"]
    assert all(not a.icon().isNull() for a in buttons if a.iconText())
    primaries = [b for b in tb.findChildren(type(w.export_button))
                 if b.objectName() == "primaryButton"]
    assert primaries == [w.export_button]
    # ganz rechts: letztes Element, davor ein dehnbarer Abstandhalter (ob alles sichtbar
    # passt, prüfen die Screenshot-Tests mit echter Schrift)
    widgets = [tb.widgetForAction(a) for a in tb.actions()]
    assert widgets[-1] is w.export_button
    assert widgets[-2].sizePolicy().horizontalPolicy().name == "Expanding"
    # deaktiviert, solange ein Video erstellt wird
    w.act_export.setEnabled(False)
    assert not w.export_button.isEnabled()
    w.act_export.setEnabled(True)


def test_primary_button_dark_text_on_accent(themed_window: MainWindow) -> None:
    img = themed_window.export_button.grab().toImage()
    colors = {img.pixelColor(x, y).name().upper()
              for x in range(img.width()) for y in range(img.height())}
    assert T["accent"] in colors


def test_tile_selection_is_blue_border(themed_window: MainWindow, tmp_path: Path) -> None:
    w = themed_window
    view = w.timeline
    delegate = view.itemDelegate()
    idx = w.model.index(0)
    size = delegate.sizeHint(QStyleOptionViewItem(), idx)

    def paint(selected: bool) -> QImage:
        img = QImage(size, QImage.Format.Format_RGB32)
        img.fill(QColor(T["bg"]))
        opt = QStyleOptionViewItem()
        view.initViewItemOption(opt)
        opt.rect = QRect(0, 0, size.width(), size.height())
        if selected:
            opt.state |= QStyle.StateFlag.State_Selected
        p = QPainter(img)
        delegate.paint(p, opt, idx)
        p.end()
        return img

    sel, unsel = paint(True), paint(False)
    mid_y = size.height() // 2
    edge = sel.pixelColor(5, mid_y)  # Rand links (Kachel 4 px eingerückt, Linie 2 px)
    assert edge.name().upper() == T["accent"]
    assert unsel.pixelColor(5, mid_y).name().upper() != T["accent"]
    # keine Füllung: Hintergrund unter dem Text bleibt dunkel
    below = sel.pixelColor(size.width() // 2, size.height() - 8)
    assert below.name().upper() == T["bg"]


def test_progress_bar_filled_in_every_windows_style(qtbot) -> None:  # noqa: ANN001
    from PySide6.QtWidgets import QStyleFactory

    from slideshow.app.activity import ProgressBar

    app = QApplication.instance()
    assert isinstance(app, QApplication)
    available = QStyleFactory.keys()
    try:
        for style in ("windows11", "windowsvista", "Fusion"):
            if style not in available:
                continue
            app.setStyle(style)
            app.setStyleSheet(stylesheet())
            bar = ProgressBar()
            qtbot.addWidget(bar)
            bar.resize(150, 10)
            bar.set_fraction(0.5)
            img = bar.grab().toImage()
            y = img.height() // 2
            assert img.pixelColor(img.width() // 4, y).name().upper() == T["muted"], style
            assert img.pixelColor(img.width() * 9 // 10, y).name().upper() == T["input"], style
            assert bar.percent.text() == "50 %"
    finally:
        theme.apply_theme(app)


def test_theme_applies_dark_palette(themed_window: MainWindow) -> None:
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    pal = app.palette()
    assert pal.color(pal.ColorRole.Window).name().upper() == T["bg"]
    assert pal.color(pal.ColorRole.Highlight).name().upper() == T["accent"]
    assert pal.color(pal.ColorRole.HighlightedText).name().upper() == T["bg"]
    assert app.styleSheet() == stylesheet()
