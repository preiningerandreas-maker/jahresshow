"""Schriftgröße und -farbe als Projektstandard mit Ausnahmen pro Text: Modell, alte Projekte,
Schlüssel der Segmente, Standbild, Oberfläche."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from slideshow.app.properties import ItemPanel, ProjectPanel
from slideshow.core.chapters import make_chapter_title
from slideshow.core.project import Item, Project, Settings, TextOverlay
from slideshow.render.segment import _item_settings, _picture_key_data, render_still

# --- Modell ---------------------------------------------------------------------------


def make(tmp_path: Path, **text_kw: object) -> tuple[Project, Item]:
    item = Item("photo", path="a.jpg", text=TextOverlay("Hallo", **text_kw))  # type: ignore[arg-type]
    return Project(items=[item], base_dir=tmp_path), item


def test_text_follows_project_default_unless_own(tmp_path: Path) -> None:
    project, item = make(tmp_path)
    project.settings.text_size, project.settings.text_color = 80, "#FF0000"
    shown = project.styled(item).text
    assert shown is not None and (shown.size, shown.color) == (80, "#FF0000")
    assert item.text is not None
    assert (item.text.size, item.text.color) == (56, "#FFFFFF")  # Original bleibt

    item.text.size, item.text.own_size = 40, True
    shown = project.styled(item).text
    assert shown is not None and (shown.size, shown.color) == (40, "#FF0000")
    item.text.color, item.text.own_color = "#00FF00", True
    shown = project.styled(item).text
    assert shown is not None and (shown.size, shown.color) == (40, "#00FF00")


def test_styled_without_text_or_change_returns_same_item(tmp_path: Path) -> None:
    plain = Item("photo", path="a.jpg")
    project = Project(items=[plain], base_dir=tmp_path)
    assert project.styled(plain) is plain
    _, item = make(tmp_path)
    assert project.styled(item) is item  # Standard 56 / weiß: nichts zu ändern


def test_roundtrip_and_old_projects(tmp_path: Path) -> None:
    project, _ = make(tmp_path, size=70, own_size=True)
    project.settings.text_size, project.settings.text_color = 90, "#FFCC00"
    project.items.append(Item("photo", path="b.jpg", text=TextOverlay("B", color="#112233")))
    target = tmp_path / "p.json"
    project.save(target)
    back = Project.load(target)
    assert (back.settings.text_size, back.settings.text_color) == (90, "#FFCC00")
    assert [(t.own_size, t.own_color) for t in (it.text for it in back.items) if t] == [
        (True, False), (False, False)]

    # Altes Projekt: weder Projektstandard noch own_*-Felder in der Datei
    data = json.loads(target.read_text("utf-8"))
    for key in ("text_size", "text_color"):
        del data["settings"][key]
    data["items"][0]["text"].update(size=80, color="#ffffff")
    data["items"][1]["text"].update(size=56, color="#112233")
    for raw in data["items"]:
        raw["text"].pop("own_size")
        raw["text"].pop("own_color")
    target.write_text(json.dumps(data), "utf-8")
    old = Project.load(target)
    assert (old.settings.text_size, old.settings.text_color) == (56, "#FFFFFF")
    a, b = (it.text for it in old.items)
    assert a is not None and b is not None
    # Abweichung vom früheren Standard war eine bewusste Wahl und bleibt erhalten
    assert (a.own_size, a.own_color) == (True, False)
    assert (b.own_size, b.own_color) == (False, True)
    shown = old.styled(old.items[0]).text
    assert shown is not None and shown.size == 80  # sieht aus wie vorher


def test_invalid_default_size_rejected() -> None:
    with pytest.raises(ValueError, match="Schriftgröße"):
        Settings(text_size=0).validate()


def test_title_cards_keep_their_own_size(tmp_path: Path) -> None:
    ch = make_chapter_title("2026-03")
    assert ch.text is not None and ch.text.own_size
    project = Project(items=[ch], base_dir=tmp_path)
    project.settings.text_size = 30
    shown = project.styled(ch).text
    assert shown is not None and shown.size == ch.text.size


# --- Schlüssel der Segmente -----------------------------------------------------------


def test_segment_key_data_unchanged_for_default_style(tmp_path: Path) -> None:
    item = Item("title", text=TextOverlay("Hallo"))  # ohne Datei: nur die Einstellungen zählen
    project = Project(items=[item], base_dir=tmp_path)
    cache = tmp_path / "cache"
    text = _item_settings(project.styled(item))["text"]
    assert "own_size" not in text and "own_color" not in text
    assert (text["size"], text["color"]) == (56, "#FFFFFF")
    # Projektstandard geändert: Schlüssel ändert sich, mit eigener Größe nicht (für die Größe)
    before = _picture_key_data(project, item, cache)
    project.settings.text_size = 90
    assert _picture_key_data(project, item, cache) != before
    item.text.size, item.text.own_size = 56, True  # type: ignore[union-attr]
    assert _picture_key_data(project, item, cache) == before


# --- Standbild ------------------------------------------------------------------------


@pytest.mark.slow
def test_still_uses_project_color_and_own_color(synthetic: Path, tmp_path: Path) -> None:
    src = tmp_path / "Querformat Wörthersee.jpg"
    shutil.copy2(synthetic / src.name, src)
    item = Item("photo", path=str(src), fit="crop",
                text=TextOverlay("HALLO", position="center", size=120, own_size=True,
                                 bar=False, fade=0.0))
    project = Project(items=[item], base_dir=tmp_path)
    project.settings.text_color = "#FF0000"
    cache = tmp_path / "cache"

    def reddest(it: Item, out: str) -> int:
        png = render_still(project, it, 640, 360, tmp_path / out, cache)
        with Image.open(png) as im:
            rgb = im.convert("RGB")
            return sum(1 for r, g, b in rgb.get_flattened_data()
                       if r > 230 and g < 60 and b < 60)

    plain = Item("photo", path=str(src), fit="crop")
    base = reddest(plain, "base.png")  # Rot, das schon im Foto steckt
    assert reddest(item, "a.png") - base > 200  # Projektfarbe rot kommt im Text an
    item.text.color, item.text.own_color = "#00FF00", True  # type: ignore[union-attr]
    assert reddest(item, "b.png") - base < 50  # eigene Farbe gewinnt


# --- Oberfläche -----------------------------------------------------------------------


def test_project_panel_sets_defaults(qtbot, tmp_path: Path) -> None:  # noqa: ANN001
    project = Project(base_dir=tmp_path)
    panel = ProjectPanel()
    qtbot.addWidget(panel)
    changes: list[int] = []
    panel.changed.connect(lambda: changes.append(1))
    panel.set_project(project)
    assert panel.text_size.value() == 56 and panel.text_color.color() == "#FFFFFF"
    panel.text_size.setValue(72)
    panel.text_color.colorChanged.emit("#FFCC00")
    assert (project.settings.text_size, project.settings.text_color) == (72, "#FFCC00")
    assert len(changes) == 2


def test_item_panel_marks_own_style_and_resets(qtbot, tmp_path: Path) -> None:  # noqa: ANN001
    project, item = make(tmp_path)
    project.settings.text_size = 90
    panel = ItemPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.set_items(project, [item])
    assert panel.size.value() == 90  # zeigt, was gilt
    assert not panel.use_default_style.isEnabled()
    assert item.text is not None and not item.text.own_size  # Anzeigen ändert nichts

    panel.size.setValue(64)
    assert item.text.own_size and item.text.size == 64 and not item.text.own_color
    assert panel.use_default_style.isEnabled()
    panel.color.colorChanged.emit("#336699")
    assert item.text.own_color and item.text.color == "#336699"

    panel.use_default_style.click()
    assert not item.text.own_size and not item.text.own_color
    assert panel.size.value() == 90 and panel.color.color() == "#FFFFFF"
