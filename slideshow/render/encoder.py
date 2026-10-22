"""Encoder-Erkennung: welcher H.264-Kodierer (Grafikkarte oder Prozessor) funktioniert hier?"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from slideshow.core.ffmpeg import CREATIONFLAGS, ffmpeg_path
from slideshow.render.cache import atomic_write_text

ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf", "libx264")

LABELS = {
    "h264_nvenc": "NVIDIA-Grafikkarte",
    "h264_qsv": "Intel-Grafik (Quick Sync)",
    "h264_amf": "AMD-Grafikkarte",
    "libx264": "Prozessor (langsamer, funktioniert immer)",
}

MAX_BITRATE_1080 = 8_000_000  # 1 h Show bleibt unter 4 GB (FAT32)


def bitrates(height: int, *, intermediate: bool = False) -> tuple[int, int]:
    """(Zielbitrate, Maximalbitrate) in bit/s für eine Ausgabehöhe."""
    if intermediate:
        # Zwischendateien werden noch einmal kodiert: großzügige Qualität
        top = 24_000_000 if height <= 1080 else 60_000_000
        return (top * 3 // 4, top)
    if height <= 480:
        return (1_500_000, 2_500_000)
    if height <= 1080:
        return (6_000_000, MAX_BITRATE_1080)
    return (16_000_000, 20_000_000)


def encoder_args(encoder: str, height: int, *, intermediate: bool = False) -> list[str]:
    """Kodier-Argumente für ffmpeg: H.264 High, yuv420p, konstante 30 fps, GOP 2 s."""
    target, peak = bitrates(height, intermediate=intermediate)
    level = "4.1" if height <= 1080 else "5.1"
    rate = ["-b:v", str(target), "-maxrate", str(peak), "-bufsize", str(peak * 2)]
    common = ["-g", "60", "-pix_fmt", "yuv420p", "-color_primaries", "bt709",
              "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]
    if encoder == "libx264":
        preset = "veryfast" if intermediate or height <= 480 else "fast"
        crf = "16" if intermediate else "20"
        return ["-c:v", "libx264", "-preset", preset, "-crf", crf,
                "-maxrate", str(peak), "-bufsize", str(peak * 2),
                "-profile:v", "high", "-level:v", level, *common]
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19",
                *rate, "-profile:v", "high", "-level:v", level, *common]
    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "medium", *rate,
                "-profile:v", "high", "-level", level.replace(".", ""), *common]
    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "vbr_peak", *rate,
                "-profile:v", "high", "-level", level, *common]
    raise ValueError(f"Unbekannter Kodierer: {encoder!r}")


def probe_encoder(encoder: str) -> bool:
    """Probe-Encode: 1 s Testbild in 1080p."""
    cmd = [str(ffmpeg_path()), "-hide_banner", "-nostdin", "-loglevel", "error",
           "-f", "lavfi", "-i", "testsrc2=duration=1:size=1920x1080:rate=30",
           *encoder_args(encoder, 1080), "-f", "null", "-"]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=60,
                             creationflags=CREATIONFLAGS, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return res.returncode == 0


def _ffmpeg_key() -> str:
    p = ffmpeg_path()
    st = p.stat()
    return f"{p.resolve()}|{st.st_size}|{st.st_mtime_ns}"


def detect_encoder(cache_dir: Path | None = None, forced: str | None = None) -> str:
    """Erster funktionierender Kodierer in fester Reihenfolge; Ergebnis wird gecacht."""
    if forced is not None and forced not in ENCODERS:
        raise ValueError(f"Unbekannter Kodierer {forced!r}. Möglich: {', '.join(ENCODERS)}")

    cache_file = cache_dir / "encoder.json" if cache_dir else None
    key = _ffmpeg_key()
    results: dict[str, bool] = {}
    if cache_file and cache_file.is_file():
        try:
            data = json.loads(cache_file.read_text("utf-8"))
            if data.get("ffmpeg") == key:
                results = {k: bool(v) for k, v in data.get("results", {}).items()}
        except (OSError, ValueError):
            pass

    def works(encoder: str) -> bool:
        if encoder not in results:
            results[encoder] = probe_encoder(encoder)
        return results[encoder]

    if forced:
        chosen: str | None = forced if works(forced) else None
        if chosen is None:
            raise RuntimeError(f"Der Kodierer {forced} ({LABELS[forced]}) funktioniert hier nicht.")
    else:
        chosen = next((e for e in ENCODERS if works(e)), None)
        if chosen is None:
            raise RuntimeError("Kein Videokodierer funktioniert – ist ffmpeg.exe vollständig?")
    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(cache_file, json.dumps({"ffmpeg": key, "results": results}))
    return chosen
