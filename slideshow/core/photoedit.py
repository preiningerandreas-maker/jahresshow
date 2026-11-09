"""Foto bearbeiten ohne Oberfläche: Bildausschnitt in Pixel umrechnen und anwenden."""

from __future__ import annotations

from collections.abc import Sequence

from PIL import Image

MIN_SIDE = 2  # kleinster Ausschnitt in Pixel (yuv420 braucht gerade, positive Maße)


def crop_box(size: tuple[int, int], crop: Sequence[float]) -> tuple[int, int, int, int]:
    """Ausschnitt [links, oben, Breite, Höhe] (Anteile) als Pixelrahmen (l, o, r, u).
    Liegt immer im Bild und ist mindestens MIN_SIDE Pixel groß."""
    width, height = size
    x, y, w, h = crop
    left = min(max(0, round(x * width)), max(0, width - MIN_SIDE))
    top = min(max(0, round(y * height)), max(0, height - MIN_SIDE))
    right = min(width, max(left + MIN_SIDE, round((x + w) * width)))
    bottom = min(height, max(top + MIN_SIDE, round((y + h) * height)))
    return left, top, right, bottom


def apply_crop(img: Image.Image, crop: Sequence[float] | None) -> Image.Image:
    """Ausschnitt anwenden; None oder das ganze Bild liefert das Bild unverändert."""
    if crop is None:
        return img
    box = crop_box(img.size, crop)
    if box == (0, 0, img.width, img.height):
        return img
    return img.crop(box)


def draft_size(w: int, h: int, crop: Sequence[float] | None) -> tuple[int, int]:
    """Größe für das beschleunigte JPEG-Laden: der Ausschnitt soll danach noch (w, h) füllen,
    das ganze Bild muss also entsprechend größer dekodiert werden."""
    if crop is None:
        return w, h
    return (max(w, round(w / max(crop[2], 0.01))), max(h, round(h / max(crop[3], 0.01))))
