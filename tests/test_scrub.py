"""Daten für die Schnitt-Leiste: Bilderreihe, genaues Bild, Klangbild."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from slideshow.core.importer import video_info
from slideshow.core.scrub import (
    FRAME_RATE,
    PCM_BYTES_PER_SECOND,
    cached_frames,
    cached_pcm,
    exact_frame,
    frame_index,
    make_frames,
    make_pcm,
    pcm_seconds,
    pcm_slice,
)


def test_frame_index_nearest_and_clamped() -> None:
    assert frame_index(0.0, 10) == 0
    assert frame_index(1.0, 10) == FRAME_RATE
    assert frame_index(0.3, 10) == round(0.3 * FRAME_RATE)
    assert frame_index(99.0, 10) == 9  # hinter dem Ende: letztes Bild
    assert frame_index(-1.0, 10) == 0
    assert frame_index(1.0, 0) == 0


def test_frames_made_once_and_cached(synthetic: Path, tmp_path: Path) -> None:
    clip = synthetic / "Video mit Ton.mp4"
    assert cached_frames(clip, tmp_path) is None
    frames = make_frames(clip, tmp_path)
    length = video_info(clip).duration
    assert abs(len(frames) - length * FRAME_RATE) <= 2
    with Image.open(frames[0]) as im:
        assert im.width == 320
    assert cached_frames(clip, tmp_path) == frames
    assert make_frames(clip, tmp_path) == frames  # zweiter Aufruf aus dem Cache
    assert not [p for p in (tmp_path / "scrub").iterdir() if ".part" in p.name]


def test_frames_for_hdr_and_umlaut_paths(synthetic: Path, umlaut_dir: Path,
                                         tmp_path: Path) -> None:
    hdr = synthetic / "HDR HLG.mp4"
    frames = make_frames(hdr, tmp_path)
    assert frames and frames[0].suffix == ".jpg"


def test_exact_frame(synthetic: Path, tmp_path: Path) -> None:
    clip = synthetic / "Video mit Ton.mp4"
    out = tmp_path / "bild Ü.jpg"
    exact_frame(clip, 1.0, out, tmp_path)
    with Image.open(out) as im:
        assert im.width == 640


def test_pcm_slice_matches_position(synthetic: Path, tmp_path: Path) -> None:
    track = synthetic / "Musik 1 – Frühling.mp3"
    assert cached_pcm(track, tmp_path) is None
    pcm = make_pcm(track, tmp_path)
    assert cached_pcm(track, tmp_path) == pcm
    seconds = pcm_seconds(pcm)
    assert seconds > 2
    chunk = pcm_slice(pcm, 1.0, 1.5)
    assert len(chunk) == int(1.5 * PCM_BYTES_PER_SECOND)
    assert len(chunk) % 2 == 0
    # Gleiche Stelle zweimal gelesen = gleiche Daten, andere Stelle = andere Daten
    assert pcm_slice(pcm, 1.0, 0.1) == chunk[: int(0.1 * PCM_BYTES_PER_SECOND) // 2 * 2]
    assert pcm_slice(pcm, 0.0, 0.1) != pcm_slice(pcm, 2.0, 0.1)
    # Hinter dem Ende: leer; am Ende: kürzer als verlangt
    assert pcm_slice(pcm, seconds + 5, 1.0) == b""
    assert 0 < len(pcm_slice(pcm, seconds - 0.5, 1.5)) < 1.5 * PCM_BYTES_PER_SECOND


def test_pcm_missing_file_raises(tmp_path: Path) -> None:
    from slideshow.core.ffmpeg import FFmpegError

    with pytest.raises((FFmpegError, OSError)):
        make_pcm(tmp_path / "gibt es nicht.mp3", tmp_path)
