from __future__ import annotations

import json
from pathlib import Path

import pytest

from slideshow.core.project import (
    SCHEMA_VERSION,
    Item,
    Music,
    Project,
    Settings,
    TextOverlay,
)

TESTS = Path(__file__).resolve().parent


def _project(base: Path) -> Project:
    return Project(
        name="Jahresrückblick 2026",
        items=[
            Item("title", text=TextOverlay("Grüße aus Kärnten", position="center")),
            Item("photo", path=str(base / "Fotos" / "Größe.jpg"), fit="blur"),
            Item("video", path="Clips/Schöner Tag.mp4", transition="wipeleft"),
        ],
        music=Music(tracks=[str(base / "Musik" / "Lied – Ä.mp3")]),
        base_dir=base,
    )


def test_roundtrip_with_umlaut_paths(umlaut_dir: Path) -> None:
    p = _project(umlaut_dir)
    target = umlaut_dir / "Projekt Ü.json"
    p.save(target)

    raw = json.loads(target.read_text("utf-8"))
    assert raw["schema_version"] == SCHEMA_VERSION
    # Pfade im Projektordner werden relativ gespeichert
    assert raw["items"][1]["path"] == "Fotos/Größe.jpg"
    assert raw["music"]["tracks"] == ["Musik/Lied – Ä.mp3"]

    loaded = Project.load(target)
    assert loaded.name == p.name
    assert loaded.items[0].text == TextOverlay("Grüße aus Kärnten", position="center")
    assert loaded.resolve(loaded.items[1].path or "") == umlaut_dir / "Fotos" / "Größe.jpg"
    assert loaded.items[2].transition == "wipeleft"


def test_newer_schema_rejected(tmp_path: Path) -> None:
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"schema_version": SCHEMA_VERSION + 1, "items": []}), "utf-8")
    with pytest.raises(ValueError, match="neueren"):
        Project.load(f)


def test_missing_schema_rejected(tmp_path: Path) -> None:
    f = tmp_path / "p.json"
    f.write_text("{}", "utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        Project.load(f)


def test_unknown_fields_ignored(tmp_path: Path) -> None:
    data = {"schema_version": 1, "zukunft": 1,
            "items": [{"kind": "title", "neu": True, "text": {"text": "x", "egal": 2}}]}
    p = Project.from_dict(data, tmp_path)
    assert p.items[0].text is not None and p.items[0].text.text == "x"


@pytest.mark.parametrize(
    "item",
    [
        {"kind": "photo"},
        {"kind": "foto", "path": "a.jpg"},
        {"kind": "photo", "path": "a.jpg", "fit": "stretch"},
        {"kind": "photo", "path": "a.jpg", "transition": "spin"},
        {"kind": "photo", "path": "a.jpg", "duration": 0},
        {"kind": "video", "path": "a.mp4", "volume": -0.1},
        {"kind": "video", "path": "a.mp4", "volume": 2.5},
        {"kind": "title", "text": {"text": "x", "position": "left"}},
    ],
)
def test_validation(item: dict, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Element 1"):
        Project.from_dict({"schema_version": 1, "items": [item]}, tmp_path)


def test_timeline_durations_and_transitions(tmp_path: Path) -> None:
    p = Project(
        items=[
            Item("title"),  # 4 s Standard
            Item("photo", path="a.jpg", duration=3.0),
            Item("video", path="v.mp4", start=1.0),  # 7 s Clip ab 1 s -> 6 s
            Item("photo", path="b.jpg", transition="none"),
            Item("photo", path="c.jpg", duration=1.0, transition="fadeblack",
                 transition_duration=2.0),  # Übergang wird begrenzt
        ],
        settings=Settings(photo_duration=4.0, transition="fade", transition_duration=1.0),
        base_dir=tmp_path,
    )
    tl = p.timeline(lambda _path: 7.0)
    assert [e.frames for e in tl] == [120, 90, 180, 120, 30]
    assert [e.transition_frames for e in tl] == [0, 30, 30, 0, 14]
    assert [e.transition for e in tl] == ["none", "fade", "fade", "none", "fadeblack"]
    assert [e.start_frame for e in tl] == [0, 90, 150, 330, 436]
    total = p.total_frames(lambda _path: 7.0)
    assert total == sum(e.frames for e in tl) - sum(e.transition_frames for e in tl)
    assert total == 466


def test_demo_project_content() -> None:
    """demo.json muss die Pflicht-Bestandteile für M1 enthalten."""
    p = Project.load(TESTS / "demo.json")
    kinds = [i.kind for i in p.items]
    assert kinds[0] == "title"
    photos = [i for i in p.items if i.kind == "photo"]
    assert any("Querformat" in (i.path or "") for i in photos)
    assert any("Hochkant" in (i.path or "") and i.fit == "blur" for i in photos)
    assert any(i.kind == "video" and i.audio for i in p.items)
    texts = [i.text.text for i in p.items if i.text]
    assert any(set("äöüÄÖÜß") & set(t) for t in texts)
    assert p.settings.transition == "fade"
    assert len(p.music.tracks) == 2


def test_duck_volume_default_range_and_old_projects(tmp_path) -> None:  # noqa: ANN001
    from slideshow.core.project import DEFAULT_DUCK, MAX_DUCK, Music

    assert DEFAULT_DUCK == 0.2 and MAX_DUCK == 0.5
    assert Music().duck_volume == 0.2
    assert Music(duck_volume=0.8).duck_volume == 0.5  # alter Wert über 50 % wird begrenzt
    assert Music(duck_volume=0.25).duck_volume == 0.25  # alter Standard bleibt
    path = tmp_path / "alt.json"
    path.write_text('{"schema_version": 1, "name": "x", "items": [], '
                    '"music": {"duck_volume": 1.0}}', encoding="utf-8")
    assert Project.load(path).music.duck_volume == 0.5
    path.write_text('{"schema_version": 1, "name": "x", "items": []}', encoding="utf-8")
    assert Project.load(path).music.duck_volume == 0.2
