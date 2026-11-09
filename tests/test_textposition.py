"""Freie Textposition: Modell und alte Projekte, Text-Rendering (Mitte, Ecken, Balken),
Kreuz-Feld, Eigenschaften-Panel und Standbild."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from slideshow.app.positionpad import PositionPad
from slideshow.app.properties import FREE_POSITION, PRESET_POINTS, ItemPanel
from slideshow.core.project import Item, Project, TextOverlay
from slideshow.render.segment import render_still
from slideshow.render.text import render_text

W, H = 1920, 1080


def bbox(overlay: TextOverlay) -> tuple[int, int, int, int]:
    box = render_text(overlay, W, H).getchannel("A").getbbox()
    assert box is not None
    return box


def center(box: tuple[int, int, int, int]) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


# --- Modell ---------------------------------------------------------------------------


def test_free_position_roundtrip_and_old_projects(tmp_path: Path) -> None:
    p = Project(items=[Item("photo", path="a.jpg", text=TextOverlay("A", x=0.2, y=0.9)),
                       Item("photo", path="b.jpg", text=TextOverlay("B"))], base_dir=tmp_path)
    target = tmp_path / "p.json"
    p.save(target)
    a, b = (it.text for it in Project.load(target).items)
    assert a is not None and (a.x, a.y, a.free) == (0.2, 0.9, True)
    assert b is not None and (b.x, b.y, b.free) == (None, None, False)

    data = json.loads(target.read_text("utf-8"))
    for raw in data["items"]:
        raw["text"].pop("x")
        raw["text"].pop("y")
    target.write_text(json.dumps(data), "utf-8")
    assert [it.text.free for it in Project.load(target).items] == [False, False]  # type: ignore[union-attr]


@pytest.mark.parametrize("kw", [{"x": 0.5}, {"y": 0.5}, {"x": -0.1, "y": 0.5},
                                {"x": 0.5, "y": 1.1}])
def test_invalid_free_position_rejected(kw: dict) -> None:
    with pytest.raises(ValueError, match="Textposition"):
        TextOverlay("x", **kw).validate()


# --- Rendering ------------------------------------------------------------------------


def test_free_text_sits_on_the_point() -> None:
    for x, y in ((0.3, 0.4), (0.5, 0.5), (0.7, 0.6)):
        box = bbox(TextOverlay("Grüße vom See", bar=False, x=x, y=y))
        cx, cy = center(box)
        assert abs(cx - x * W) < 30, (x, y)
        # Textblock inkl. Zeilenabstand: die Schriftmitte weicht etwas von der Blockmitte ab
        assert abs(cy - y * H) < 45, (x, y)


@pytest.mark.parametrize("x,y", [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)])
def test_corners_stay_inside_the_frame(x: float, y: float) -> None:
    for bar in (False, True):
        box = bbox(TextOverlay("Ecke", bar=bar, x=x, y=y))
        assert box[0] >= 0 and box[1] >= 0 and box[2] <= W and box[3] <= H
    # Wirklich in der gewählten Ecke
    cx, cy = center(bbox(TextOverlay("Ecke", bar=True, x=x, y=y)))
    assert (cx < W / 2) == (x == 0.0) and (cy < H / 2) == (y == 0.0)


def test_free_bar_wraps_only_the_text() -> None:
    free = bbox(TextOverlay("Kurz", bar=True, x=0.5, y=0.5))
    assert free[2] - free[0] < W / 2  # Balken nur um den Text, nicht über die ganze Breite
    preset = bbox(TextOverlay("Kurz", bar=True, position="center"))
    assert preset[0] == 0 and preset[2] == W  # Voreinstellung wie bisher


def test_presets_unchanged_without_free_position() -> None:
    a = render_text(TextOverlay("Grüße", position="bottom"), W, H)
    b = render_text(TextOverlay("Grüße", position="bottom", x=None, y=None), W, H)
    assert a.tobytes() == b.tobytes()


def test_long_free_text_wraps_and_stays_inside() -> None:
    text = "Ein sehr langer Satz über den Wörthersee " * 6
    box = bbox(TextOverlay(text, bar=True, x=0.9, y=0.9))
    assert box[0] >= 0 and box[2] <= W and box[3] <= H


@pytest.mark.slow
def test_still_shows_text_in_the_chosen_corner(synthetic: Path, tmp_path: Path) -> None:
    src = tmp_path / "Querformat Wörthersee.jpg"
    shutil.copy2(synthetic / src.name, src)
    plain = Item("photo", path=str(src), fit="crop")
    free = Item("photo", path=str(src), fit="crop",
                text=TextOverlay("Oben rechts", size=80, x=1.0, y=0.0, fade=0.0))
    cache = tmp_path / "cache"
    a = render_still(Project(items=[plain], base_dir=tmp_path), plain, 640, 360,
                     tmp_path / "a.png", cache)
    b = render_still(Project(items=[free], base_dir=tmp_path), free, 640, 360,
                     tmp_path / "b.png", cache)
    from PIL import ImageChops, ImageStat
    with Image.open(a) as ia, Image.open(b) as ib:
        d = ImageChops.difference(ia.convert("RGB"), ib.convert("RGB"))
        top_right = ImageStat.Stat(d.crop((320, 0, 640, 120))).mean
        rest = ImageStat.Stat(d.crop((0, 180, 640, 360))).mean
    assert sum(top_right) / 3 > 5 and sum(rest) / 3 < 1


# --- Kreuz-Feld -----------------------------------------------------------------------


@pytest.fixture
def pad(qtbot) -> PositionPad:  # noqa: ANN001
    w = PositionPad()
    qtbot.addWidget(w)
    w.resize(320, 180)
    w.show()
    return w


def test_pad_click_and_drag_set_point(pad: PositionPad) -> None:
    got: list[tuple[float, float]] = []
    pad.moved.connect(lambda x, y: got.append((round(x, 2), round(y, 2))))
    f = pad._frame()
    QTest.mouseClick(pad, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(round(f.x() + f.width() * 0.25), round(f.y() + f.height() * 0.75)))
    assert got[-1] == (0.25, 0.75) and pad.free

    QTest.mousePress(pad, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(160, 90))
    QTest.mouseMove(pad, QPoint(-500, 5000))  # weit außerhalb: im Feld festgehalten
    QTest.mouseRelease(pad, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       QPoint(-500, 5000))
    assert got[-1] == (0.0, 1.0)


def test_pad_arrow_keys_and_set_point_is_silent(pad: PositionPad) -> None:
    got: list[tuple[float, float]] = []
    pad.moved.connect(lambda x, y: got.append((x, y)))
    pad.set_point(0.5, 0.5, free=False)
    assert got == [] and not pad.free

    QTest.keyClick(pad, Qt.Key.Key_Right)
    assert got[-1] == pytest.approx((0.51, 0.5)) and pad.free
    QTest.keyClick(pad, Qt.Key.Key_Up, Qt.KeyboardModifier.ShiftModifier)
    assert got[-1] == pytest.approx((0.51, 0.45))
    pad.set_point(1.0, 0.0, free=True)
    QTest.keyClick(pad, Qt.Key.Key_Right)  # am Rand: bleibt im Feld
    assert got[-1] == pytest.approx((1.0, 0.0))


# --- Eigenschaften --------------------------------------------------------------------


def _panel(qtbot, tmp_path: Path, items: list[Item]) -> tuple[ItemPanel, Project]:  # noqa: ANN001
    project = Project(items=items, base_dir=tmp_path)
    panel = ItemPanel()
    qtbot.addWidget(panel)
    panel.resize(360, 900)
    panel.show()
    return panel, project


def test_panel_drag_makes_text_free_and_preset_resets(qtbot, tmp_path: Path) -> None:  # noqa: ANN001
    item = Item("photo", path="a.jpg", text=TextOverlay("Hallo", position="bottom"))
    panel, project = _panel(qtbot, tmp_path, [item])
    changes: list[int] = []
    panel.changed.connect(lambda: changes.append(1))
    panel.set_items(project, [item])
    assert panel.position.currentData() == "bottom" and not panel.pad.free
    assert (panel.pad.point) == PRESET_POINTS["bottom"]

    panel.pad.moved.emit(0.2, 0.3)
    assert (item.text.x, item.text.y) == (0.2, 0.3)  # type: ignore[union-attr]
    assert panel.position.currentData() == FREE_POSITION and panel.pad.free and changes

    # Voreinstellung wählen: frei ist aus, Voreinstellung gilt
    panel.position.setCurrentIndex(panel.position.findData("top"))
    assert item.text.position == "top" and not item.text.free  # type: ignore[union-attr]
    assert not panel.pad.free and panel.pad.point == PRESET_POINTS["top"]


def test_panel_free_choice_starts_where_the_text_sits(qtbot, tmp_path: Path) -> None:  # noqa: ANN001
    item = Item("photo", path="a.jpg", text=TextOverlay("Hallo", position="center"))
    panel, project = _panel(qtbot, tmp_path, [item])
    panel.set_items(project, [item])
    panel.position.setCurrentIndex(panel.position.findData(FREE_POSITION))
    assert (item.text.x, item.text.y) == PRESET_POINTS["center"]  # type: ignore[union-attr]


def test_panel_loads_free_position_and_applies_to_all_selected(qtbot, tmp_path: Path) -> None:  # noqa: ANN001
    a = Item("photo", path="a.jpg", text=TextOverlay("A", x=0.1, y=0.2))
    b = Item("photo", path="b.jpg")  # noch ohne Text
    panel, project = _panel(qtbot, tmp_path, [a, b])
    panel.set_items(project, [a])
    assert panel.position.currentData() == FREE_POSITION
    assert panel.pad.point == (0.1, 0.2) and panel.pad.free

    panel.set_items(project, [a, b])
    panel.pad.moved.emit(0.9, 0.8)
    assert [(it.text.x, it.text.y) for it in (a, b) if it.text] == [(0.9, 0.8)] * 2
