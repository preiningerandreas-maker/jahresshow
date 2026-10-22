"""Bearbeiten der Zeitleiste ohne Oberfläche: verschieben, einsortieren, nach Datum ordnen.
Alle Funktionen liefern neue Listen; Kapitel werden danach mit apply_chapters neu gesetzt."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from slideshow.core.chapters import is_chapter_title
from slideshow.core.project import Item


def move_items(items: Sequence[Item], rows: Iterable[int], target: int) -> list[Item]:
    """Elemente an den Zeilen `rows` vor die Zeile `target` verschieben (Drag & Drop).
    target = len(items) hängt sie ans Ende. Die Reihenfolge der verschobenen bleibt erhalten."""
    picked = sorted({r for r in rows if 0 <= r < len(items)})
    if not picked:
        return list(items)
    moving = [items[r] for r in picked]
    # Zielposition nach dem Herausnehmen: um die davor liegenden verschobenen Zeilen kleiner
    target = max(0, min(target, len(items)))
    target -= sum(1 for r in picked if r < target)
    rest = [it for i, it in enumerate(items) if i not in set(picked)]
    return rest[:target] + moving + rest[target:]


def _is_media(item: Item) -> bool:
    return item.kind != "title"


def merge_by_date(existing: Sequence[Item], new: Sequence[Item]) -> list[Item]:
    """Neu importierte Elemente (bereits nach Datum sortiert) passend zum Aufnahmedatum in die
    vorhandene Reihenfolge einfügen. Die vorhandene Reihenfolge bleibt unangetastet, auch wenn
    der Nutzer sie per Drag & Drop geändert hat. Elemente ohne Datum kommen ans Ende."""
    out = list(existing)
    start = 0
    for item in new:
        pos = len(out)
        if item.taken:
            for i in range(start, len(out)):
                ex = out[i]
                if _is_media(ex) and ex.taken and ex.taken > item.taken:
                    pos = i
                    break
        out.insert(pos, item)
        start = pos + 1
    return out


def sort_by_date(items: Sequence[Item]) -> list[Item]:
    """Fotos und Videos nach Aufnahmedatum ordnen. Titelkarten (ohne Kapitel) bleiben vorn,
    Zwischentitel fallen weg und werden von apply_chapters neu gesetzt."""
    titles = [it for it in items if it.kind == "title" and not is_chapter_title(it)]
    media = [it for it in items if _is_media(it)]
    # Ohne Datum: ans Ende, in bisheriger Reihenfolge (sort ist stabil)
    media.sort(key=lambda it: (it.taken is None, it.taken or ""))
    return titles + media
