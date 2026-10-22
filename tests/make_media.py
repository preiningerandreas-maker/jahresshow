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


def _hdr_video(out: Path, size: tuple[int, int], seconds: float, transfer: str,
               rotation: int = 0) -> None:
    """HEVC 10 Bit HDR wie vom Samsung S23 (PQ = smpte2084, HLG = arib-std-b67)."""
    w, h = size
    tmp = out.with_suffix(".tmp.mp4")
    run_ffmpeg([
        "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=500:sample_rate=48000",
        "-t", str(seconds),
        "-vf", f"zscale=pin=bt709:tin=bt709:min=bt709:rin=tv:p=bt2020:t={transfer}:"
               "m=bt2020nc:r=tv:npl=100,format=yuv420p10le",
        "-c:v", "libx265", "-preset", "ultrafast", "-crf", "30",
        "-x265-params", f"log-level=error:colorprim=bt2020:transfer={transfer}:"
                        "colormatrix=bt2020nc",
        "-color_primaries", "bt2020", "-color_trc", transfer, "-colorspace", "bt2020nc",
        "-tag:v", "hvc1", "-ac", "2", "-c:a", "aac", "-b:a", "96k", str(tmp),
    ])
    # Hochkant-Handyvideos speichern quer und setzen nur eine Drehung in den Metadaten
    run_ffmpeg(["-display_rotation", str(rotation), "-i", str(tmp), "-c", "copy",
                "-movflags", "+faststart", str(out)])
    tmp.unlink()


def _vfr_sync(out: Path, seconds: float = 4) -> None:
    """VFR-Video: weißer Blitz und Piepton gleichzeitig bei 2,0–2,2 s, sonst schwarz/still."""
    run_ffmpeg([
        "-f", "lavfi", "-i", f"color=c=black:size=640x360:rate=60:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=1000:sample_rate=48000:duration={seconds}",
        "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='between(t,2,2.2)',"
               "select='not(mod(n\\,3))+not(mod(n\\,7))',format=yuv420p",
        "-fps_mode", "vfr",
        "-af", "volume=volume=0:enable='not(between(t,2,2.2))'",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
        "-ac", "2", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out),
    ])


LIVE_PHOTO_ID = "5B1F2C3D-4E5F-4A6B-8C7D-9E0F1A2B3C4D"
EXIF_MAKER_NOTE = 0x927C

# HDR10+-Szene (SMPTE ST 2094-40) für x265, Format wie hdr10plus_tool
HDR10PLUS_JSON = (
    '{"JSONInfo":{"HDR10plusProfile":"A","Version":"1.0"},"SceneInfo":[{"LuminanceParameters":'
    '{"AverageRGB":1000,"LuminanceDistributions":{"DistributionIndex":[1,5,10,25,50,75,90,95,99],'
    '"DistributionValues":[10,100,200,500,1000,2000,3000,4000,5000]},"MaxScl":[5000,5000,5000]},'
    '"NumberOfWindows":1,"TargetedSystemDisplayMaximumLuminance":400,"SceneFrameIndex":0,'
    '"SceneId":0,"SequenceFrameIndex":0}],"SceneInfoSummary":{"SceneFirstFrameIndex":[0],'
    '"SceneFrameNumbers":[60]},"ToolInfo":{"Tool":"jahresshow","Version":"1"}}'
)


def _x265_escape(path: Path) -> str:
    return path.resolve().as_posix().replace(":", "\\:")


def _hevc_sdr(out: Path, bits: int) -> None:
    """HEVC SDR wie von aktuellen Handys ohne HDR, mit bt709-Farbangaben."""
    pix = "yuv420p10le" if bits == 10 else "yuv420p"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=600:sample_rate=48000", "-t", "2",
        "-vf", f"format={pix}", "-c:v", "libx265", "-preset", "ultrafast", "-crf", "30",
        "-x265-params", "log-level=error:colorprim=bt709:transfer=bt709:colormatrix=bt709",
        "-tag:v", "hvc1", "-ac", "2", "-c:a", "aac", "-b:a", "64k", str(out),
    ])


def _hevc_bt2020_sdr(out: Path) -> None:
    """HEVC 10 Bit SDR mit bt2020-Primärfarben: Fläche RGB (50, 180, 60) in bt709."""
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=0x32B43C:size=320x180:rate=30:duration=1",
        "-vf", "format=rgb24,scale=out_color_matrix=bt709:out_range=tv,format=yuv444p,"
               "zscale=pin=bt709:tin=bt709:min=bt709:rin=tv:"
               "p=bt2020:t=bt2020-10:m=bt2020nc:r=tv,format=yuv420p10le",
        "-c:v", "libx265", "-preset", "ultrafast", "-crf", "20",
        "-x265-params", "log-level=error:colorprim=bt2020:transfer=bt2020-10:"
                        "colormatrix=bt2020nc",
        "-tag:v", "hvc1", str(out),
    ])


