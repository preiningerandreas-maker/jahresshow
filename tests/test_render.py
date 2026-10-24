"""Echte Render-Läufe mit FFmpeg (480p-Vorschau, damit es auch auf dem T450s schnell geht)."""

from __future__ import annotations

import array
import math
import shutil
import threading
import wave
from pathlib import Path

import pytest
from mediacheck import assert_working_format, pixel_rgb, yuv_at

from slideshow.cli import main as cli_main
from slideshow.core import normalize
from slideshow.core.ffmpeg import Cancelled, probe, run_ffmpeg
from slideshow.core.importer import clip_length, video_info
from slideshow.core.normalize import BackgroundNormalizer, cached_normalized
from slideshow.core.project import FPS, Item, Music, Project, Settings, TextOverlay
from slideshow.render import assemble
from slideshow.render.assemble import RenderError, RenderOptions, render_project
from slideshow.render.audio import build_music
from slideshow.render.segment import RenderConfig, prepare_photo, render_segment

pytestmark = pytest.mark.slow


def _copy_media(synthetic: Path, target: Path, *names: str) -> None:
    for n in names:
        shutil.copy2(synthetic / n, target / n)


def _streams(path: Path) -> tuple[dict, dict, dict]:
    data = probe(path)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    a = next(s for s in data["streams"] if s["codec_type"] == "audio")
    return v, a, data["format"]


@pytest.fixture
def show(synthetic: Path, umlaut_dir: Path) -> Project:
    _copy_media(synthetic, umlaut_dir, "Querformat Wörthersee.jpg", "Hochkant Größe.jpg",
                "Video mit Ton.mp4", "VFR Handy.mp4", "Winter Schnee.heic",
                "Musik 2 – Sommer.m4a")
    p = Project(
        name="Test Ö",
        items=[
            Item("title", duration=2.0, text=TextOverlay("Jahr 2026 – Grüße", position="center")),
            Item("photo", path="Querformat Wörthersee.jpg", duration=2.0,
                 text=TextOverlay("Wörthersee")),
            Item("photo", path="Hochkant Größe.jpg", duration=2.0, fit="black",
                 transition="wipeleft"),
            Item("video", path="Video mit Ton.mp4", start=1.0, duration=3.0,
                 transition="slideleft"),
            Item("video", path="VFR Handy.mp4", fit="crop", transition="none"),
            Item("photo", path="Winter Schnee.heic", duration=2.0, transition="fadeblack"),
        ],
        music=Music(tracks=["Musik 2 – Sommer.m4a"]),  # 9 s: muss in Schleife laufen
        settings=Settings(transition_duration=0.5),
        base_dir=umlaut_dir,
    )
    p.save(umlaut_dir / "Projekt.json")
    return p


def test_render_preview_blocks_and_cache(show: Project, umlaut_dir: Path) -> None:
    cache = umlaut_dir / "cache"
    out = umlaut_dir / "Ausgabe Ü" / "show.mp4"
    expected = show.total_frames(clip_length)
    # 2+2+2+3+5+2 = 16 s, minus 4 Übergänge à 0,5 s
    assert expected == 14 * FPS

    opts = RenderOptions(out, preview=True, cache_dir=cache, max_block=2)
    first = render_project(show, opts)
    assert first.blocks_total == 3 and first.segments_cached == 0
    v, a, fmt = _streams(out)
    assert (v["width"], v["height"]) == (854, 480)
    assert v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p"
    assert v["r_frame_rate"] == "30/1" and v["avg_frame_rate"] == "30/1"
    assert int(v["nb_frames"]) == expected
    assert a["codec_name"] == "aac" and a["sample_rate"] == "48000"
    assert abs(float(fmt["duration"]) - expected / FPS) < 0.05

    second = render_project(show, opts)
    assert second.segments_cached == second.segments_total
    assert second.blocks_cached == second.blocks_total
    assert second.seconds * 3 < first.seconds

    # Nur ein Element ändern: nur dieses Segment und sein Block werden neu gerendert
    show.items[1].text = TextOverlay("Wörthersee geändert")
    third = render_project(show, opts)
    assert third.segments_cached == third.segments_total - 1
    assert third.blocks_cached == third.blocks_total - 1
    assert not list(cache.rglob("*.part.*"))


def test_cli_preview_section(show: Project, umlaut_dir: Path) -> None:
    out = umlaut_dir / "abschnitt.mp4"
    rc = cli_main(["render", str(umlaut_dir / "Projekt.json"), "-o", str(out), "--preview",
                   "--from", "2", "--to", "3", "--cache-dir", str(umlaut_dir / "cache")])
    assert rc == 0
    v, _, fmt = _streams(out)
    assert v["height"] == 480
    assert abs(float(fmt["duration"]) - 3.5) < 0.05


