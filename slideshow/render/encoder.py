"""Encoder-Erkennung: welcher H.264-Kodierer (Grafikkarte oder Prozessor) funktioniert hier?"""

from __future__ import annotations

import contextlib
import json
import logging
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from slideshow.core.ffmpeg import CREATIONFLAGS, ffmpeg_path
from slideshow.render.cache import atomic_write_text

log = logging.getLogger(__name__)

ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf", "libx264")
CPU_ENCODER = "libx264"

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


def is_hardware(encoder: str) -> bool:
    return encoder != CPU_ENCODER


# Probe-Encode: mehrere Sekunden wechselnde Inhalte, wie sie im Export vorkommen (bewegtes
# Bild, Überblendung, wiederholtes Standbild wie bei Fotos, starkes Rauschen mit hoher
# Bitrate), mit dem Bildformat der Segmente. Erhöhen, wenn er sich ändert: Alte Ergebnisse
# in encoder.json gelten dann nicht mehr.
PROBE_VERSION = 2
PROBE_SECONDS = 4
PROBE_SOURCE = (
    "testsrc2=s=1920x1080:r=30:d=1.5,format=yuv420p,settb=1/30[a];"
    "color=c=0x204060:s=1920x1080:r=30:d=1.5,format=yuv420p,settb=1/30[b];"
    "[a][b]xfade=transition=fade:duration=0.5:offset=1[ab];"
    "testsrc2=s=1920x1080:r=30:d=0.1,format=yuv420p,"
    "loop=loop=29:size=1:start=0,settb=1/30,setpts=N[c];"
    "testsrc2=s=1920x1080:r=30:d=1,noise=alls=80:allf=t,format=yuv420p,settb=1/30[d];"
    "[ab][c][d]concat=n=3:v=1:a=0,"
    "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv,"
    "setsar=1,split=2[seg][fin]"
)


def probe_command(encoder: str, workdir: Path) -> list[str]:
    """Kodiert die Probe zweimal in echte MP4-Dateien: mit den Parametern der Segmente
    (Zwischendateien) und mit denen der fertigen Show."""
    return [str(ffmpeg_path()), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-filter_complex", PROBE_SOURCE,
            "-map", "[seg]", "-frames:v", str(PROBE_SECONDS * 30),
            *encoder_args(encoder, 1080, intermediate=True), str(workdir / "segment.mp4"),
            "-map", "[fin]", "-frames:v", str(PROBE_SECONDS * 30),
            *encoder_args(encoder, 1080), "-movflags", "+faststart",
            str(workdir / "show.mp4")]


