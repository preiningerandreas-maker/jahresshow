"""Automatische Kapitel pro Monat: vor jedem neuen Monat ein Zwischentitel ("März 2026").

Zwischentitel sind Titel-Elemente mit gesetztem `chapter` und ohne Foto. Die Titelkarte am
Anfang hat kein Kapitel und bleibt beim Neuberechnen unangetastet.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from slideshow.core.importer import MediaFile
from slideshow.core.project import Item, TextOverlay

MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember")

CHAPTER_DURATION = 3.0
CHAPTER_BACKGROUND = "#1E2A3A"
CHAPTER_TEXT_SIZE = 110


def chapter_key(taken: datetime) -> str:
    return f"{taken.year:04d}-{taken.month:02d}"


def chapter_title(key: str) -> str:
    """'2026-03' → 'März 2026'."""
    year, month = key.split("-")
    return f"{MONTHS[int(month) - 1]} {int(year)}"


def is_chapter_title(item: Item) -> bool:
    return item.kind == "title" and item.chapter is not None and not item.path


def make_chapter_title(key: str) -> Item:
    return Item(
        "title",
        duration=CHAPTER_DURATION,
        background=CHAPTER_BACKGROUND,
        text=TextOverlay(chapter_title(key), position="center", size=CHAPTER_TEXT_SIZE,
                         bold=True, bar=False),
        chapter=key,
    )


def media_items(media: Iterable[MediaFile]) -> list[Item]:
    """Zeitleisten-Elemente aus importierten Dateien (Reihenfolge bleibt erhalten)."""
    return [
        Item(m.kind, path=str(m.path.resolve()), taken=m.taken.isoformat(timespec="seconds"),
             chapter=chapter_key(m.taken), damage=m.damage, damage_readable=m.damage_readable,
             audio=m.kind != "video" or m.has_audio)  # Video ohne Tonspur: Ton aus
        for m in media
    ]


def apply_chapters(items: list[Item], enabled: bool = True) -> list[Item]:
    """Zwischentitel neu setzen, passend zur aktuellen Reihenfolge (auch nach Drag & Drop).

    Elemente ohne Datum gehören zum Kapitel davor. Ein vom Nutzer geänderter Zwischentitel
    (Text, Dauer, Farbe) bleibt erhalten, solange es sein Kapitel noch gibt.
    """
    existing = {it.chapter: it for it in items if is_chapter_title(it)}
    body = [it for it in items if not is_chapter_title(it)]

    out: list[Item] = []
    current: str | None = None
    for it in body:
        if it.kind == "title":  # Titelkarte (mit oder ohne Foto)
            out.append(it)
            continue
        key = chapter_key(datetime.fromisoformat(it.taken)) if it.taken else current
        if key is not None and key != current:
            if enabled:
                title = existing.pop(key, None) or make_chapter_title(key)
                out.append(title)
            current = key
        it.chapter = current
        out.append(it)
    return out


def build_items(media: Iterable[MediaFile], chapters: bool = True) -> list[Item]:
    """Importierte Dateien (bereits sortiert) als Elemente, mit Zwischentiteln pro Monat."""
    return apply_chapters(media_items(media), chapters)
