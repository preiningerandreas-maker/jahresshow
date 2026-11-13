"""Daten für die Schnitt-Leiste: Live-Bild beim Ziehen (Video) und Vorhören (Musik).

Ein ffmpeg-Aufruf pro Mausbewegung wäre auf schwachen Geräten (ThinkPad, HEVC) zu langsam.
Darum wird pro Datei einmal im Hintergrund eine kleine Datenmenge erzeugt und im Cache
abgelegt; beim Ziehen wird nur noch daraus gelesen:
- Video: eine Bilderreihe (FRAME_RATE Bilder pro Sekunde, kleine JPEGs)
- Musik: ein kleines Klangbild (mono, 22 kHz, 16 Bit), aus dem sich kurze Stücke an jeder
  Stelle sofort herauslesen lassen
"""

from __future__ import annotations

import shutil
from pathlib import Path

from slideshow.core.ffmpeg import run_ffmpeg
from slideshow.core.importer import video_info
from slideshow.core.normalize import cached_normalized, tonemap_filter
from slideshow.render.cache import (
    cleanup_tmp,
    commit_tmp,
    file_fingerprint,
    make_key,
    tmp_path,
)

FRAME_RATE = 2  # Bilder pro Sekunde in der Bilderreihe
FRAME_WIDTH = 320
EXACT_WIDTH = 640  # das genaue Bild beim Loslassen
SCRUB_VERSION = 1
PCM_RATE = 22050
PCM_BYTES_PER_SECOND = PCM_RATE * 2  # mono, 16 Bit
SNIPPET_SECONDS = 1.5  # so lange wird beim Ziehen vorgehört


def _key(kind: str, src: Path, **extra: object) -> str:
    return make_key({"type": kind, "version": SCRUB_VERSION, "source": file_fingerprint(src),
                     **extra})


def frames_dir(src: Path, cache_dir: Path) -> Path:
    return cache_dir / "scrub" / f"frames-{_key('frames', src)}"


def cached_frames(src: Path, cache_dir: Path) -> list[Path] | None:
    """Fertige Bilderreihe (sortiert), falls schon erzeugt."""
    try:
        folder = frames_dir(src, cache_dir)
    except OSError:
        return None
    if not (folder / "done").is_file():
        return None
    return sorted(folder.glob("*.jpg"))


def make_frames(src: Path, cache_dir: Path) -> list[Path]:
    """Bilderreihe erzeugen (aus dem umgewandelten Arbeitsvideo, falls vorhanden)."""
    done = cached_frames(src, cache_dir)
    if done is not None:
        return done
    folder = frames_dir(src, cache_dir)
    folder.parent.mkdir(parents=True, exist_ok=True)
    work = folder.with_name(tmp_path(folder).name)
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        source = cached_normalized(src, cache_dir) or src
        vf = f"fps={FRAME_RATE},scale={FRAME_WIDTH}:-2"
        info = video_info(source)
        if info.hdr:
            vf = f"fps={FRAME_RATE},{tonemap_filter(info)},scale={FRAME_WIDTH}:-2"
        vf = f"{vf},scale=out_range=pc,format=yuvj420p"
        run_ffmpeg(["-i", str(source), "-an", "-vf", vf, "-q:v", "5",
                    str(work / "%06d.jpg")])
        (work / "done").write_bytes(b"")
        try:
            work.replace(folder)
        except OSError:
            if not (folder / "done").is_file():
                raise
            shutil.rmtree(work, ignore_errors=True)  # anderer Lauf war schneller
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return sorted(folder.glob("*.jpg"))


def frame_index(t: float, count: int) -> int:
    """Nummer des Bildes der Reihe, das zur Zeit t (Sekunden) am nächsten liegt."""
    if count <= 0:
        return 0
    return min(max(round(t * FRAME_RATE), 0), count - 1)


def exact_frame(src: Path, t: float, out: Path, cache_dir: Path | None = None) -> None:
    """Das genaue Bild bei t Sekunden (für den Moment, in dem der Schieber losgelassen wird)."""
    source = (cached_normalized(src, cache_dir) if cache_dir else None) or src
    info = video_info(source)
    vf = f"scale={EXACT_WIDTH}:-2"
    if info.hdr:
        vf = f"{tonemap_filter(info)},{vf}"
    vf = f"{vf},scale=out_range=pc,format=yuvj420p"
    run_ffmpeg(["-ss", f"{max(t, 0.0):.3f}", "-i", str(source), "-frames:v", "1", "-an",
                "-vf", vf, "-q:v", "3", "-update", "1", str(out)])


def pcm_path(src: Path, cache_dir: Path) -> Path:
    return cache_dir / "scrub" / f"audio-{_key('pcm', src)}.pcm"


def cached_pcm(src: Path, cache_dir: Path) -> Path | None:
    try:
        p = pcm_path(src, cache_dir)
    except OSError:
        return None
    return p if p.is_file() else None


def make_pcm(src: Path, cache_dir: Path) -> Path:
    """Klangbild erzeugen (ohne Kopf, reine 16-Bit-Werte)."""
    done = cached_pcm(src, cache_dir)
    if done is not None:
        return done
    final = pcm_path(src, cache_dir)
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = tmp_path(final)
    try:
        run_ffmpeg(["-i", str(src), "-vn", "-ac", "1", "-ar", str(PCM_RATE),
                    "-f", "s16le", str(tmp)])
        commit_tmp(tmp, final)
    except BaseException:
        cleanup_tmp(tmp)
        raise
    return final


def pcm_seconds(pcm: Path) -> float:
    return pcm.stat().st_size / PCM_BYTES_PER_SECOND


def pcm_slice(pcm: Path, t: float, seconds: float = SNIPPET_SECONDS) -> bytes:
    """Kurzes Stück ab der Stelle t (Sekunden); leer, wenn t hinter dem Ende liegt."""
    offset = max(int(max(t, 0.0) * PCM_RATE), 0) * 2
    with pcm.open("rb") as f:
        f.seek(offset)
        data = f.read(int(seconds * PCM_BYTES_PER_SECOND) // 2 * 2)
    return data