def probe_encoder(encoder: str) -> bool:
    with tempfile.TemporaryDirectory(prefix="flashback-probe-") as tmp:
        try:
            res = subprocess.run(probe_command(encoder, Path(tmp)), capture_output=True,
                                 timeout=180, creationflags=CREATIONFLAGS, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return False
        if res.returncode != 0:
            log.info("Probe-Encode mit %s fehlgeschlagen: %s", encoder,
                     res.stderr.decode("utf-8", "replace").strip()[-500:])
        return res.returncode == 0


def _ffmpeg_key() -> str:
    p = ffmpeg_path()
    st = p.stat()
    return f"{p.resolve()}|{st.st_size}|{st.st_mtime_ns}|probe{PROBE_VERSION}"


# --- Einstellung „Grafikkarte verwenden“ und gemerkte Pannen ------------------------------

GPU_AUTO = "auto"
GPU_NEVER = "never"
GPU_MODES = {GPU_AUTO: "Automatisch", GPU_NEVER: "Nie (nur Prozessor)"}
PREFS_FILE = "grafikkarte.json"


@dataclass
class GpuPrefs:
    mode: str = GPU_AUTO
    # Kodierer, die auf diesem Gerät beim Kodieren versagt haben -> Datum (JJJJ-MM-TT).
    # Ein bestandener Probe-Encode hebt das nicht auf: Der hatte vorher ja auch bestanden.
    failed: dict[str, str] = field(default_factory=dict)


def load_prefs(cache_dir: Path | None) -> GpuPrefs:
    if cache_dir is None:
        return GpuPrefs()
    try:
        data = json.loads((cache_dir / PREFS_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return GpuPrefs()
    mode = data.get("mode") if data.get("mode") in GPU_MODES else GPU_AUTO
    failed = {k: str(v) for k, v in dict(data.get("failed") or {}).items() if k in ENCODERS}
    return GpuPrefs(mode, failed)


def save_prefs(cache_dir: Path, prefs: GpuPrefs) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(cache_dir / PREFS_FILE,
                      json.dumps({"mode": prefs.mode, "failed": prefs.failed}, indent=1))


def set_gpu_mode(cache_dir: Path, mode: str) -> None:
    if mode not in GPU_MODES:
        raise ValueError(f"Unbekannte Einstellung {mode!r}")
    prefs = load_prefs(cache_dir)
    prefs.mode = mode
    save_prefs(cache_dir, prefs)


def remember_failure(cache_dir: Path, encoder: str) -> None:
    """Der Kodierer hat beim echten Kodieren versagt: künftig gleich den Prozessor nehmen."""
    prefs = load_prefs(cache_dir)
    prefs.failed[encoder] = date.today().isoformat()
    save_prefs(cache_dir, prefs)


def forget_failures(cache_dir: Path) -> None:
    prefs = load_prefs(cache_dir)
    prefs.failed = {}
    save_prefs(cache_dir, prefs)


def failed_text(prefs: GpuPrefs) -> str:
    """„Intel-Grafik (Quick Sync) am 23.10.2026“"""
    parts = []
    for enc, day in prefs.failed.items():
        with contextlib.suppress(ValueError):
            day = date.fromisoformat(day).strftime("%d.%m.%Y")
        parts.append(f"{LABELS[enc]} am {day}")
    return ", ".join(parts)


@dataclass(frozen=True)
class EncoderChoice:
    name: str
    note: str | None = None  # Hinweis für die Export-Zusammenfassung


def choose_encoder(cache_dir: Path | None, forced: str | None = None) -> EncoderChoice:
    """Kodierer für einen Lauf: erzwungener, sonst nach Einstellung und gemerkten Pannen."""
    if forced is not None:
        return EncoderChoice(detect_encoder(cache_dir, forced))
    prefs = load_prefs(cache_dir)
    if prefs.mode == GPU_NEVER:
        return EncoderChoice(CPU_ENCODER)
    chosen = detect_encoder(cache_dir, skip=prefs.failed)
    if prefs.failed and detect_encoder(cache_dir) in prefs.failed:
        return EncoderChoice(chosen, (
            f"Die Grafikkarte hat bei einem früheren Export versagt ({failed_text(prefs)}). "
            "Deshalb rechnet jetzt gleich der Prozessor, das dauert länger. Unter „Extras → "
            "Grafikkarte verwenden“ lässt sich die Grafikkarte wieder erlauben."))
    return EncoderChoice(chosen)


def detect_encoder(cache_dir: Path | None = None, forced: str | None = None, *,
                   skip: Iterable[str] = ()) -> str:
    """Erster funktionierender Kodierer in fester Reihenfolge; Ergebnis wird gecacht.
    skip: diese Kodierer nicht verwenden (haben auf diesem Gerät schon versagt)."""
    if forced is not None and forced not in ENCODERS:
        raise ValueError(f"Unbekannter Kodierer {forced!r}. Möglich: {', '.join(ENCODERS)}")
    skip = set(skip) - {CPU_ENCODER}

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
        chosen = next((e for e in ENCODERS if e not in skip and works(e)), None)
        if chosen is None:
            raise RuntimeError("Kein Videokodierer funktioniert – ist ffmpeg.exe vollständig?")
    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(cache_file, json.dumps({"ffmpeg": key, "results": results}))
    return chosen
