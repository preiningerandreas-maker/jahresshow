"""Musikspur: Playlist hintereinander, Schleife falls zu kurz, Fade-out am Ende,
Absenken während Clips mit Originalton."""

from __future__ import annotations

import threading
from pathlib import Path

from slideshow.core.ffmpeg import ProgressCallback, run_ffmpeg
from slideshow.core.importer import video_info
from slideshow.core.project import FPS, Project, TimelineEntry
from slideshow.render.cache import (
    cleanup_tmp,
    commit_tmp,
    file_fingerprint,
    make_key,
    tmp_path,
)

SAMPLE_RATE = 48000
DUCK_RAMP = 0.5  # Sekunden für das Absenken/Anheben der Musik


def duck_ranges(project: Project, timeline: list[TimelineEntry]) -> list[tuple[float, float]]:
    """Zeitbereiche (Sekunden), in denen ein Clip mit Originalton läuft."""
    ranges = []
    for e in timeline:
        item = e.item
        if item.kind != "video" or not item.audio or item.volume <= 0:
            continue
        assert item.path is not None
        if video_info(project.resolve(item.path)).has_audio:
            ranges.append((e.start_frame / FPS, (e.start_frame + e.frames) / FPS))
    return ranges


def duck_expression(ranges: list[tuple[float, float]], duck: float, ramp: float = DUCK_RAMP) -> str:
    """FFmpeg-Ausdruck für die Musiklautstärke über die Zeit t."""
    if not ranges:
        return "1"
    terms = [
        f"clip(min((t-{a - ramp:.3f})/{ramp},({b + ramp:.3f}-t)/{ramp}),0,1)" for a, b in ranges
    ]
    return f"1-{1 - duck:.3f}*clip({'+'.join(terms)},0,1)"


def trim_filter(start: float, end: float | None) -> str:
    """Filterkette (mit Komma am Ende), die ein Stück auf [start, end] kürzt, sonst leer."""
    if start <= 0 and end is None:
        return ""
    parts = [f"start={start:.3f}"] if start > 0 else []
    if end is not None:
        parts.append(f"end={end:.3f}")
    return f"atrim={':'.join(parts)},asetpts=PTS-STARTPTS,"


def build_music(
    project: Project,
    total_seconds: float,
    ranges: list[tuple[float, float]],
    cache_dir: Path,
    *,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Path | None:
    """Erzeugt die fertige Musikspur als WAV (gecacht). None, wenn keine Musik gewählt ist."""
    tracks = [project.resolve(t) for t in project.music.tracks]
    if not tracks:
        return None
    music_dir = cache_dir / "music"
    music_dir.mkdir(parents=True, exist_ok=True)
    fps = [file_fingerprint(t) for t in tracks]
    trims = [project.music.trim_of(t) for t in project.music.tracks]
    # Ohne Ausschnitt bleibt der Schlüssel wie bisher, alte Zwischenstände gelten weiter
    pkey: dict = {"type": "playlist", "tracks": fps}
    if any(t != (0.0, None) for t in trims):
        pkey["trims"] = [list(t) for t in trims]

    playlist = music_dir / f"{make_key(pkey)}.wav"
    if not playlist.is_file():
        args: list[str] = []
        chain = []
        for i, t in enumerate(tracks):
            args += ["-i", str(t)]
            chain.append(
                f"[{i}:a]{trim_filter(*trims[i])}aresample={SAMPLE_RATE},"
                f"aformat=sample_fmts=s16:channel_layouts=stereo[a{i}]"
            )
        joined = "".join(f"[a{i}]" for i in range(len(tracks)))
        chain.append(f"{joined}concat=n={len(tracks)}:v=0:a=1[m]")
        tmp = tmp_path(playlist)
        try:
            run_ffmpeg([*args, "-filter_complex", ";".join(chain), "-map", "[m]",
                        "-c:a", "pcm_s16le", str(tmp)], cancel=cancel)
            commit_tmp(tmp, playlist)
        finally:
            cleanup_tmp(tmp)

    m = project.music
    key = make_key({
        "type": "music", "tracks": fps, "total": round(total_seconds, 4),
        **({"trims": pkey["trims"]} if "trims" in pkey else {}),
        "ranges": [[round(a, 4), round(b, 4)] for a, b in ranges],
        "volume": m.volume, "duck": m.duck_volume, "fade": m.fade_out,
    })
    out = music_dir / f"{key}.wav"
    if out.is_file():
        return out
    fade = min(m.fade_out, total_seconds)
    samples = round(total_seconds * SAMPLE_RATE)
    filt = (
        f"[0:a]atrim=end_sample={samples},asetpts=N/SR/TB,volume={m.volume:.3f},"
        f"volume=eval=frame:volume='{duck_expression(ranges, m.duck_volume)}',"
        f"afade=t=out:st={total_seconds - fade:.3f}:d={fade:.3f},apad,"
        f"atrim=end_sample={samples}[m]"
    )
    tmp = tmp_path(out)
    try:
        # Schleife: Playlist endlos wiederholen und auf Showlänge schneiden
        run_ffmpeg(["-stream_loop", "-1", "-i", str(playlist), "-filter_complex", filt,
                    "-map", "[m]", "-c:a", "pcm_s16le", str(tmp)],
                   progress=progress, cancel=cancel)
        commit_tmp(tmp, out)
    finally:
        cleanup_tmp(tmp)
    return out
