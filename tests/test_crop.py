"""Bildausschnitt eines Fotos: Modell und alte Projektdateien, Pixelrechnung, Render
(Segment und Standbild-Vorschau), Zuschnitt-Dialog und Eigenschaften-Panel."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from slideshow.app.cropdialog import CropDialog, CropView, clamp_rect, to_qimage
from slideshow.app.properties import ItemPanel
from slideshow.core.photoedit import apply_crop, crop_box, draft_size
from slideshow.core.project import Item, Project
from slideshow.render.segment import (
    RenderConfig,
    _item_settings,
    prepare_photo,
    render_segment,
    render_still,
)

RED, BLUE = (220, 30, 30), (30, 30, 220)


def two_color_photo(path: Path, size: tuple[int, int] = (1200, 800)) -> Path:
    """Linke Hälfte rot, rechte Hälfte blau."""
    img = Image.new("RGB", size, RED)
    img.paste(Image.new("RGB", (size[0] // 2, size[1]), BLUE), (size[0] // 2, 0))
    img.save(path, "JPEG", quality=95)
    return path


# --- Modell ---------------------------------------------------------------------------


def test_crop_roundtrip_and_old_projects_load(tmp_path: Path) -> None:
    p = Project(items=[Item("photo", path="a.jpg", crop=[0.1, 0.2, 0.5, 0.6]),
                       Item("photo", path="b.jpg")], base_dir=tmp_path)
    target = tmp_path / "p.json"
    p.save(target)
    loaded = Project.load(target)
    assert loaded.items[0].crop == [0.1, 0.2, 0.5, 0.6]
    assert loaded.items[1].crop is None

    # Alte Projektdatei: das Feld „crop“ gibt es nicht
    data = json.loads(target.read_text("utf-8"))
    for raw in data["items"]:
        raw.pop("crop", None)
    target.write_text(json.dumps(data), "utf-8")
    old = Project.load(target)
    assert [it.crop for it in old.items] == [None, None]


@pytest.mark.parametrize("crop", [[0.1, 0.1, 0.5], [-0.2, 0, 0.5, 0.5], [0, 0, 0, 0.5],
                                  [0.6, 0, 0.5, 0.5], [0, 0.7, 0.5, 0.5]])
def test_invalid_crop_rejected(crop: list[float]) -> None:
    with pytest.raises(ValueError, match="Bildausschnitt"):
        Item("photo", path="a.jpg", crop=crop).validate()


def test_crop_not_in_segment_key_when_unset() -> None:
    """Segmente aus früheren Versionen bleiben gültig: ohne Ausschnitt gleicher Schlüssel."""
    assert "crop" not in _item_settings(Item("photo", path="a.jpg"))
    assert _item_settings(Item("photo", path="a.jpg", crop=[0, 0, 0.5, 0.5]))["crop"] == [
        0, 0, 0.5, 0.5]


# --- Pixelrechnung --------------------------------------------------------------------


def test_crop_box_and_apply() -> None:
    assert crop_box((1000, 500), [0.25, 0.5, 0.5, 0.5]) == (250, 250, 750, 500)
    assert crop_box((1000, 500), [0, 0, 1, 1]) == (0, 0, 1000, 500)
    # winziger Ausschnitt am Rand bleibt im Bild und mindestens 2 Pixel groß
    left, top, right, bottom = crop_box((100, 100), [1.0, 1.0, 0.0001, 0.0001])
    assert (right - left, bottom - top) == (2, 2) and right <= 100 and bottom <= 100

    img = Image.new("RGB", (400, 200))
    assert apply_crop(img, None) is img
    assert apply_crop(img, [0, 0, 1, 1]) is img
    assert apply_crop(img, [0.5, 0, 0.5, 1]).size == (200, 200)


def test_draft_size_covers_cropped_area() -> None:
    assert draft_size(640, 360, None) == (640, 360)
    w, h = draft_size(640, 360, [0, 0, 0.25, 0.5])
    assert w >= 640 * 4 and h >= 360 * 2


def test_prepare_photo_cuts_cropped_part(tmp_path: Path) -> None:
    src = two_color_photo(tmp_path / "zwei Farben.jpg")
    out = prepare_photo(src, 1920, 1080, tmp_path / "a.png", crop=[0.5, 0.0, 0.5, 1.0])
    with Image.open(out) as im:
        assert im.size == (600, 800)  # rechte Hälfte, nicht hochskaliert
        r, g, b = im.convert("RGB").getpixel((300, 400))
        assert b > 150 and r < 80  # blau
    # Mit beschleunigtem Laden (Vorschau) dasselbe Ergebnis in der Sache
    out = prepare_photo(src, 640, 360, tmp_path / "b.png", fast=True, crop=[0.0, 0.0, 0.5, 1.0])
    with Image.open(out) as im:
        assert im.convert("RGB").getpixel((im.width // 2, im.height // 2))[0] > 150  # rot


# --- Render ---------------------------------------------------------------------------


@pytest.mark.slow
def test_still_and_segment_show_crop(synthetic: Path, tmp_path: Path) -> None:
    src = two_color_photo(tmp_path / "zwei Farben.jpg")
    cache = tmp_path / "cache"
    whole = Item("photo", path=str(src), duration=1.0, fit="black")
    right = Item("photo", path=str(src), duration=1.0, fit="crop", crop=[0.5, 0, 0.5, 1])
    project = Project(items=[right], base_dir=tmp_path)

    still = render_still(project, right, 640, 360, tmp_path / "s.png", cache)
    with Image.open(still) as im:
        rgb = im.convert("RGB")
        for xy in ((10, 10), (320, 180), (630, 350)):
            r, _g, b = rgb.getpixel(xy)
            assert b > 150 and r < 80, xy  # überall blau: nur die rechte Hälfte

    entry = project.timeline(lambda _p: 0.0)[0]
    seg, _ = render_segment(project, entry, RenderConfig(640, 360, "libx264", cache))
    assert seg.is_file()

    # Anderer Ausschnitt = anderes Segment
    project2 = Project(items=[whole], base_dir=tmp_path)
    seg2, _ = render_segment(project2, project2.timeline(lambda _p: 0.0)[0],
                             RenderConfig(640, 360, "libx264", cache))
    assert seg2 != seg


# --- Oberfläche -----------------------------------------------------------------------


def test_clamp_rect_keeps_minimum_and_bounds() -> None:
    left, top, right, bottom = clamp_rect(0.99, 0.99, 0.995, 1.0)
    assert 0 <= left < right <= 1 and 0 <= top < bottom <= 1
    assert right - left >= 0.02 - 1e-9
    assert clamp_rect(-1, -1, 2, 2) == (0.0, 0.0, 1.0, 1.0)


@pytest.fixture
def view(qtbot, tmp_path: Path) -> CropView:  # noqa: ANN001
    v = CropView(to_qimage(Image.open(two_color_photo(tmp_path / "v.jpg"))))
    qtbot.addWidget(v)
    v.resize(640, 460)
    v.show()
    return v


def _drag(view: CropView, a: QPointF, b: QPointF) -> None:
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     a.toPoint())
    QTest.mouseMove(view, b.toPoint())
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       b.toPoint())


def test_view_draws_new_rectangle_moves_and_resizes(view: CropView) -> None:
    assert view.crop() is None  # ohne Ausschnitt: ganzes Bild
    ir = view.image_rect()

    # Neues Rechteck aufziehen: oben links in der Mitte bis Viertel / Dreiviertel
    start = QPointF(ir.x() + ir.width() * 0.25, ir.y() + ir.height() * 0.25)
    end = QPointF(ir.x() + ir.width() * 0.75, ir.y() + ir.height() * 0.75)
    # Zuerst auf das ganze Bild verkleinern geht nicht ohne Griff: außerhalb beginnen
    view.set_crop([0.4, 0.4, 0.2, 0.2])
    _drag(view, start, end)
    x, y, w, h = view.crop() or (0, 0, 0, 0)
    assert (round(x, 2), round(y, 2), round(w, 2), round(h, 2)) == (0.25, 0.25, 0.5, 0.5)

    # Verschieben: in die Mitte klicken und nach rechts unten ziehen, bleibt im Bild
    c = view.crop_rect().center()
    _drag(view, c, QPointF(c.x() + 10_000, c.y() + 10_000))
    x, y, w, h = view.crop() or (0, 0, 0, 0)
    assert round(x + w, 3) == 1.0 and round(y + h, 3) == 1.0 and round(w, 2) == 0.5

    # Größe ändern: rechten Rand nach links ziehen
    cr = view.crop_rect()
    _drag(view, QPointF(cr.right(), cr.center().y()),
          QPointF(cr.right() - cr.width() / 2, cr.center().y()))
    x2, _y2, w2, _h2 = view.crop() or (0, 0, 0, 0)
    assert round(x2, 3) == round(x, 3) and round(w2, 2) == 0.25


def test_view_click_outside_without_drag_keeps_crop(view: CropView) -> None:
    view.set_crop([0.2, 0.2, 0.3, 0.3])
    before = view.crop()
    ir = view.image_rect()
    p = QPointF(ir.right() - 5, ir.bottom() - 5)
    _drag(view, p, p)
    assert view.crop() == before


def test_dialog_result_and_reset(qtbot, tmp_path: Path) -> None:  # noqa: ANN001
    img = Image.open(two_color_photo(tmp_path / "d.jpg"))
    dialog = CropDialog(img, [0.1, 0.1, 0.5, 0.5])
    qtbot.addWidget(dialog)
    assert dialog.result_crop() == pytest.approx([0.1, 0.1, 0.5, 0.5])
    assert "Ausschnitt" in dialog.info.text()
    dialog.reset_button.click()
    assert dialog.result_crop() is None and dialog.info.text() == "Ganzes Foto"


def test_panel_sets_and_clears_crop(qtbot, tmp_path: Path,  # noqa: ANN001
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    src = two_color_photo(tmp_path / "p.jpg")
    project = Project(items=[Item("photo", path=str(src)), Item("photo", path=str(src))],
                      base_dir=tmp_path)
    panel = ItemPanel()
    qtbot.addWidget(panel)
    panel.show()
    changes: list[int] = []
    panel.changed.connect(lambda: changes.append(1))

    # Mehrere Fotos markiert: kein Ausschnitt
    panel.set_items(project, list(project.items))
    assert not panel.crop_button.isVisibleTo(panel)

    panel.set_items(project, [project.items[0]])
    assert panel.crop_button.isVisibleTo(panel) and not panel.crop_clear.isEnabled()

    monkeypatch.setattr(CropDialog, "exec", lambda self: CropDialog.DialogCode.Accepted)
    monkeypatch.setattr(CropDialog, "result_crop", lambda self: [0.0, 0.0, 0.5, 0.5])
    panel.crop_button.click()
    assert project.items[0].crop == [0.0, 0.0, 0.5, 0.5] and project.items[1].crop is None
    assert changes and panel.crop_clear.isEnabled() and "50 %" in panel.crop_state.text()

    panel.crop_clear.click()
    assert project.items[0].crop is None and not panel.crop_clear.isEnabled()

    # Abbrechen ändert nichts
    monkeypatch.setattr(CropDialog, "exec", lambda self: CropDialog.DialogCode.Rejected)
    n = len(changes)
    panel.crop_button.click()
    assert project.items[0].crop is None and len(changes) == n


def test_panel_hides_crop_for_video_and_title(qtbot, tmp_path: Path) -> None:  # noqa: ANN001
    project = Project(items=[Item("title"), Item("video", path="v.mp4")], base_dir=tmp_path)
    panel = ItemPanel()
    qtbot.addWidget(panel)
    panel.show()
    for item in project.items:
        panel.set_items(project, [item])
        assert not panel.crop_button.isVisibleTo(panel)
