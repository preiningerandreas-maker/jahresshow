"""Schriftauswahl: installierte Windows-Schriften, Suche, Vorschau wie im Export, Übernahme
in die Eigenschaften."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog

from slideshow.app.fonts import FontDialog, font_label, font_name, installed_fonts, preview_image
from slideshow.core.project import Item, Project, TextOverlay
from slideshow.render.text import DEFAULT_FONT

windows = pytest.mark.skipif(sys.platform != "win32", reason="Schriftenliste nur unter Windows")


def test_font_names_from_registry() -> None:
    assert font_name("Arial Bold (TrueType)") == "Arial Bold"
    assert font_name("Cambria & Cambria Math (TrueType)") == "Cambria"
    assert font_name("Source Sans Pro (OpenType)") == "Source Sans Pro"


@windows
def test_installed_fonts_lists_windows_fonts() -> None:
    fonts = installed_fonts()
    assert fonts["Arial"].name.lower() == "arial.ttf" and fonts["Arial"].is_file()
    assert "Arial Bold" in fonts  # Schnitte sind eigene Einträge
    assert not any("TrueType" in n or "Wingdings" in n for n in fonts)  # ohne Symbolschriften
    assert list(fonts) == sorted(fonts, key=str.lower)
    assert font_label(str(fonts["Arial"]), fonts) == "Arial"
    assert font_label(r"D:\Schriften\Meine Schrift.ttf", fonts) == "Meine Schrift"


def test_preview_drawn_like_export(qtbot) -> None:  # noqa: ANN001
    img = preview_image(str(DEFAULT_FONT), "Wörthersee")
    assert not img.isNull()
    colours = {img.pixel(x, y) for x in range(0, img.width(), 3) for y in range(0, img.height(), 3)}
    assert len(colours) > 2  # Text ist zu sehen, nicht nur Hintergrund
    assert preview_image(str(Path(__file__)), "x").isNull()  # keine Schriftdatei
    # langer Text wird verkleinert: rechter Rand bleibt frei
    long = preview_image(str(DEFAULT_FONT), "Sommerurlaub am Wörthersee mit der ganzen Familie")
    right = {long.pixel(long.width() - 4, y) for y in range(long.height())}
    assert len(right) == 1


@windows
def test_dialog_search_preview_and_choice(qtbot) -> None:  # noqa: ANN001
    fonts = installed_fonts()
    dlg = FontDialog(str(fonts["Arial"]), "Urlaub 2026")
    qtbot.addWidget(dlg)
    assert dlg.list.currentItem().text() == "Arial"  # aktuelle Schrift vorgewählt
    assert not dlg.preview.pixmap().isNull()

    dlg.search.setText("calib")
    visible = [dlg.list.item(i).text() for i in range(dlg.list.count())
               if not dlg.list.item(i).isHidden()]
    assert visible and all("calib" in n.lower() for n in visible)
    assert dlg.list.currentItem().text() == visible[0]
    dlg.accept()
    assert dlg.result() == QDialog.DialogCode.Accepted
    assert dlg.chosen == str(fonts[visible[0]])

    dlg = FontDialog(None, "")
    qtbot.addWidget(dlg)
    dlg.search.setText("gibt es nicht xyz")
    assert not dlg.ok_button.isEnabled() and dlg.preview.pixmap().isNull()
    dlg.accept()
    assert dlg.chosen is None and dlg.result() != QDialog.DialogCode.Accepted


def test_other_font_opens_font_dialog(qtbot, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
                                      tmp_path: Path) -> None:
    from slideshow.app.properties import OTHER_FONT, ItemPanel

    calls: list[tuple[str | None, str]] = []
    chosen = str(tmp_path / "Meine Schrift.ttf")

    def fake_choose(_parent: object, current: str | None, sample: str) -> str:
        calls.append((current, sample))
        return chosen

    monkeypatch.setattr(FontDialog, "choose", staticmethod(fake_choose))
    item = Item("title", text=TextOverlay("Sommer 2026"))
    panel = ItemPanel()
    qtbot.addWidget(panel)
    panel.set_items(Project(items=[item]), [item])
    other = panel.font_combo.findText(OTHER_FONT)
    panel.font_combo.setCurrentIndex(other)
    panel.font_combo.activated.emit(other)
    assert calls == [("Noto Sans", "Sommer 2026")]
    assert item.text is not None and item.text.font == chosen
    assert panel.font_combo.currentText() == "Meine Schrift"
