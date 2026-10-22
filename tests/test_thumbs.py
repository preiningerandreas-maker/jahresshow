from __future__ import annotations

import shutil
import threading
from pathlib import Path

import pytest
from mediacheck import RED, make_jpeg
from PIL import Image

from slideshow.core.normalize import normalize_video
from slideshow.core.thumbs import THUMB_SIZE, ThumbnailCache


@pytest.fixture
def thumbs(umlaut_dir: Path):  # type: ignore[no-untyped-def]
    tc = ThumbnailCache(umlaut_dir / "Cache Ü")
    yield tc
    tc.shutdown()


def test_photo_thumbnail_rotated_and_cached(umlaut_dir: Path, thumbs: ThumbnailCache) -> None:
    src = make_jpeg(umlaut_dir / "Drehung Größe.jpg", orientation=6, size=(800, 400))
    assert thumbs.get(src) is None
    out = thumbs.create(src)
    with Image.open(out) as im:
        assert im.size == (THUMB_SIZE // 2, THUMB_SIZE)  # hochkant nach EXIF-Drehung
        r, g, b = im.convert("RGB").getpixel((im.width // 2, 5))
        assert r > 150 and b < 100 and RED[0] > 150
    assert thumbs.get(src) == out
    mtime = out.stat().st_mtime_ns
    assert thumbs.create(src) == out and out.stat().st_mtime_ns == mtime


@pytest.mark.parametrize(
    ("name", "size"),
    [
        ("Winter Schnee.heic", (320, 240)),
        ("Gedreht EXIF.jpg", (240, 320)),
        ("Video mit Ton.mp4", (320, 180)),
        ("Hochkant Video.mp4", (180, 320)),
        ("HDR Hochkant PQ.mp4", (180, 320)),
    ],
)
def test_thumbnails_for_all_types(synthetic: Path, umlaut_dir: Path, thumbs: ThumbnailCache,
                                  name: str, size: tuple[int, int]) -> None:
    src = umlaut_dir / f"Ä {name}"
    shutil.copy2(synthetic / name, src)
    with Image.open(thumbs.create(src)) as im:
        assert im.size == size


def test_hdr_thumbnail_prefers_normalized(synthetic: Path, umlaut_dir: Path) -> None:
    src = umlaut_dir / "HDR.mp4"
    shutil.copy2(synthetic / "HDR HLG.mp4", src)
    cache = umlaut_dir / "cache"
    normalize_video(src, cache)
    tc = ThumbnailCache(cache)
    try:
        with Image.open(tc.create(src)) as im:
            assert im.size == (320, 180)
    finally:
        tc.shutdown()


def test_lazy_background_request(synthetic: Path, umlaut_dir: Path,
                                 thumbs: ThumbnailCache) -> None:
    srcs = [make_jpeg(umlaut_dir / f"Bild {i} ö.jpg") for i in range(5)]
    done: list[Path] = []
    lock = threading.Lock()

    def cb(src: Path, fut) -> None:  # type: ignore[no-untyped-def]
        with lock:
            done.append(src)

    futures = [thumbs.request(s, cb) for s in srcs]
    futures.append(thumbs.request(srcs[0]))  # doppelte Anfrage teilt sich die Aufgabe
    results = [f.result(timeout=30) for f in futures]
    assert all(p.is_file() for p in results)
    assert results[0] == results[-1]
    thumbs.shutdown()
    assert sorted(done) == sorted(srcs)
