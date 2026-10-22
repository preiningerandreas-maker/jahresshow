"""Erzeugt synthetische Testmedien in tests/media/synthetic/ per FFmpeg (und Pillow für EXIF).

Aufruf: python tests/make_media.py [--force]
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from slideshow.core.ffmpeg import run_ffmpeg  # noqa: E402
from slideshow.render.text import load_font  # noqa: E402

SYNTHETIC = ROOT / "tests" / "media" / "synthetic"

EXIF_ORIENTATION = 0x0112
EXIF_IFD = 0x8769
EXIF_DATETIME_ORIGINAL = 0x9003


def _photo(out: Path, size: tuple[int, int], label: str, date: str, orientation: int = 1) -> None:
    """Testbild von FFmpeg, Beschriftung und EXIF-Datum per Pillow."""
    png = out.with_suffix(".tmp.png")
    run_ffmpeg(["-f", "lavfi", "-i", f"testsrc2=size={size[0]}x{size[1]}:rate=1",
                "-frames:v", "1", str(png)])
    with Image.open(png) as im:
        img = im.convert("RGB")
    png.unlink()
    draw = ImageDraw.Draw(img)
    font = load_font("Noto Sans", max(24, size[1] // 14), bold=True)
    draw.text((size[0] // 20, size[1] // 20), label, font=font, fill=(255, 255, 255),
              stroke_width=4, stroke_fill=(0, 0, 0))
    exif = Image.Exif()
    exif[EXIF_ORIENTATION] = orientation
    exif.get_ifd(EXIF_IFD)[EXIF_DATETIME_ORIGINAL] = date
    if out.suffix.lower() == ".heic":
        from pillow_heif import register_heif_opener

        register_heif_opener()
        img.save(out, "HEIF", quality=60, exif=exif.tobytes())
    else:
        img.save(out, "JPEG", quality=82, exif=exif.tobytes())


def _video(out: Path, size: tuple[int, int], seconds: float, *, audio: bool = True,
           vfr: bool = False, freq: int = 440) -> None:
    w, h = size
    args = ["-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate={60 if vfr else 30}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000"]
    vf = "format=yuv420p"
    if vfr:
        # Unregelmäßige Bildabstände wie bei Handyvideos
        vf = "select='not(mod(n\\,3))+not(mod(n\\,7))',format=yuv420p"
        args += ["-fps_mode", "vfr"]
    args += ["-t", str(seconds), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "30", "-g", "30"]
    if audio:
        args += ["-ac", "2", "-c:a", "aac", "-b:a", "96k"]
    args += ["-movflags", "+faststart", str(out)]
    run_ffmpeg(args)


def _music(out: Path, seconds: float, base: int) -> None:
    # Einfache Tonfolge: alle 0,5 s ein neuer Halbton
    expr = (f"0.25*sin(2*PI*t*{base}*pow(2\\,floor(mod(t*2\\,8))/12))"
            f"+0.1*sin(2*PI*t*{base // 2})")
    args = ["-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:c=stereo", "-t", str(seconds)]
    if out.suffix == ".mp3":
        args += ["-c:a", "libmp3lame", "-b:a", "128k"]
    else:
        args += ["-c:a", "aac", "-b:a", "128k"]
    run_ffmpeg([*args, str(out)])


MEDIA: dict[str, Callable[[Path], None]] = {
    "Querformat Wörthersee.jpg": lambda p: _photo(
        p, (3000, 2000), "Querformat", "2026:07:14 10:30:00"),
    "Hochkant Größe.jpg": lambda p: _photo(
        p, (1500, 2000), "Hochkant", "2026:08:02 16:45:00"),
    "Gedreht EXIF.jpg": lambda p: _photo(
        p, (2000, 1500), "EXIF 6", "2026:03:21 09:00:00", orientation=6),
    "Winter Schnee.heic": lambda p: _photo(
        p, (1600, 1200), "HEIC", "2026:01:05 12:00:00"),
    "Video mit Ton.mp4": lambda p: _video(p, (1920, 1080), 6),
    "Hochkant Video.mp4": lambda p: _video(p, (1080, 1920), 4, freq=660),
    "VFR Handy.mp4": lambda p: _video(p, (1280, 720), 5, vfr=True, freq=550),
    "Musik 1 – Frühling.mp3": lambda p: _music(p, 12, 330),
    "Musik 2 – Sommer.m4a": lambda p: _music(p, 9, 392),
}


def ensure_media(force: bool = False) -> Path:
    """Erzeugt fehlende Testdateien und gibt den Ordner zurück."""
    SYNTHETIC.mkdir(parents=True, exist_ok=True)
    for name, make in MEDIA.items():
        out = SYNTHETIC / name
        if force or not out.is_file():
            make(out)
    return SYNTHETIC


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Alle Dateien neu erzeugen")
    folder = ensure_media(parser.parse_args().force)
    for f in sorted(folder.iterdir()):
        print(f"{f.stat().st_size / 1024:8.0f} KB  {f.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
