"""Je ein Test pro Zeile aus CLAUDE.md "Unterstützte Quellen (handyunabhängig)".

Erkannt wird nur über Metadaten (ffprobe/EXIF). Die Testdateien erzeugt tests/make_media.py;
Dolby Vision kann FFmpeg nicht erzeugen – dafür gibt es einen Test in test_real_media.py.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import pytest
from mediacheck import (
    RED,
    assert_working_format,
    make_jpeg,
    mean_rgb,
    pixel_rgb,
    streams,
    video_pts,
)

from slideshow.core import importer
from slideshow.core.importer import (
    import_media,
    open_photo,
    photo_info,
    video_info,
)
from slideshow.core.normalize import normalize_video, tonemap_filter, video_filter
from slideshow.core.project import FPS, Item, Project
from slideshow.render.assemble import RenderOptions, render_project

pytestmark = pytest.mark.slow


def _copy(synthetic: Path, name: str, target: Path, new_name: str | None = None) -> Path:
    dst = target / (new_name or name)
    shutil.copy2(synthetic / name, dst)
    return dst


def _normalize(synthetic: Path, name: str, umlaut_dir: Path) -> tuple[Path, Path]:
    src = _copy(synthetic, name, umlaut_dir, f"Größe {name}")
    return src, normalize_video(src, umlaut_dir / "Cache Ü")


def _frame_side_data(path: Path) -> list[str]:
    from mediacheck import ffprobe

    data = ffprobe(path, "-select_streams", "v:0", "-read_intervals", "%+#1",
                   "-show_frames")
    return [sd.get("side_data_type", "") for f in data.get("frames", [])
            for sd in f.get("side_data_list", [])]


# --- H.264 8 Bit SDR (ältere Android, WhatsApp): direkt normalisieren ----------------------


def test_h264_8bit_sdr(synthetic: Path, umlaut_dir: Path) -> None:
    src, out = _normalize(synthetic, "Video mit Ton.mp4", umlaut_dir)
    sv, _ = streams(src)
    assert (sv["codec_name"], sv["pix_fmt"]) == ("h264", "yuv420p")
    assert not video_info(src).hdr
    v, a = assert_working_format(out)
    assert (int(v["width"]), int(v["height"])) == (1920, 1080)
    assert a is not None


# --- HEVC 8/10 Bit SDR: 10 Bit auf 8 Bit, bt709 --------------------------------------------


@pytest.mark.parametrize(("name", "pix_fmt"), [("HEVC SDR 8 Bit.mp4", "yuv420p"),
                                               ("HEVC SDR 10 Bit.mp4", "yuv420p10le")])
def test_hevc_sdr(synthetic: Path, umlaut_dir: Path, name: str, pix_fmt: str) -> None:
    src, out = _normalize(synthetic, name, umlaut_dir)
    sv, _ = streams(src)
    assert (sv["codec_name"], sv["pix_fmt"], sv["color_transfer"]) == ("hevc", pix_fmt, "bt709")
    assert not video_info(src).hdr
    v, _ = assert_working_format(out)
    assert v["pix_fmt"] == "yuv420p"  # 8 Bit
    # Kein Tonemapping bei SDR: Helligkeit bleibt gleich
    assert mean_rgb(out) == pytest.approx(mean_rgb(src), abs=6)


# --- HEVC SDR mit bt2020-Primärfarben: Farbraum nach bt709 umrechnen ----------------------


def test_hevc_sdr_bt2020_primaries_converted(synthetic: Path, umlaut_dir: Path) -> None:
    src, out = _normalize(synthetic, "HEVC SDR bt2020.mp4", umlaut_dir)
    info = video_info(src)
    assert (info.color_primaries, info.transfer) == ("bt2020", "bt2020-10")
    assert info.wide_gamut and not info.hdr
    assert "tonemap" not in video_filter(info) and "p=bt709" in video_filter(info)
    assert not video_info(synthetic / "HEVC SDR 10 Bit.mp4").wide_gamut
    v, _ = assert_working_format(out)
    # Quelle: Fläche RGB (50, 180, 60) in bt709, gespeichert in bt2020
    target = (50, 180, 60)
    assert _close_rgb(mean_rgb(out), target, tol=6), mean_rgb(out)
    # Gegenprobe: nur die Matrix umgerechnet (ohne Primärfarben) wirkt deutlich blasser
    assert not _close_rgb(mean_rgb(src, matrix="bt2020"), target, tol=20)


# --- HDR10/HDR10+ (PQ): Tonemapping nach bt709 ---------------------------------------------


@pytest.mark.parametrize("name", ["HDR Hochkant PQ.mp4", "HDR10plus.mp4"])
def test_hdr10_and_hdr10plus(synthetic: Path, umlaut_dir: Path, name: str) -> None:
    src, out = _normalize(synthetic, name, umlaut_dir)
    info = video_info(src)
    assert info.transfer == "smpte2084" and info.hdr
    if name == "HDR10plus.mp4":
        assert any("HDR10+" in t for t in _frame_side_data(src))
    v, _ = assert_working_format(out)
    assert v["color_transfer"] == "bt709"
    assert not any("HDR10+" in t or "Mastering" in t for t in _frame_side_data(out))


# --- HLG (arib-std-b67): Tonemapping nach bt709 --------------------------------------------


@pytest.mark.parametrize("name", ["HDR HLG.mp4", "iPhone HLG.mov"])
def test_hlg(synthetic: Path, umlaut_dir: Path, name: str) -> None:
    src, out = _normalize(synthetic, name, umlaut_dir)
    assert video_info(src).transfer == "arib-std-b67"
    v, _ = assert_working_format(out)
    assert v["color_transfer"] not in ("arib-std-b67", "smpte2084")


# --- Dolby Vision 8.4: als HLG behandeln (Erkennung; echte Datei siehe test_real_media) -----


def _fake_probe(transfer: str, profile: int | None, compat: int | None):  # type: ignore[no-untyped-def]
    side = [{"side_data_type": "DOVI configuration record", "dv_profile": profile,
             "dv_bl_signal_compatibility_id": compat}] if profile is not None else []
    return lambda _p: {
        "format": {"duration": "2.0"},
        "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                     "color_transfer": transfer, "color_primaries": "bt2020",
                     "color_space": "bt2020nc", "side_data_list": side}],
    }


@pytest.mark.parametrize(
    ("transfer", "profile", "compat", "expected"),
    [
        ("arib-std-b67", 8, 4, "arib-std-b67"),  # iPhone: Basisschicht HLG
        ("unknown", 8, 4, "arib-std-b67"),  # ohne Kennlinie: aus DV-Konfiguration
        ("unknown", 8, 1, "smpte2084"),  # Profil 8.1: HDR10-Basisschicht
        ("unknown", None, None, "unknown"),
    ],
)
def test_dolby_vision_detection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                transfer: str, profile: int | None, compat: int | None,
                                expected: str) -> None:
    importer._video_info.cache_clear()
    monkeypatch.setattr(importer, "probe", _fake_probe(transfer, profile, compat))
    f = tmp_path / f"dv {transfer} {profile}.mov"
    f.write_bytes(b"x")
    info = video_info(f)
    importer._video_info.cache_clear()
    assert info.transfer == expected
    assert info.hdr == (expected in ("arib-std-b67", "smpte2084"))
    if profile == 8 and compat == 4:
        assert "tin=arib-std-b67" in tonemap_filter(info)


# --- Drehung nur in Metadaten: korrekt drehen ----------------------------------------------


@pytest.mark.parametrize(
    ("rotation", "red_at", "blue_at"),
    [
        # displaymatrix: Winkel gegen den Uhrzeigersinn; Quelle links rot, rechts blau
        (90, (0.5, 0.9), (0.5, 0.1)),
        (180, (0.9, 0.5), (0.1, 0.5)),
        (270, (0.5, 0.1), (0.5, 0.9)),
    ],
)
def test_rotation_metadata(synthetic: Path, umlaut_dir: Path, rotation: int,
                           red_at: tuple[float, float], blue_at: tuple[float, float]) -> None:
    src, out = _normalize(synthetic, f"Drehung {rotation}.mp4", umlaut_dir)
    info = video_info(src)
    portrait = rotation in (90, 270)
    assert (info.height > info.width) == portrait
    v, _ = assert_working_format(out)  # prüft auch: keine Drehung mehr in den Metadaten
    assert (int(v["height"]) > int(v["width"])) == portrait
    r = pixel_rgb(out, *red_at)
    b = pixel_rgb(out, *blue_at)
    assert r[0] > 180 and r[2] < 80, r
    assert b[2] > 180 and b[0] < 80, b
    assert import_media([src]).media[0].portrait == portrait


# --- Hohe Bildraten und VFR: auf 30 fps CFR ------------------------------------------------


@pytest.mark.parametrize("name", ["60 fps.mp4", "120 fps.mp4", "240 fps.mp4", "VFR Handy.mp4"])
def test_high_fps_and_vfr_to_30_cfr(synthetic: Path, umlaut_dir: Path, name: str) -> None:
    src, out = _normalize(synthetic, name, umlaut_dir)
    duration = video_info(src).duration
    v, a = assert_working_format(out)
    pts = video_pts(out)
    assert len(pts) == pytest.approx(duration * FPS, abs=1)
    assert all(abs((b - a_) - 1 / FPS) < 0.002 for a_, b in zip(pts, pts[1:], strict=False))
    assert a is not None
    assert abs(float(v["start_time"]) - float(a["start_time"])) < 0.05


# --- MOV-Container: wie MP4 ----------------------------------------------------------------


def test_mov_like_mp4(synthetic: Path, umlaut_dir: Path) -> None:
    src = _copy(synthetic, "iPhone HLG.mov", umlaut_dir, "IMG_0042 Ä.MOV")
    res = import_media([umlaut_dir])
    assert [(m.path, m.kind, m.has_audio) for m in res.media] == [(src, "video", True)]
    assert res.media[0].duration == pytest.approx(2.0, abs=0.1)
    out = normalize_video(src, umlaut_dir / "cache")
    assert out.suffix == ".mp4"
    assert_working_format(out)


# --- JPG, HEIC/HEIF, PNG: EXIF-Drehung und Datum ------------------------------------------


@pytest.mark.parametrize(("fmt", "ext"), [("JPEG", ".jpg"), ("HEIF", ".heic"),
                                          ("HEIF", ".heif"), ("PNG", ".png")])
def test_photo_formats_rotation_and_date(umlaut_dir: Path, fmt: str, ext: str) -> None:
    src = make_jpeg(umlaut_dir / f"Foto Größe{ext}", date="2026:11:03 14:00:00",
                    orientation=6, fmt=fmt)
    m = import_media([src]).media[0]
    assert m.kind == "photo"
    assert (m.taken, m.date_source) == (datetime(2026, 11, 3, 14, 0), "exif")
    assert (m.width, m.height) == (100, 200)
    # pillow-heif dreht HEIF schon beim Speichern und setzt die Orientierung auf 1
    assert m.orientation == (1 if fmt == "HEIF" else 6)
    img = open_photo(src)
    assert img.size == (100, 200)
    px = img.getpixel((50, 10))
    assert abs(px[0] - RED[0]) < 40 and px[2] < 80  # oben rot = richtig gedreht


# --- Live Photos und Motion Photos: V1 nur Standbild ---------------------------------------


def test_live_photo_uses_still_only(synthetic: Path, umlaut_dir: Path) -> None:
    heic = _copy(synthetic, "Live Photo.heic", umlaut_dir, "IMG_0001.HEIC")
    # Anderer Name als das Foto: Zuordnung nur über die Kennung in den Metadaten
    mov = _copy(synthetic, "Live Photo.mov", umlaut_dir, "Bewegung Ü.MOV")
    # Gleicher Name wie das Foto, aber keine Kennung: bleibt ein normales Video
    plain = _copy(synthetic, "Video mit Ton.mp4", umlaut_dir, "IMG_0001.MOV")
    assert photo_info(heic).content_ids

    res = import_media([umlaut_dir])
    assert {m.path for m in res.media} == {heic, plain}
    assert res.live_videos == [(mov, heic)]
    by_path = {m.path: m for m in res.media}
    assert by_path[heic].live_photo
    assert not by_path[plain].live_photo


def test_live_video_without_photo_is_kept(synthetic: Path, umlaut_dir: Path) -> None:
    mov = _copy(synthetic, "Live Photo.mov", umlaut_dir)
    res = import_media([umlaut_dir])
    assert [m.path for m in res.media] == [mov] and res.live_videos == []


def test_motion_photo_uses_still_only(synthetic: Path, umlaut_dir: Path) -> None:
    still = make_jpeg(umlaut_dir / "still.jpg", date="2026:06:01 12:00:00")
    motion = umlaut_dir / "Bewegung Ö.jpg"
    motion.write_bytes(still.read_bytes() + b"MotionPhoto_Data"
                       + (synthetic / "VFR Handy.mp4").read_bytes() + b"\x00SEFT")
    still.unlink()
    m = import_media([umlaut_dir]).media
    assert [(x.path, x.kind, x.motion_photo) for x in m] == [(motion, "photo", True)]
    assert open_photo(motion).size == (200, 100)


# --- Fehlende Farbmetadaten: als bt709 annehmen ---------------------------------------------

def _close_rgb(rgb: tuple[float, ...], expected: tuple[float, ...], tol: float = 3) -> bool:
    return all(abs(c - e) <= tol for c, e in zip(rgb, expected, strict=True))


def test_missing_color_tags_assumed_bt709(synthetic: Path, umlaut_dir: Path) -> None:
    src, out = _normalize(synthetic, "Ohne Farbangaben.mp4", umlaut_dir)
    info = video_info(src)
    assert info.color_tags_missing
    # Quelle: Fläche RGB (200, 50, 50), als bt709 kodiert
    expected = mean_rgb(src, matrix="bt709")
    assert _close_rgb(expected, (200, 50, 50), tol=6)
    # Gegenprobe: als bt601 gelesen wäre die Farbe deutlich falsch
    assert not _close_rgb(mean_rgb(src, matrix="bt601"), expected, tol=6)
    assert_working_format(out)
    assert _close_rgb(mean_rgb(out), expected), (mean_rgb(out), expected)


def test_end_video_bt709_for_untagged_and_hdr_sources(synthetic: Path,
                                                      umlaut_dir: Path) -> None:
    """Endvideo: Farbangaben ausdrücklich bt709, Farbe einer unmarkierten Quelle stimmt."""
    untagged = _copy(synthetic, "Ohne Farbangaben.mp4", umlaut_dir)
    hdr = _copy(synthetic, "HDR HLG.mp4", umlaut_dir)
    normalize_video(hdr, umlaut_dir / "cache")  # wie nach dem Import
    project = Project(items=[Item("video", path=untagged.name, transition="none"),
                             Item("video", path=hdr.name, transition="none")],
                      base_dir=umlaut_dir)
    out = umlaut_dir / "Ende Ü.mp4"
    render_project(project, RenderOptions(out, preview=True, cache_dir=umlaut_dir / "cache"))
    v, _ = streams(out)
    assert (v.get("color_primaries"), v.get("color_transfer"), v.get("color_space")) == (
        "bt709", "bt709", "bt709")
    expected = mean_rgb(untagged, matrix="bt709")
    assert _close_rgb(mean_rgb(out, at=0.5), expected), (mean_rgb(out, at=0.5), expected)