def _hdr10plus(out: Path) -> None:
    """HDR10+: PQ mit dynamischen Metadaten (Samsung)."""
    meta = out.with_suffix(".tmp.json")
    meta.write_text(HDR10PLUS_JSON, "utf-8")
    try:
        run_ffmpeg([
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30", "-t", "2",
            "-vf", "zscale=pin=bt709:tin=bt709:min=bt709:rin=tv:p=bt2020:t=smpte2084:"
                   "m=bt2020nc:r=tv:npl=100,format=yuv420p10le",
            "-c:v", "libx265", "-preset", "ultrafast", "-crf", "30",
            "-x265-params", "log-level=error:hdr10=1:colorprim=bt2020:transfer=smpte2084:"
                            f"colormatrix=bt2020nc:dhdr10-info={_x265_escape(meta)}",
            "-tag:v", "hvc1", str(out),
        ])
    finally:
        meta.unlink(missing_ok=True)


def _rotated(out: Path, rotation: int) -> None:
    """Links rot, rechts blau; Drehung nur in den Metadaten (displaymatrix)."""
    tmp = out.with_suffix(".tmp.mp4")
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=red:size=160x180:rate=30:duration=1,"
                "pad=320:180:0:0:blue", "-c:v", "libx264", "-preset", "veryfast",
                "-pix_fmt", "yuv420p", str(tmp)])
    run_ffmpeg(["-display_rotation", str(rotation), "-i", str(tmp), "-c", "copy", str(out)])
    tmp.unlink()


def _high_fps(out: Path, fps: int) -> None:
    run_ffmpeg([
        "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate={fps}",
        "-f", "lavfi", "-i", "sine=frequency=700:sample_rate=48000", "-t", "1",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "35", "-pix_fmt", "yuv420p",
        "-ac", "2", "-c:a", "aac", "-b:a", "64k", str(out),
    ])


def _no_color_tags(out: Path) -> None:
    """Fläche RGB (200, 50, 50), als bt709 kodiert, aber ohne Farbangaben (wie WhatsApp)."""
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=0xC83232:size=320x180:rate=30:duration=1",
        "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast",
        # 2 = "nicht angegeben" für Primärfarben, Kennlinie und Matrix
        "-bsf:v", "h264_metadata=colour_primaries=2:transfer_characteristics=2:"
                  "matrix_coefficients=2",
        str(out),
    ])


def _live_photo_still(out: Path) -> None:
    """HEIC wie vom iPhone: Kennung des Live Photos in der Apple-MakerNote."""
    img = Image.new("RGB", (400, 300), (40, 120, 200))
    exif = Image.Exif()
    sub = exif.get_ifd(EXIF_IFD)
    sub[EXIF_DATETIME_ORIGINAL] = "2026:09:12 15:20:00"
    sub[EXIF_MAKER_NOTE] = b"Apple iOS\x00\x00\x01MM" + LIVE_PHOTO_ID.encode("ascii")
    from pillow_heif import register_heif_opener

    register_heif_opener()
    img.save(out, "HEIF", quality=60, exif=exif.tobytes())


def _live_photo_video(out: Path) -> None:
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30", "-t", "1.5",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "35", "-pix_fmt", "yuv420p",
        "-metadata", f"com.apple.quicktime.content.identifier={LIVE_PHOTO_ID}",
        "-movflags", "use_metadata_tags+faststart", str(out),
    ])


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
    "HDR Hochkant PQ.mp4": lambda p: _hdr_video(p, (1280, 720), 2, "smpte2084", 90),
    "HDR HLG.mp4": lambda p: _hdr_video(p, (1280, 720), 2, "arib-std-b67"),
    "VFR Blitz Ton.mp4": lambda p: _vfr_sync(p),
    "HEVC SDR 8 Bit.mp4": lambda p: _hevc_sdr(p, 8),
    "HEVC SDR 10 Bit.mp4": lambda p: _hevc_sdr(p, 10),
    "HEVC SDR bt2020.mp4": _hevc_bt2020_sdr,
    "HDR10plus.mp4": _hdr10plus,
    "iPhone HLG.mov": lambda p: _hdr_video(p, (640, 360), 2, "arib-std-b67"),
    "Drehung 90.mp4": lambda p: _rotated(p, 90),
    "Drehung 180.mp4": lambda p: _rotated(p, 180),
    "Drehung 270.mp4": lambda p: _rotated(p, 270),
    "60 fps.mp4": lambda p: _high_fps(p, 60),
    "120 fps.mp4": lambda p: _high_fps(p, 120),
    "240 fps.mp4": lambda p: _high_fps(p, 240),
    "Ohne Farbangaben.mp4": _no_color_tags,
    "Live Photo.heic": _live_photo_still,
    "Live Photo.mov": _live_photo_video,
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
