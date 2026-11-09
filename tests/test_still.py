"""Standbild-Vorschau: gleiche Filter wie der Export (Formatmodus, Text, Farben)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat

from slideshow.core.ffmpeg import run_ffmpeg
from slideshow.core.normalize import normalize_video
from slideshow.core.project import Item, Project, TextOverlay
from slideshow.render.segment import RenderConfig, render_segment, render_still, still_key

pytestmark = pytest.mark.slow

W, H = 640, 360


def first_frame(segment: Path, out: Path) -> Path:
    """Erstes Bild eines Segments, umgerechnet wie im Standbild (bt709 → RGB)."""
    run_ffmpeg(["-i", str(segment), "-frames:v", "1", "-vf",
                "scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=rgb24",
                "-update", "1", str(out)])
    return out


def mean_diff(a: Path, b: Path) -> float:
    with Image.open(a) as ia, Image.open(b) as ib:
        diff = ImageChops.difference(ia.convert("RGB"), ib.convert("RGB"))
        return sum(ImageStat.Stat(diff).mean) / 3


@pytest.mark.parametrize("fit", ["blur", "black", "crop"])
def test_still_matches_export_segment(fit: str, synthetic: Path, umlaut_dir: Path,
                                      tmp_path: Path) -> None:
    src = umlaut_dir / "Hochkant Größe.jpg"
    shutil.copy2(synthetic / src.name, src)
    item = Item("photo", path=str(src), duration=1.0, fit=fit,
                text=TextOverlay("Grüße aus Kärnten – ß", color="#FFE08A", bar_color="#203040",
                                 bar_opacity=0.7, fade=0.0))
    project = Project(items=[item], base_dir=umlaut_dir)
    cache = tmp_path / "cache"

    still = render_still(project, item, W, H, tmp_path / "still.png", cache)
    entry = project.timeline(lambda _p: 0.0)[0]
    seg, _ = render_segment(project, entry, RenderConfig(W, H, "libx264", cache))
    frame = first_frame(seg, tmp_path / "frame.png")
    # Nur Unterschiede durch die H.264-Kompression des Segments
    assert mean_diff(still, frame) < 3.0
    assert not list(cache.rglob("*.part*"))


def test_still_of_converted_video_matches_export(synthetic: Path, umlaut_dir: Path,
                                                  tmp_path: Path) -> None:
    src = umlaut_dir / "HDR HLG.mp4"
    shutil.copy2(synthetic / src.name, src)
    cache = tmp_path / "cache"
    normalize_video(src, cache)
    item = Item("video", path=str(src), duration=1.0, start=0.5,
                text=TextOverlay("Urlaub", position="top", fade=0.0))
    project = Project(items=[item], base_dir=umlaut_dir)
    still = render_still(project, item, W, H, tmp_path / "still.png", cache)
    entry = project.timeline(lambda _p: 1.0)[0]
    seg, _ = render_segment(project, entry, RenderConfig(W, H, "libx264", cache))
    assert mean_diff(still, first_frame(seg, tmp_path / "frame.png")) < 3.0


def test_still_key_changes_with_visible_settings(umlaut_dir: Path, synthetic: Path) -> None:
    src = umlaut_dir / "Querformat Wörthersee.jpg"
    shutil.copy2(synthetic / src.name, src)
    project = Project(base_dir=umlaut_dir)
    base = Item("photo", path=str(src))
    k = still_key(project, base, W, H, umlaut_dir)
    assert still_key(project, Item("photo", path=str(src), duration=9.0), W, H, umlaut_dir) == k
    for changed in (Item("photo", path=str(src), fit="crop"),
                    Item("photo", path=str(src), text=TextOverlay("Hallo"))):
        assert still_key(project, changed, W, H, umlaut_dir) != k
