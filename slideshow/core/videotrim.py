"""Video kürzen: Start und Ende im Original-Clip. Im Projekt stehen weiter nur Item.start
(Startpunkt im Clip) und Item.duration (Länge ab dem Start, None = bis zum Ende); hier die
Umrechnung auf „von … bis“, damit alte Projekte unverändert laden."""

from __future__ import annotations

from slideshow.core.project import Item

MIN_CLIP = 0.5  # kürzestes Stück in Sekunden
END_SNAP = 0.05  # ein Ende so nah am Clipende gilt als „bis zum Ende“


def trim_end(item: Item, length: float) -> float:
    """Ende des gezeigten Teils im Original-Clip in Sekunden."""
    if item.duration is None:
        return length
    return min(length, item.start + item.duration)


def set_trim(item: Item, length: float, *, start: float | None = None,
             end: float | None = None) -> None:
    """Start und/oder Ende setzen (Sekunden im Clip, end <= 0 = bis zum Ende). Der Teil bleibt
    innerhalb des Clips und mindestens MIN_CLIP lang; das jeweils andere Ende bleibt stehen."""
    if length <= 0:
        return
    cur_end = trim_end(item, length)
    new_start = item.start if start is None else start
    new_end = cur_end if end is None else (length if end <= 0 else end)
    floor = min(MIN_CLIP, length)
    new_start = min(max(new_start, 0.0), max(0.0, length - floor))
    if start is not None and end is None:
        # Start nach hinten geschoben: das Ende bleibt, notfalls Mindestlänge
        new_end = max(new_end, min(length, new_start + floor))
    new_end = min(max(new_end, new_start + floor), length)
    item.start = new_start
    item.duration = None if new_end >= length - END_SNAP else new_end - new_start
