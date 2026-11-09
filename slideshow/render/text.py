"""Text als transparentes PNG mit Pillow rendern (kein drawtext, keine Font-Pfade in FFmpeg)."""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

from slideshow.core.project import TextOverlay
from slideshow.render.cache import cleanup_tmp, commit_tmp, tmp_path

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
BUNDLED_FONTS = {"noto sans": FONT_DIR / "NotoSans.ttf"}
DEFAULT_FONT = BUNDLED_FONTS["noto sans"]


def font_file(name: str) -> Path:
    """Mitgelieferte Schrift per Name oder eigene Schriftdatei per Pfad."""
    bundled = BUNDLED_FONTS.get(name.strip().lower())
    if bundled is not None:
        return bundled
    path = Path(name)
    return path if path.is_file() else DEFAULT_FONT


@lru_cache(maxsize=8)
def _font_bytes(path: str) -> bytes:
    # Über Python lesen, damit Pfade mit Umlauten unter Windows sicher funktionieren
    return Path(path).read_bytes()


def load_font(name: str, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(io.BytesIO(_font_bytes(str(font_file(name)))), size)
    try:
        axes = font.get_variation_axes()
    except OSError:  # keine variable Schrift
        return font
    values = []
    for axis in axes:
        axis_name = axis["name"]
        if isinstance(axis_name, bytes):
            axis_name = axis_name.decode("ascii", "ignore")
        if axis_name == "Weight":
            values.append(700 if bold else 400)
        else:
            values.append(axis["default"])
    font.set_variation_by_axes(values)
    return font


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        line = words[0]
        for word in words[1:]:
            candidate = f"{line} {word}"
            if font.getlength(candidate) <= max_width:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def render_text(overlay: TextOverlay, width: int, height: int) -> Image.Image:
    """Bildschirmfüllendes RGBA-Bild mit Text (und optionalem Balken), sonst transparent."""
    scale = height / 1080
    size = max(8, round(overlay.size * scale))
    font = load_font(overlay.font, size, overlay.bold)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if not overlay.text.strip():
        return img

    lines = _wrap(overlay.text, font, int(width * 0.88))
    ascent, descent = font.getmetrics()
    line_h = round((ascent + descent) * 1.1)
    block_h = line_h * len(lines)
    pad = round(size * 0.5)
    margin = round(height * 0.06)

    if overlay.free:
        return _render_free(img, overlay, lines, font, size, line_h, pad)

    if overlay.position == "top":
        y0 = margin + pad
    elif overlay.position == "center":
        y0 = (height - block_h) // 2
    else:
        y0 = height - margin - pad - block_h

    draw = ImageDraw.Draw(img)
    if overlay.bar:
        r, g, b = ImageColor.getrgb(overlay.bar_color)[:3]
        alpha = round(255 * overlay.bar_opacity)
        draw.rectangle((0, y0 - pad, width, y0 + block_h + pad), fill=(r, g, b, alpha))

    color = ImageColor.getrgb(overlay.color)[:3] + (255,)
    for i, line in enumerate(lines):
        x = (width - font.getlength(line)) / 2
        y = y0 + i * line_h
        if not overlay.bar:
            # Leichter Schatten für Lesbarkeit auf hellen Fotos
            off = max(1, size // 20)
            draw.text((x + off, y + off), line, font=font, fill=(0, 0, 0, 160))
        draw.text((x, y), line, font=font, fill=color)
    return img


def _clamp_span(start: float, length: float, lo: float, total: float) -> float:
    """Anfang eines Blocks der Länge length so legen, dass er zwischen lo und total - lo bleibt
    (passt er nicht hinein, mittig)."""
    hi = total - lo - length
    if hi < lo:
        return (total - length) / 2
    return min(max(start, lo), hi)


def _render_free(img: Image.Image, overlay: TextOverlay, lines: list[str],
                 font: ImageFont.FreeTypeFont, size: int, line_h: int, pad: int) -> Image.Image:
    """Text an freier Position: (x, y) ist die Mitte des Textblocks. Der Block bleibt samt
    Balken im Bild, auch in den Ecken. Der Balken umschließt nur den Text."""
    assert overlay.x is not None and overlay.y is not None
    width, height = img.size
    block_w = max(font.getlength(line) for line in lines)
    block_h = line_h * len(lines)
    edge = round(height * 0.02) + pad
    left = _clamp_span(overlay.x * width - block_w / 2, block_w, edge, width)
    top = _clamp_span(overlay.y * height - block_h / 2, block_h, edge, height)

    draw = ImageDraw.Draw(img)
    if overlay.bar:
        r, g, b = ImageColor.getrgb(overlay.bar_color)[:3]
        alpha = round(255 * overlay.bar_opacity)
        draw.rectangle((left - pad, top - pad, left + block_w + pad, top + block_h + pad),
                       fill=(r, g, b, alpha))
    color = ImageColor.getrgb(overlay.color)[:3] + (255,)
    for i, line in enumerate(lines):
        x = left + (block_w - font.getlength(line)) / 2
        y = top + i * line_h
        if not overlay.bar:
            off = max(1, size // 20)
            draw.text((x + off, y + off), line, font=font, fill=(0, 0, 0, 160))
        draw.text((x, y), line, font=font, fill=color)
    return img


def save_text_png(overlay: TextOverlay, width: int, height: int, out: Path) -> Path:
    tmp = tmp_path(out)
    try:
        render_text(overlay, width, height).save(tmp, "PNG")
        commit_tmp(tmp, out)
    finally:
        cleanup_tmp(tmp)
    return out
