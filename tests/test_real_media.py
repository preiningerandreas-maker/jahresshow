"""Tests mit echten Beispieldateien aus tests/media/real/ (nicht im Git).

Fehlt der Ordner oder ein Dateityp darin, werden die Tests übersprungen.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import REAL_MEDIA
from mediacheck import streams, video_pts
from PIL import Image

from slideshow.core.chapters import build_items, is_chapter_title
from slideshow.core.importer import (
    PHOTO_EXTS,
    VIDEO_EXTS,
    import_media,
    open_photo,
    photo_info,
    video_info,
)
from slideshow.core.normalize import normalize_video
from slideshow.core.thumbs import ThumbnailCache


def _real(exts: set[str]) -> list[Path]:
    if not REAL_MEDIA.is_dir():
        return []
    return sorted(p for p in REAL_MEDIA.rglob("*") if p.suffix.lower() in exts)


REAL_VIDEOS = _real(VIDEO_EXTS)
REAL_HEIC = _real({".heic", ".heif"})
REAL_PHOTOS = _real(PHOTO_EXTS)
# Höchstens 12 Fotos für die teureren Einzeltests, gleichmäßig verteilt
SAMPLE_PHOTOS = REAL_PHOTOS[:: max(1, len(REAL_PHOTOS) // 12)][:12]


def _ids(paths: list[Path]) -> list[str]:
    return [p.name for p in paths]


def test_import_real_folder(real_media: Path) -> None:
    res = import_media([real_media])
    assert res.media, "keine Fotos/Videos gefunden"
    assert res.skipped == [], res.messages()
    taken = [m.taken for m in res.media]
    assert taken == sorted(taken)
    # Bei Handyfotos sollte fast immer ein echtes Aufnahmedatum vorhanden sein
    photos = [m for m in res.media if m.kind == "photo"]
    if photos:
        with_date = [m for m in photos if m.date_source in ("exif", "name")]
        assert len(with_date) >= 0.9 * len(photos)
    for dup, orig in res.duplicates:
        assert dup.read_bytes() == orig.read_bytes()

    items = build_items(res.media)
    titles = [it for it in items if is_chapter_title(it)]
    months = {m.taken.strftime("%Y-%m") for m in res.media}
    assert {t.chapter for t in titles} == months
    assert len(titles) >= len(months)


@pytest.mark.parametrize("src", SAMPLE_PHOTOS, ids=_ids(SAMPLE_PHOTOS))
def test_real_photo_rotation_and_thumbnail(src: Path, tmp_path: Path) -> None:
    info = photo_info(src)
    img = open_photo(src)
    assert img.size == (info.width, info.height)
    tc = ThumbnailCache(tmp_path)
    try:
        with Image.open(tc.create(src)) as thumb:
            assert (thumb.width > thumb.height) == (info.width > info.height)
    finally:
        tc.shutdown()


@pytest.mark.parametrize("src", REAL_HEIC, ids=_ids(REAL_HEIC))
def test_real_heic(src: Path) -> None:
    m = import_media([src]).media[0]
    assert m.kind == "photo"
    img = open_photo(src)
    assert img.mode == "RGB" and img.size == (m.width, m.height)


@pytest.mark.slow
@pytest.mark.parametrize("src", REAL_VIDEOS, ids=_ids(REAL_VIDEOS))
def test_real_video_normalized(src: Path, tmp_path: Path) -> None:
    before = video_info(src)
    out = normalize_video(src, tmp_path)
    v, a = streams(out)
    assert v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p"
    assert v["r_frame_rate"] == "30/1" and v["avg_frame_rate"] == "30/1"
    assert int(v["height"]) <= 1080
    assert v.get("color_transfer") == "bt709"
    assert v.get("color_transfer") not in ("smpte2084", "arib-std-b67")
    # Hochkant bleibt hochkant
    assert (int(v["height"]) > int(v["width"])) == (before.height > before.width)
    pts = video_pts(out)
    assert all(abs((b - a_) - 1 / 30) < 0.002 for a_, b in zip(pts, pts[1:], strict=False))
    assert abs(float(v["duration"]) - before.duration) < 0.1
    if before.has_audio:
        assert a is not None and a["sample_rate"] == "48000"
        assert abs(float(v["start_time"]) - float(a["start_time"])) < 0.05
        assert abs(float(v["duration"]) - float(a["duration"])) < 0.05
