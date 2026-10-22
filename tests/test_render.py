"""Echte Render-Läufe mit FFmpeg (480p-Vorschau, damit es auch auf dem T450s schnell geht)."""

from __future__ import annotations

import array
import math
import shutil
import threading
import wave
from pathlib import Path

import pytest

from slideshow.cli import main as cli_main
from slideshow.core.ffmpeg import Cancelled, probe, run_ffmpeg
from slideshow.core.importer import clip_length
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
