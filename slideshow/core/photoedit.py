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


def rotate_image(img: Image.Image, degrees: int) -> Image.Image:
    """Im Uhrzeigersinn um 0, 90, 180 oder 270 Grad drehen."""
    steps = (degrees // 90) % 4
    if steps == 0:
        return img
    ops = {1: Image.Transpose.ROTATE_270, 2: Image.Transpose.ROTATE_180,
           3: Image.Transpose.ROTATE_90}  # PIL zählt gegen den Uhrzeigersinn
    return img.transpose(ops[steps])


def rotate_crop(crop: Sequence[float] | None, degrees: int) -> list[float] | None:
    """Ausschnitt [links, oben, Breite, Höhe] mit dem Bild mitdrehen (im Uhrzeigersinn)."""
    if crop is None:
        return None
    x, y, w, h = crop
    for _ in range((degrees // 90) % 4):
        x, y, w, h = 1.0 - y - h, x, h, w
    return [x, y, w, h]


def draft_size(w: int, h: int, crop: Sequence[float] | None) -> tuple[int, int]:
    """Größe für das beschleunigte JPEG-Laden: der Ausschnitt soll danach noch (w, h) füllen,
    das ganze Bild muss also entsprechend größer dekodiert werden."""
    if crop is None:
        return w, h
    # Ob das Bild gedreht wird, ist hier egal: entscheidend ist die längere Seite
    scale = 1 / max(min(crop[2], crop[3]), 0.01)
    return max(w, round(w * scale)), max(h, round(h * scale))
