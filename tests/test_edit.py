"""Bearbeiten der Zeitleiste ohne Oberfläche, Duplikate gegen das Projekt."""

from __future__ import annotations

import shutil
from pathlib import Path

from slideshow.core.chapters import apply_chapters, is_chapter_title
from slideshow.core.edit import merge_by_date, move_items, sort_by_date
from slideshow.core.importer import find_existing_duplicates, import_media
from slideshow.core.project import Item


def photo(name: str, taken: str | None) -> Item:
    return Item("photo", path=name, taken=taken)


def names(items: list[Item]) -> list[str]:
    return [it.path or (it.text.text if it.text else it.kind) for it in items]


def test_move_items_forward_backward_and_end() -> None:
    items = [photo(n, None) for n in "abcdef"]
    assert names(move_items(items, [0], 3)) == list("bcadef")
    assert names(move_items(items, [4], 1)) == list("aebcdf")
    assert names(move_items(items, [1, 3], 6)) == list("acefbd")  # ans Ende, Reihenfolge bleibt
    assert names(move_items(items, [1, 3], 0)) == list("bdacef")
    assert names(move_items(items, [2], 2)) == list("abcdef")  # auf sich selbst
    assert names(move_items(items, [], 2)) == list("abcdef")


def test_merge_by_date_keeps_manual_order() -> None:
    title = Item("title")
    existing = [title, photo("jun", "2026-06-01T10:00:00"), photo("mar", "2026-03-01T10:00:00"),
                photo("aug", "2026-08-01T10:00:00")]  # Nutzer hat März hinter Juni gezogen
    new = [photo("jan", "2026-01-05T10:00:00"), photo("jul", "2026-07-01T10:00:00"),
           photo("dez", "2026-12-01T10:00:00"), photo("x", None)]
    out = merge_by_date(existing, new)
    assert out[0] is title
    assert names(out) == ["title", "jan", "jun", "mar", "jul", "aug", "dez", "x"]


def test_sort_by_date_keeps_title_card_and_drops_chapter_titles() -> None:
    items = apply_chapters([Item("title"), photo("b", "2026-05-02T00:00:00"),
                            photo("a", "2026-03-01T00:00:00"), photo("c", None)])
    assert any(is_chapter_title(it) for it in items)
    out = apply_chapters(sort_by_date(items))
    media = [it.path for it in out if it.kind == "photo"]
    assert media == ["a", "b", "c"]
    assert out[0].kind == "title" and not is_chapter_title(out[0])
    assert [it.chapter for it in out if is_chapter_title(it)] == ["2026-03", "2026-05"]


def test_existing_duplicates_and_already_in_project(synthetic: Path, umlaut_dir: Path) -> None:
    a = umlaut_dir / "Querformat Wörthersee.jpg"
    shutil.copy2(synthetic / a.name, a)
    copy = umlaut_dir / "Kopie Wörthersee.jpg"
    shutil.copy2(a, copy)
    other = umlaut_dir / "Hochkant Größe.jpg"
    shutil.copy2(synthetic / other.name, other)
    res = import_media([a, copy, other])
    dups, already = find_existing_duplicates(res.media, [a])
    assert already == [a]  # dieselbe Datei
    assert dups == [(copy, a)]  # gleicher Inhalt unter anderem Namen
    assert next(m for m in res.media if m.path == copy).duplicate_of == a
