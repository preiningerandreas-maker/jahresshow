from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from slideshow.core.chapters import (
    MONTHS,
    apply_chapters,
    build_items,
    chapter_title,
    is_chapter_title,
)
from slideshow.core.importer import MediaFile, import_media
from slideshow.core.project import Item, Project, TextOverlay


def _media(name: str, taken: datetime, kind: str = "photo") -> MediaFile:
    return MediaFile(Path("C:/Fotos Ü") / name, kind, taken, "exif", 100, 100, 1)


def _summary(items: list[Item]) -> list[str]:
    return [
        f"[{it.text.text}]" if is_chapter_title(it) and it.text else Path(it.path or "").name
        for it in items
    ]


# --- (6) Kapitel pro Monat mit korrekten Zwischentiteln ---------------------------------


def test_month_titles_german() -> None:
    assert [chapter_title(f"2026-{m:02d}") for m in range(1, 13)] == [
        f"{name} 2026" for name in MONTHS]
    assert chapter_title("2026-03") == "März 2026"
    assert chapter_title("2025-12") == "Dezember 2025"


def test_chapters_per_month() -> None:
    media = [
        _media("silvester.jpg", datetime(2025, 12, 31, 23, 0)),
        _media("neujahr.jpg", datetime(2026, 1, 1, 0, 10)),
        _media("schnee.mp4", datetime(2026, 1, 20), "video"),
        _media("frühling.jpg", datetime(2026, 3, 2)),
        _media("märz.jpg", datetime(2026, 3, 30)),
        _media("sommer.jpg", datetime(2026, 7, 14)),
    ]
    items = build_items(media)
    assert _summary(items) == [
        "[Dezember 2025]", "silvester.jpg",
        "[Januar 2026]", "neujahr.jpg", "schnee.mp4",
        "[März 2026]", "frühling.jpg", "märz.jpg",
        "[Juli 2026]", "sommer.jpg",
    ]
    titles = [it for it in items if is_chapter_title(it)]
    assert [t.chapter for t in titles] == ["2025-12", "2026-01", "2026-03", "2026-07"]
    for t in titles:
        assert t.kind == "title" and t.path is None
        assert t.text is not None and t.text.position == "center"
        t.validate()
    assert [it.chapter for it in items if not is_chapter_title(it)] == [
        "2025-12", "2026-01", "2026-01", "2026-03", "2026-03", "2026-07"]
    assert items[2].text is not None and items[2].text.text == "Januar 2026"


def test_chapters_can_be_disabled() -> None:
    media = [_media("a.jpg", datetime(2026, 1, 1)), _media("b.jpg", datetime(2026, 2, 1))]
    items = build_items(media, chapters=False)
    assert _summary(items) == ["a.jpg", "b.jpg"]
    assert [it.chapter for it in items] == ["2026-01", "2026-02"]
    # wieder einschalten
    assert _summary(apply_chapters(items, True)) == [
        "[Januar 2026]", "a.jpg", "[Februar 2026]", "b.jpg"]


def test_rebuild_after_reorder_keeps_custom_titles_and_title_card() -> None:
    media = [
        _media("a.jpg", datetime(2026, 1, 5)),
        _media("b.jpg", datetime(2026, 2, 5)),
        _media("c.jpg", datetime(2026, 2, 6)),
        _media("d.jpg", datetime(2026, 4, 1)),
    ]
    card = Item("title", text=TextOverlay("Unser Jahr 2026", position="center"))
    items = [card, *build_items(media)]
    feb = next(it for it in items if it.chapter == "2026-02" and is_chapter_title(it))
    assert feb.text is not None
    feb.text.text = "Februar – Fasching in Villach"

    # Nutzer zieht d.jpg (April) vor die Februar-Fotos und entfernt a.jpg (Jänner)
    card_, jan, a, feb_, b, c, apr, d = items
    assert (card_, feb_) == (card, feb) and a.path and d.path
    items = apply_chapters([card, jan, d, feb, b, c, apr])
    assert items[0] is card
    assert _summary(items[1:]) == [
        "[April 2026]", "d.jpg", "[Februar – Fasching in Villach]", "b.jpg", "c.jpg"]
    assert items[3] is feb
    assert all(it.chapter is None or it.chapter != "2026-01" for it in items)


def test_undated_items_join_previous_chapter() -> None:
    items = build_items([_media("a.jpg", datetime(2026, 5, 1))])
    items.append(Item("photo", path="ohne Datum.jpg"))
    items = apply_chapters(items)
    assert _summary(items) == ["[Mai 2026]", "a.jpg", "ohne Datum.jpg"]
    assert items[-1].chapter == "2026-05"


def test_chapters_from_import_with_umlauts(synthetic: Path, umlaut_dir: Path) -> None:
    for n in ["Querformat Wörthersee.jpg", "Hochkant Größe.jpg", "Gedreht EXIF.jpg",
              "Winter Schnee.heic"]:
        shutil.copy2(synthetic / n, umlaut_dir / n)
    items = build_items(import_media([umlaut_dir]).media)
    assert _summary(items) == [
        "[Januar 2026]", "Winter Schnee.heic",
        "[März 2026]", "Gedreht EXIF.jpg",
        "[Juli 2026]", "Querformat Wörthersee.jpg",
        "[August 2026]", "Hochkant Größe.jpg",
    ]
    # Zwischentitel überstehen Speichern/Laden
    p = Project(items=items, base_dir=umlaut_dir)
    p.save(umlaut_dir / "Kapitel Ä.json")
    loaded = Project.load(umlaut_dir / "Kapitel Ä.json")
    assert _summary(loaded.items) == _summary(items)
    assert [it.chapter for it in loaded.items] == [it.chapter for it in items]


def test_skipped_month_gets_no_title_and_survives_save(tmp_path: Path) -> None:
    items = [Item("photo", path="a.jpg", taken="2022-09-03T10:00:00"),
             Item("photo", path="b.jpg", taken="2022-10-03T10:00:00")]
    out = apply_chapters(items, True, skipped=["2022-09"])
    assert [it.chapter for it in out if is_chapter_title(it)] == ["2022-10"]
    # auch ein schon vorhandener Titel des Monats fällt weg
    again = apply_chapters(apply_chapters(items, True) + [], True, skipped=["2022-09"])
    assert [it.chapter for it in again if is_chapter_title(it)] == ["2022-10"]
    project = Project(items=out)
    project.settings.skipped_chapters = ["2022-09"]
    path = tmp_path / "p.json"
    project.save(path)
    assert Project.load(path).settings.skipped_chapters == ["2022-09"]


def test_old_project_without_skipped_chapters_loads(tmp_path: Path) -> None:
    path = tmp_path / "alt.json"
    path.write_text('{"schema_version": 1, "name": "x", "items": [], "settings": {}}',
                    encoding="utf-8")
    assert Project.load(path).settings.skipped_chapters == []
