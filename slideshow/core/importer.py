"""Metadaten von Mediendateien (ffprobe). EXIF-Sortierung und Import folgen in M2."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from slideshow.core.ffmpeg import probe

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
MUSIC_EXTS = {".mp3", ".m4a", ".wav", ".flac"}


@dataclass(frozen=True)
class VideoInfo:
    duration: float
    width: int
    height: int
    has_audio: bool


def video_info(path: Path) -> VideoInfo:
    st = path.stat()
    return _video_info(str(path.resolve()), st.st_size, st.st_mtime_ns)


@lru_cache(maxsize=4096)
def _video_info(path: str, size: int, mtime_ns: int) -> VideoInfo:
    data = probe(path)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError(f"Keine Videospur in {path}")
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    w, h = int(video.get("width", 0)), int(video.get("height", 0))
    rotation = _rotation(video)
    if rotation in (90, 270):
        w, h = h, w
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return VideoInfo(duration, w, h, has_audio)


def _rotation(stream: dict) -> int:
    for sd in stream.get("side_data_list", []):
        if "rotation" in sd:
            return abs(int(float(sd["rotation"]))) % 360
    rot = stream.get("tags", {}).get("rotate")
    return abs(int(rot)) % 360 if rot else 0


def clip_length(path: Path) -> float:
    return video_info(path).duration