def test_cancel_leaves_no_partial_files(show: Project, umlaut_dir: Path) -> None:
    cache = umlaut_dir / "cache"
    cancel = threading.Event()

    def status(msg: str, frac: float) -> None:
        if frac > 0.05:
            cancel.set()

    with pytest.raises(Cancelled):
        render_project(show, RenderOptions(umlaut_dir / "x.mp4", preview=True, cache_dir=cache),
                       status=status, cancel=cancel)
    assert not (umlaut_dir / "x.mp4").exists()
    assert not list(cache.rglob("*.part.*"))


def test_free_space_check(show: Project, umlaut_dir: Path,
                          monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(assemble.shutil, "disk_usage", lambda _p: shutil._ntuple_diskusage(1, 1, 0))
    with pytest.raises(RenderError, match="Speicherplatz"):
        render_project(show, RenderOptions(umlaut_dir / "x.mp4", preview=True,
                                           cache_dir=umlaut_dir / "cache"))


def test_missing_file_message(show: Project, umlaut_dir: Path) -> None:
    show.items[1].path = "fehlt.jpg"
    with pytest.raises(RenderError, match="nicht gefunden"):
        render_project(show, RenderOptions(umlaut_dir / "x.mp4", preview=True,
                                           cache_dir=umlaut_dir / "cache"))


def test_vfr_video_segment_is_cfr(synthetic: Path, umlaut_dir: Path) -> None:
    _copy_media(synthetic, umlaut_dir, "VFR Handy.mp4")
    p = Project(items=[Item("video", path="VFR Handy.mp4", duration=3.0)], base_dir=umlaut_dir)
    entry = p.timeline(clip_length)[0]
    cfg = RenderConfig(854, 480, "libx264", umlaut_dir / "cache")
    seg, cached = render_segment(p, entry, cfg)
    assert not cached
    v, a, _ = _streams(seg)
    assert v["avg_frame_rate"] == "30/1" and int(v["nb_frames"]) == 90
    assert abs(float(a["duration"]) - 3.0) < 0.01
    assert render_segment(p, entry, cfg) == (seg, True)


def test_prepare_photo_applies_exif_rotation_and_heic(synthetic: Path, tmp_path: Path) -> None:
    from PIL import Image

    out = prepare_photo(synthetic / "Gedreht EXIF.jpg", 1920, 1080, tmp_path / "a.png")
    with Image.open(out) as im:
        assert im.height > im.width  # gespeichert quer, EXIF 6 -> hochkant
    out = prepare_photo(synthetic / "Winter Schnee.heic", 1920, 1080, tmp_path / "b.png")
    with Image.open(out) as im:
        assert im.size == (1600, 1200)  # kleiner als Ziel: nicht hochskaliert


def _mean_volume(path: Path, start: float, end: float, tmp: Path) -> float:
    """RMS-Pegel (dB) eines Ausschnitts."""
    wav = tmp / "ausschnitt.wav"
    run_ffmpeg(["-ss", str(start), "-to", str(end), "-i", str(path),
                "-c:a", "pcm_s16le", str(wav)])
    with wave.open(str(wav), "rb") as w:
        samples = array.array("h", w.readframes(w.getnframes()))
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    return 20 * math.log10(max(rms, 1e-9) / 32768)


def test_music_loops_fades_and_ducks(synthetic: Path, umlaut_dir: Path) -> None:
    _copy_media(synthetic, umlaut_dir, "Musik 1 – Frühling.mp3", "Musik 2 – Sommer.m4a")
    p = Project(music=Music(tracks=["Musik 2 – Sommer.m4a", "Musik 1 – Frühling.mp3"],
                            fade_out=3.0, duck_volume=0.25),
                base_dir=umlaut_dir)
    total = 30.0  # Playlist ist 21 s lang -> Schleife
    music = build_music(p, total, [(5.0, 9.0)], umlaut_dir / "cache")
    assert music is not None
    data = probe(music)
    a, fmt = data["streams"][0], data["format"]
    assert a["sample_rate"] == "48000" and a["channels"] == 2
    assert abs(float(fmt["duration"]) - total) < 0.01

    normal = _mean_volume(music, 1.0, 4.0, umlaut_dir)
    ducked = _mean_volume(music, 6.0, 8.5, umlaut_dir)
    looped = _mean_volume(music, 22.0, 25.0, umlaut_dir)
    ending = _mean_volume(music, 29.5, 29.95, umlaut_dir)
    # 25 % Lautstärke entspricht etwa -12 dB
    assert 9.0 < normal - ducked < 15.0
    assert looped > normal - 3.0  # nach der Schleife läuft wieder Musik
    assert ending < normal - 15.0  # Fade-out
    # zweiter Aufruf aus dem Cache
    assert build_music(p, total, [(5.0, 9.0)], umlaut_dir / "cache") == music


def test_original_sound_per_clip(synthetic: Path, umlaut_dir: Path) -> None:
    """Derselbe Clip dreimal: 100 %, 50 % und ohne Originalton. Jeder behält seinen Pegel."""
    _copy_media(synthetic, umlaut_dir, "Video mit Ton.mp4")
    clip = {"kind": "video", "path": "Video mit Ton.mp4", "duration": 2.0, "transition": "none"}
    p = Project(items=[Item(**clip), Item(**clip, volume=0.5), Item(**clip, audio=False)],
                base_dir=umlaut_dir)
    out = umlaut_dir / "Originalton Ü.mp4"
    render_project(p, RenderOptions(out, encoder="libx264", cache_dir=umlaut_dir / "cache"))
    full, half, off = (_mean_volume(out, a + 0.3, a + 1.7, umlaut_dir) for a in (0.0, 2.0, 4.0))
    assert full > -30.0  # Sinuston ist deutlich zu hören
    assert 5.0 < full - half < 7.0  # 50 % entspricht etwa -6 dB
    assert off < -70.0  # stumm


def test_final_video_has_bt709_color_metadata(show: Project, umlaut_dir: Path) -> None:
    """Endvideo trägt bt709 ausdrücklich in den Metadaten, auch bei Quellen ohne Farbangaben
    (Fotos, Handyvideos mit "unknown")."""
    out = umlaut_dir / "Farbe Ü.mp4"
    render_project(show, RenderOptions(out, preview=True, cache_dir=umlaut_dir / "cache"))
    v, _, _ = _streams(out)
    assert v.get("color_primaries") == "bt709"
    assert v.get("color_transfer") == "bt709"
    assert v.get("color_space") == "bt709"


def test_hard_cut_then_transition_in_one_block(synthetic: Path, umlaut_dir: Path) -> None:
    """Regression: harter Schnitt und danach Überblendung im selben Block (Standardgröße 20)."""
    _copy_media(synthetic, umlaut_dir, "Querformat Wörthersee.jpg", "Hochkant Größe.jpg")
    p = Project(
        items=[
            Item("photo", path="Querformat Wörthersee.jpg", duration=1.0),
            Item("photo", path="Hochkant Größe.jpg", duration=1.0, transition="none"),
            Item("photo", path="Querformat Wörthersee.jpg", duration=1.0, transition="fade"),
        ],
        settings=Settings(transition_duration=0.4),
        base_dir=umlaut_dir,
    )
    out = umlaut_dir / "Schnitt.mp4"
    result = render_project(p, RenderOptions(out, preview=True, cache_dir=umlaut_dir / "c"))
    assert result.blocks_total == 1
    v, _, _ = _streams(out)
    assert int(v["nb_frames"]) == p.total_frames(clip_length)


# Sollwerte bt709, begrenzter Bereich (Y 16–235, U/V 16–240): Weiß und reines Rot
# (bt601 ergäbe für Rot Y 81, U 90 – das wäre der Fehler bei der automatischen Umwandlung)
TEXT_YUV = {"#FFFFFF": (235.0, 128.0, 128.0), "#FF0000": (62.6, 102.3, 240.0)}


def test_text_overlay_colors_bt709(synthetic: Path, umlaut_dir: Path) -> None:
    """Weißes und rotes Textfeld auf Farbfläche, Foto und Video: Pixelwerte im Endvideo."""
    _copy_media(synthetic, umlaut_dir, "Querformat Wörthersee.jpg", "Video mit Ton.mp4")
    backgrounds = [
        {"kind": "title"},
        {"kind": "photo", "path": "Querformat Wörthersee.jpg"},
        {"kind": "video", "path": "Video mit Ton.mp4", "audio": False},
    ]
    items = [
        Item(**bg, duration=1.0, transition="none",
             text=TextOverlay("Ä", position="center", size=200, color=color,
                              bar_color=color, bar_opacity=1.0))
        for bg in backgrounds for color in TEXT_YUV
    ]
    out = umlaut_dir / "Textfarben Ü.mp4"
    render_project(Project(items=items, base_dir=umlaut_dir),
                   RenderOptions(out, encoder="libx264", cache_dir=umlaut_dir / "cache"))
    v, _, _ = _streams(out)
    assert (int(v["width"]), int(v["height"])) == (1920, 1080)
    for i, item in enumerate(items):
        assert item.text is not None
        expected = TEXT_YUV[item.text.color]
        # Mitte des Balkens, links neben dem Text
        got = yuv_at(out, 0.1, 0.5, at=i + 0.5)
        assert all(abs(g - e) <= 2 for g, e in zip(got, expected, strict=True)), (
            item.kind, item.text.color, got, expected)


# --- Rendern nie mit Original-Videos (außer 480p-Vorschau) --------------------------------


def _hdr_project(synthetic: Path, umlaut_dir: Path, name: str = "HDR Hochkant PQ.mp4"
                 ) -> tuple[Project, Path]:
    _copy_media(synthetic, umlaut_dir, name)
    return Project(items=[Item("video", path=name)], base_dir=umlaut_dir), umlaut_dir / name


def test_export_normalizes_hdr_video_without_cache(synthetic: Path, umlaut_dir: Path) -> None:
    """HDR-Video ohne Cache direkt exportieren: Endvideo ist bt709 und tonemapped."""
    project, src = _hdr_project(synthetic, umlaut_dir)
    cache = umlaut_dir / "cache"
    assert video_info(src).hdr and cached_normalized(src, cache) is None
    messages: list[str] = []
    out = umlaut_dir / "HDR Export Ü.mp4"
    result = render_project(project, RenderOptions(out, encoder="libx264", cache_dir=cache),
                            status=lambda m, _f: messages.append(m))
    assert result.videos_normalized == 1 and result.notes == []
    assert any("wird umgewandelt" in m for m in messages)
    normalized = cached_normalized(src, cache)
    assert normalized is not None

    v, _ = assert_working_format(out)
    assert v["color_transfer"] not in ("smpte2084", "arib-std-b67")
    assert not video_info(out).hdr
    # Bildinhalt wie die tonemappte Fassung (Mitte des hochkant zentrierten Bildes) …
    ref = pixel_rgb(normalized, 0.5, 0.5)
    got = pixel_rgb(out, 0.5, 0.5)
    assert all(abs(a - b) <= 12 for a, b in zip(got, ref, strict=True)), (got, ref)
    # … und nicht wie das Original ohne Tonemapping
    raw = pixel_rgb(src, 0.5, 0.5)
    assert any(abs(a - b) > 30 for a, b in zip(got, raw, strict=True)), (got, raw)


def test_preview_may_use_original_with_note(synthetic: Path, umlaut_dir: Path) -> None:
    project, src = _hdr_project(synthetic, umlaut_dir)
    cache = umlaut_dir / "cache"
    result = render_project(project, RenderOptions(umlaut_dir / "Vorschau.mp4", preview=True,
                                                   cache_dir=cache))
    assert result.videos_normalized == 0
    assert len(result.notes) == 1 and src.name in result.notes[0]
    assert cached_normalized(src, cache) is None


def test_render_waits_for_running_normalization(synthetic: Path, umlaut_dir: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """Läuft die Umwandlung schon im Hintergrund, wartet der Export statt doppelt zu rechnen."""
    project, src = _hdr_project(synthetic, umlaut_dir, "HDR HLG.mp4")
    cache = umlaut_dir / "cache"
    started = threading.Event()
    gate = threading.Event()  # hält die Hintergrund-Umwandlung an, bis der Export wartet
    calls: list[Path] = []
    real = normalize._normalize

    def slow(src_: Path, final: Path, **kw):  # type: ignore[no-untyped-def]
        calls.append(src_)
        started.set()
        assert gate.wait(30)
        return real(src_, final, **kw)

    monkeypatch.setattr(normalize, "_normalize", slow)
    worker = BackgroundNormalizer(cache)
    worker.submit([src])
    assert started.wait(30)
    assert normalize.is_running(src, cache)
    messages: list[str] = []

    def status(msg: str, _frac: float) -> None:
        messages.append(msg)
        if "wartet" in msg:
            gate.set()

    try:
        result = render_project(project, RenderOptions(umlaut_dir / "Warten.mp4",
                                                       encoder="libx264", cache_dir=cache),
                                status=status)
    finally:
        gate.set()
    worker.wait()
    worker.close()
    assert calls == [src]  # nur einmal umgewandelt
    assert any("wartet auf laufende Umwandlung" in m for m in messages)
    assert result.notes == []
    assert_working_format(umlaut_dir / "Warten.mp4")


def test_segment_never_uses_original(synthetic: Path, umlaut_dir: Path) -> None:
    """Auch direkt aufgerufen holt render_segment die fehlende Umwandlung nach."""
    project, src = _hdr_project(synthetic, umlaut_dir, "HDR HLG.mp4")
    cfg = RenderConfig(1920, 1080, "libx264", umlaut_dir / "cache")
    entry = project.timeline(clip_length)[0]
    render_segment(project, entry, cfg)
    assert cached_normalized(src, cfg.cache_dir) is not None
