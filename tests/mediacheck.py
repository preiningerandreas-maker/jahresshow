"""Hilfen für Tests: Fotos mit EXIF erzeugen, ffprobe auswerten, Bild/Ton roh dekodieren."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image

from slideshow.core.ffmpeg import CREATIONFLAGS, ffmpeg_path, ffprobe_path, run_ffmpeg

EXIF_IFD = 0x8769
RED = (220, 30, 30)
BLUE = (30, 30, 220)


def make_jpeg(path: Path, *, date: str | None = None, orientation: int = 1,
              size: tuple[int, int] = (200, 100), fmt: str = "JPEG") -> Path:
    """Linke Hälfte rot, rechte Hälfte blau – so lässt sich die Drehung prüfen."""
    w, h = size
    img = Image.new("RGB", size, BLUE)
    img.paste(RED, (0, 0, w // 2, h))
    exif = Image.Exif()
    exif[0x0112] = orientation
    if date:
        exif.get_ifd(EXIF_IFD)[0x9003] = date
    if fmt == "HEIF":
        from pillow_heif import register_heif_opener

        register_heif_opener()
        img.save(path, "HEIF", quality=90, exif=exif.tobytes())
    else:
        img.save(path, fmt, quality=95, exif=exif.tobytes())
    return path


def ffprobe(path: Path, *extra: str) -> dict:
    cmd = [str(ffprobe_path()), "-v", "error", "-print_format", "json", "-show_format",
           "-show_streams", *extra, str(path)]
    res = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=True,
                         creationflags=CREATIONFLAGS)
    return json.loads(res.stdout)


def streams(path: Path) -> tuple[dict, dict | None]:
    data = ffprobe(path)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    return v, a


def video_pts(path: Path) -> list[float]:
    """Zeitstempel aller Videobilder laut ffprobe."""
    data = ffprobe(path, "-select_streams", "v:0", "-show_entries", "packet=pts_time")
    return sorted(float(p["pts_time"]) for p in data["packets"])


def _raw(args: list[str]) -> bytes:
    cmd = [str(ffmpeg_path()), "-v", "error", "-nostdin", *args, "pipe:1"]
    return subprocess.run(cmd, capture_output=True, check=True,
                          creationflags=CREATIONFLAGS).stdout


def first_bright_frame_time(path: Path, fps: int = 30) -> float:
    """Zeitpunkt des ersten hellen Bildes (Blitz) in einem CFR-Video."""
    raw = _raw(["-i", str(path), "-vf", "scale=8:8,format=gray", "-f", "rawvideo"])
    n = 64
    for i in range(len(raw) // n):
        if sum(raw[i * n:(i + 1) * n]) / n > 128:
            return i / fps
    raise AssertionError("Kein heller Bildabschnitt gefunden")


def first_loud_sample_time(path: Path, rate: int = 48000) -> float:
    raw = _raw(["-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", str(rate),
                "-f", "s16le"])
    for i in range(0, len(raw) - 1, 2):
        if abs(int.from_bytes(raw[i:i + 2], "little", signed=True)) > 1000:
            return (i // 2) / rate
    raise AssertionError("Kein Ton gefunden")


def mean_luma(path: Path) -> float:
    raw = _raw(["-i", str(path), "-frames:v", "1", "-vf", "scale=32:32,format=gray",
                "-f", "rawvideo"])
    return sum(raw) / len(raw)


def remux_with_date(src: Path, out: Path, creation_time: str | None) -> Path:
    args = ["-i", str(src), "-map", "0", "-c", "copy", "-map_metadata", "-1"]
    if creation_time:
        args += ["-metadata", f"creation_time={creation_time}"]
    run_ffmpeg([*args, str(out)])
    return out


def assert_working_format(path: Path) -> tuple[dict, dict | None]:
    """Arbeitsformat bzw. Endvideo: H.264 yuv420p, 30 fps CFR, bt709 ausdrücklich gesetzt."""
    v, a = streams(path)
    assert v["codec_name"] == "h264"
    assert v["pix_fmt"] == "yuv420p"
    assert v["r_frame_rate"] == "30/1"
    assert v["avg_frame_rate"] == "30/1"
    assert int(v["height"]) <= 1080
    assert v.get("color_transfer") == "bt709"
    assert v.get("color_primaries") == "bt709"
    assert v.get("color_space") == "bt709"
    assert not any("rotation" in sd for sd in v.get("side_data_list", []))
    if a is not None:
        assert a["codec_name"] == "aac"
        assert a["sample_rate"] == "48000"
    return v, a


def mean_rgb(path: Path, *, matrix: str = "bt709", at: float = 0.5) -> tuple[float, float, float]:
    """Mittlere Farbe eines Bildes, dekodiert mit der angegebenen Farbmatrix."""
    raw = _raw(["-ss", f"{at:.3f}", "-i", str(path), "-frames:v", "1",
                # erst in voller Größe nach RGB, dann verkleinern: beides in einem Schritt
                # liefert bei swscale abweichende Farben
                "-vf", f"scale=in_color_matrix={matrix}:in_range=tv,format=rgb24,"
                       "scale=8:8:flags=area",
                "-f", "rawvideo"])
    n = len(raw) // 3
    return tuple(sum(raw[c::3]) / n for c in range(3))  # type: ignore[return-value]


def pixel_rgb(path: Path, x: float, y: float) -> tuple[int, int, int]:
    """Farbe an relativer Position (0..1) im ersten Bild, nach automatischer Drehung."""
    raw = _raw(["-i", str(path), "-frames:v", "1", "-vf", "scale=16:16,format=rgb24",
                "-f", "rawvideo"])
    i = (min(15, int(y * 16)) * 16 + min(15, int(x * 16))) * 3
    return raw[i], raw[i + 1], raw[i + 2]


def yuv_at(path: Path, x: float, y: float, *, at: float = 0.5,
           block: int = 16) -> tuple[float, float, float]:
    """Mittlere Y/U/V-Werte (8 Bit, ohne Umrechnung) eines Blocks um die relative Position."""
    v, _ = streams(path)
    w, h = int(v["width"]), int(v["height"])
    raw = _raw(["-ss", f"{at:.3f}", "-i", str(path), "-frames:v", "1",
                "-pix_fmt", "yuv420p", "-f", "rawvideo"])
    cx, cy = int(x * w) // 2 * 2, int(y * h) // 2 * 2
    x0, y0 = max(0, cx - block // 2), max(0, cy - block // 2)
    luma = [raw[r * w + c] for r in range(y0, y0 + block) for c in range(x0, x0 + block)]
    cw, ch = w // 2, h // 2
    u_off, v_off = w * h, w * h + cw * ch
    chroma = [(r * cw + c) for r in range(y0 // 2, (y0 + block) // 2)
              for c in range(x0 // 2, (x0 + block) // 2)]
    n = len(chroma)
    return (sum(luma) / len(luma), sum(raw[u_off + i] for i in chroma) / n,
            sum(raw[v_off + i] for i in chroma) / n)
