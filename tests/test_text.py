from __future__ import annotations

from PIL import Image, ImageDraw

from slideshow.core.project import TextOverlay
from slideshow.render.text import DEFAULT_FONT, font_file, load_font, render_text


def test_bundled_font_present() -> None:
    assert DEFAULT_FONT.is_file()
    assert (DEFAULT_FONT.parent / "OFL.txt").is_file()
    assert font_file("Noto Sans") == DEFAULT_FONT
    assert font_file("gibt es nicht") == DEFAULT_FONT


def test_umlauts_have_real_glyphs() -> None:
    font = load_font("Noto Sans", 64)

    def glyph(ch: str) -> bytes:
        img = Image.new("L", (100, 100))
        ImageDraw.Draw(img).text((10, 0), ch, font=font, fill=255)
        assert img.getbbox() is not None, ch
        return img.tobytes()

    notdef = glyph(chr(0xE000))  # privates Zeichen -> Ersatzkästchen
    for ch in "ÄÖÜäöüß€–":
        assert glyph(ch) != notdef, f"{ch} fehlt in der Schrift"


def test_bold_is_wider() -> None:
    regular = load_font("Noto Sans", 64).getlength("Größe")
    bold = load_font("Noto Sans", 64, bold=True).getlength("Größe")
    assert bold > regular


def _bbox(position: str, bar: bool = False) -> tuple[int, int, int, int]:
    img = render_text(TextOverlay("Grüße aus Kärnten", position=position, bar=bar), 1920, 1080)
    assert img.size == (1920, 1080)
    assert img.mode == "RGBA"
    bbox = img.getchannel("A").getbbox()
    assert bbox is not None
    return bbox


def test_positions() -> None:
    top, center, bottom = _bbox("top"), _bbox("center"), _bbox("bottom")
    assert top[1] < center[1] < bottom[1]
    assert bottom[3] <= 1080
    # zentriert in der Waagrechten
    assert abs((center[0] + center[2]) / 2 - 960) < 20


def test_bar_spans_width_and_is_translucent() -> None:
    bbox = _bbox("bottom", bar=True)
    assert bbox[0] == 0 and bbox[2] == 1920
    img = render_text(TextOverlay("x", bar=True, bar_opacity=0.5), 1920, 1080)
    alpha = img.getchannel("A").getpixel((5, bbox[1] + 3))
    assert 100 < alpha < 160


def test_long_text_wraps() -> None:
    text = "Ein sehr langer Satz über den Wörthersee " * 6
    img = render_text(TextOverlay(text, bar=False), 1920, 1080)
    bbox = img.getchannel("A").getbbox()
    assert bbox is not None
    assert bbox[2] - bbox[0] <= 1920 * 0.9
    assert bbox[3] - bbox[1] > 100  # mehrere Zeilen


def test_scales_with_resolution() -> None:
    small = render_text(TextOverlay("Größe", bar=False), 854, 480).getchannel("A").getbbox()
    big = render_text(TextOverlay("Größe", bar=False), 1920, 1080).getchannel("A").getbbox()
    assert small is not None and big is not None
    assert (big[2] - big[0]) > 2 * (small[2] - small[0])


def test_empty_text_is_transparent() -> None:
    img = render_text(TextOverlay("  "), 640, 360)
    assert img.getchannel("A").getbbox() is None
