"""Foto drehen in 90-Grad-Schritten: Modell, Bild und mitgedrehter Ausschnitt, Render,
Eigenschaften-Panel."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from slideshow.app.cropdialog import CropDialog
from slideshow.app.properties import ItemPanel
from slideshow.core.photoedit import rotate_crop, rotate_image
from slideshow.core.project import Item, Project
from slideshow.render.segment import _item_settings, prepare_photo, render_still

RED, GREEN, BLUE, WHITE = (220, 30, 30), (30, 200, 30), (30, 30, 220), (240, 240, 240)


def corner_photo(path: Path) -> Path:
    """400×200: oben links rot, oben rechts grün, unten links blau, unten rechts weiß."""
    img = Image.new("RGB", (400, 200), WHITE)
    img.paste(Image.new("RGB", (200, 100), RED), (0, 0))
    img.paste(Image.new("RGB", (200, 100), GREEN), (200, 0))
    img.paste(Image.new("RGB", (200, 100), BLUE), (0, 100))
    img.save(path, "PNG")
    return path


def near(px: tuple[int, ...], want: tuple[int, int, int]) -> bool:
    return all(abs(a - b) < 40 for a, b in zip(px[:3], want, strict=True))


# --- Modell ---------------------------------------------------------------------------


def test_rotation_roundtrip_and_old_projects(tmp_path: Path) -> None:
    p = Project(items=[Item("photo", path="a.jpg", rotation=270), Item("photo", path="b.jpg")],
                base_dir=tmp_path)
    target = tmp_path / "p.json"
    p.save(target)
    assert [it.rotation for it in Project.load(target).items] == [270, 0]

    data = json.loads(target.read_text("utf-8"))
    for raw in data["items"]:
        raw.pop("rotation", None)
    target.write_text(json.dumps(data), "utf-8")
    assert [it.rotation for it in Project.load(target).items] == [0, 0]


@pytest.mark.parametrize("deg", [45, -90, 360, 91])
def test_invalid_rotation_rejected(deg: int) -> None:
    with pytest.raises(ValueError, match="Drehung"):
        Item("photo", path="a.jpg", rotation=deg).validate()


def test_rotation_not_in_key_when_zero() -> None:
    assert "rotation" not in _item_settings(Item("photo", path="a.jpg"))
    assert _item_settings(Item("photo", path="a.jpg", rotation=90))["rotation"] == 90


# --- Bild und Ausschnitt --------------------------------------------------------------


def test_rotate_image_clockwise(tmp_path: Path) -> None:
    img = Image.open(corner_photo(tmp_path / "c.png")).convert("RGB")
    cw = rotate_image(img, 90)
    assert cw.size == (200, 400)
    # Im Uhrzeigersinn: oben links wandert nach oben rechts, unten links nach oben links
    assert near(cw.getpixel((150, 50)), RED)
    assert near(cw.getpixel((50, 50)), BLUE)
    assert near(cw.getpixel((150, 350)), GREEN)
    half = rotate_image(img, 180)
    assert half.size == (400, 200) and near(half.getpixel((350, 150)), RED)
    ccw = rotate_image(img, 270)
    assert near(ccw.getpixel((50, 350)), RED) and near(ccw.getpixel((150, 350)), BLUE)
    assert rotate_image(img, 0) is img


def test_rotate_crop_follows_the_image() -> None:
    crop = [0.0, 0.0, 0.5, 0.5]  # oben links
    assert rotate_crop(crop, 90) == pytest.approx([0.5, 0.0, 0.5, 0.5])  # oben rechts
    assert rotate_crop(crop, 180) == pytest.approx([0.5, 0.5, 0.5, 0.5])
    assert rotate_crop(crop, 270) == pytest.approx([0.0, 0.5, 0.5, 0.5])
    assert rotate_crop(crop, -90) == pytest.approx([0.0, 0.5, 0.5, 0.5])
    # Nicht quadratischer Ausschnitt: Breite und Höhe tauschen
    assert rotate_crop([0.1, 0.2, 0.3, 0.6], 90) == pytest.approx([0.2, 0.1, 0.6, 0.3])
    # Vier Vierteldrehungen = Ausgangslage
    c = [0.1, 0.2, 0.3, 0.6]
    for _ in range(4):
        c = rotate_crop(c, 90)
    assert c == pytest.approx([0.1, 0.2, 0.3, 0.6])
    assert rotate_crop(None, 90) is None


def test_rotated_crop_shows_same_image_part(tmp_path: Path) -> None:
    """Ausschnitt „oben links“ (rot) bleibt rot, wenn Bild und Ausschnitt gemeinsam drehen."""
    src = corner_photo(tmp_path / "c.png")
    for deg in (0, 90, 180, 270):
        crop = rotate_crop([0.0, 0.0, 0.5, 0.5], deg)
        out = prepare_photo(src, 1920, 1080, tmp_path / f"o{deg}.png", crop=crop, rotation=deg)
        with Image.open(out) as im:
            rgb = im.convert("RGB")
            assert near(rgb.getpixel((rgb.width // 2, rgb.height // 2)), RED), deg


# --- Render ---------------------------------------------------------------------------


@pytest.mark.slow
def test_still_is_rotated(synthetic: Path, tmp_path: Path) -> None:
    src = corner_photo(tmp_path / "c.png")
    item = Item("photo", path=str(src), fit="black", rotation=90)
    project = Project(items=[item], base_dir=tmp_path)
    still = render_still(project, item, 640, 360, tmp_path / "s.png", tmp_path / "cache")
    with Image.open(still) as im:
        rgb = im.convert("RGB")
        # Hochkant im Querformat-Rahmen: links und rechts schwarze Ränder, rot oben rechts
        assert near(rgb.getpixel((5, 180)), (0, 0, 0))
        assert near(rgb.getpixel((380, 20)), RED)
        assert near(rgb.getpixel((260, 20)), BLUE)


# --- Oberfläche -----------------------------------------------------------------------


def _panel(qtbot, tmp_path: Path, n: int = 1) -> tuple[ItemPanel, Project]:  # noqa: ANN001
    src = corner_photo(tmp_path / "p.png")
    project = Project(items=[Item("photo", path=str(src)) for _ in range(n)],
                      base_dir=tmp_path)
    panel = ItemPanel()
    qtbot.addWidget(panel)
    panel.show()
    return panel, project


def test_panel_rotates_and_keeps_crop_on_same_part(qtbot, tmp_path: Path) -> None:  # noqa: ANN001
    panel, project = _panel(qtbot, tmp_path)
    changes: list[int] = []
    panel.changed.connect(lambda: changes.append(1))
    item = project.items[0]
    item.crop = [0.0, 0.0, 0.5, 0.5]
    panel.set_items(project, [item])
    assert panel.rotate_right.isVisibleTo(panel) and panel.rotate_state.text() == ""

    panel.rotate_right.click()
    assert item.rotation == 90 and item.crop == pytest.approx([0.5, 0.0, 0.5, 0.5])
    assert "90° rechts" in panel.rotate_state.text() and changes

    panel.rotate_left.click()
    panel.rotate_left.click()
    assert item.rotation == 270 and "90° links" in panel.rotate_state.text()
    # Zurück auf die Ausgangslage: Drehung 0 und Ausschnitt wieder oben links
    panel.rotate_right.click()
    assert item.rotation == 0 and item.crop == pytest.approx([0.0, 0.0, 0.5, 0.5])
    assert panel.rotate_state.text() == ""


def test_panel_rotates_all_selected_photos_only(qtbot, tmp_path: Path) -> None:  # noqa: ANN001
    panel, project = _panel(qtbot, tmp_path, n=2)
    title = Item("title")
    project.items.append(title)
    panel.set_items(project, list(project.items))
    panel.rotate_right.click()
    assert [it.rotation for it in project.items] == [90, 90, 0]


def test_panel_hides_rotation_for_video_and_title(qtbot, tmp_path: Path) -> None:  # noqa: ANN001
    project = Project(items=[Item("title"), Item("video", path="v.mp4")], base_dir=tmp_path)
    panel = ItemPanel()
    qtbot.addWidget(panel)
    panel.show()
    for item in project.items:
        panel.set_items(project, [item])
        assert not panel.rotate_right.isVisibleTo(panel)


def test_crop_dialog_gets_rotated_photo(qtbot, tmp_path: Path,  # noqa: ANN001
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    panel, project = _panel(qtbot, tmp_path)
    item = project.items[0]
    item.rotation = 90
    panel.set_items(project, [item])
    seen: list[tuple[int, int]] = []

    def fake_exec(self: CropDialog) -> CropDialog.DialogCode:
        seen.append((self.view.image.width(), self.view.image.height()))
        return CropDialog.DialogCode.Rejected

    monkeypatch.setattr(CropDialog, "exec", fake_exec)
    panel.crop_button.click()
    assert seen == [(200, 400)]  # das gedrehte Foto, damit der Ausschnitt passt
