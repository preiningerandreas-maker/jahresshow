"""Vorschaubilder für die Zeitleiste, dauerhaft im Cache. Erzeugt werden sie erst bei Bedarf
(lazy) in wenigen Hintergrund-Threads, damit die Zeitleiste auch bei 1000 Elementen flüssig
bleibt."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps

from slideshow.core.ffmpeg import run_ffmpeg
from slideshow.core.importer import media_kind, video_info
from slideshow.core.normalize import cached_normalized, tonemap_filter
from slideshow.render.cache import (
    cleanup_tmp,
    default_cache_dir,
    file_fingerprint,
    make_key,
    tmp_path,
)

THUMB_SIZE = 320  # längere Kante in Pixeln
THUMB_VERSION = 1


def thumb_path(src: Path, cache_dir: Path, size: int = THUMB_SIZE) -> Path:
    key = make_key({"type": "thumb", "version": THUMB_VERSION, "size": size,
                    "source": file_fingerprint(src)})
    return cache_dir / "thumbs" / f"{key}.jpg"


def make_photo_thumb(src: Path, out: Path, size: int = THUMB_SIZE) -> None:
    with Image.open(src) as im:
        im.draft("RGB", (size, size))  # JPEG direkt verkleinert dekodieren: deutlich schneller
        img = ImageOps.exif_transpose(im).convert("RGB")
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    img.save(out, "JPEG", quality=85)


def make_video_thumb(src: Path, out: Path, size: int = THUMB_SIZE,
                     cache_dir: Path | None = None) -> None:
    normalized = cached_normalized(src, cache_dir)
    source = normalized or src
    info = video_info(source)
    at = min(1.0, info.duration / 3)
    vf = f"scale={size}:{size}:force_original_aspect_ratio=decrease"
    if info.hdr:
        vf = f"{vf},{tonemap_filter(info)}"
    vf = f"{vf},scale=out_range=pc,format=yuvj420p"
    run_ffmpeg(["-ss", f"{at:.3f}", "-i", str(source), "-frames:v", "1", "-vf", vf,
                "-q:v", "3", "-update", "1", str(out)])


class ThumbnailCache:
    """Vorschaubilder holen oder im Hintergrund erzeugen."""

    def __init__(self, cache_dir: Path | None = None, size: int = THUMB_SIZE,
                 workers: int = 2) -> None:
        self.cache_dir = cache_dir or default_cache_dir()
        self.size = size
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="thumb")
        self._pending: dict[Path, Future[Path]] = {}
        self._lock = threading.Lock()

    def path_for(self, src: Path) -> Path:
        return thumb_path(src, self.cache_dir, self.size)

    def get(self, src: Path) -> Path | None:
        """Vorschaubild, falls schon vorhanden – sonst None (dann request() aufrufen)."""
        p = self.path_for(src)
        return p if p.is_file() else None

    def create(self, src: Path) -> Path:
        """Vorschaubild sofort erzeugen (blockiert)."""
        final = self.path_for(src)
        if final.is_file():
            return final
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp = tmp_path(final)
        try:
            if media_kind(src) == "video":
                make_video_thumb(src, tmp, self.size, self.cache_dir)
            else:
                make_photo_thumb(src, tmp, self.size)
            tmp.replace(final)
        finally:
            cleanup_tmp(tmp)
        return final

    def request(self, src: Path,
                callback: Callable[[Path, Future[Path]], None] | None = None) -> Future[Path]:
        """Vorschaubild im Hintergrund erzeugen. Mehrfache Anfragen teilen sich eine Aufgabe.
        Der Rückruf läuft im Hintergrund-Thread."""
        with self._lock:
            fut = self._pending.get(src)
            if fut is None:
                fut = self._pool.submit(self.create, src)
                self._pending[src] = fut
                fut.add_done_callback(lambda _f, s=src: self._forget(s))
        if callback is not None:
            fut.add_done_callback(lambda f, s=src: callback(s, f))
        return fut

    def _forget(self, src: Path) -> None:
        with self._lock:
            self._pending.pop(src, None)

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=True)
