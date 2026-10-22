"""Videos einmalig ins Arbeitsformat umwandeln: Höhe max. 1080, 30 fps konstant, SDR yuv420p
H.264 (bt709), Ton AAC 48 kHz. HDR (PQ/HLG) wird per zscale + tonemap=hable nach bt709 gebracht.

Das Ergebnis liegt dauerhaft im Cache (Schlüssel = Quelldatei + Einstellungen), auf langsamen
Geräten läuft die Umwandlung im Hintergrund mit Fortschritt und Restzeit.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from slideshow.core.ffmpeg import Cancelled, FFmpegError, run_ffmpeg
from slideshow.core.importer import VideoInfo, video_info
from slideshow.core.project import FPS
from slideshow.render.cache import (
    cleanup_tmp,
    default_cache_dir,
    file_fingerprint,
    make_key,
    tmp_path,
)

# Erhöhen, wenn sich das Arbeitsformat ändert (alte Dateien im Cache werden dann ungültig)
NORMALIZE_VERSION = 2
MAX_HEIGHT = 1080
SAMPLE_RATE = 48000
AUDIO_BITRATE = "192k"

_ZSCALE_TRANSFER = {"smpte2084": "smpte2084", "arib-std-b67": "arib-std-b67"}
_ZSCALE_PRIMARIES = {"bt2020": "bt2020", "bt709": "bt709"}
_ZSCALE_MATRIX = {"bt2020nc": "bt2020nc", "bt2020c": "bt2020c", "bt709": "bt709"}


@dataclass(frozen=True)
class NormalizeProgress:
    """Fortschritt einer Umwandlung, Zeiten in Sekunden."""

    done: float  # bereits umgewandelte Videolänge
    total: float  # gesamte Videolänge
    elapsed: float  # bisher benötigte Rechenzeit

    @property
    def fraction(self) -> float:
        return min(1.0, self.done / self.total) if self.total > 0 else 0.0

    @property
    def eta(self) -> float | None:
        """Geschätzte Restzeit, None solange noch keine Schätzung möglich ist."""
        if self.fraction <= 0.01 or self.elapsed <= 0:
            return None
        return self.elapsed / self.fraction * (1 - self.fraction)


NormalizeCallback = Callable[[NormalizeProgress], None]


def tonemap_filter(info: VideoInfo) -> str:
    """HDR → SDR bt709: linearisieren, hable-Tonemapping, zurück nach bt709 begrenzter Bereich."""
    tin = _ZSCALE_TRANSFER.get(info.transfer, "smpte2084")
    pin = _ZSCALE_PRIMARIES.get(info.color_primaries, "bt2020")
    min_ = _ZSCALE_MATRIX.get(info.color_space, "bt2020nc")
    rin = "pc" if info.color_range == "pc" else "tv"
    return (
        f"zscale=tin={tin}:pin={pin}:min={min_}:rin={rin}:t=linear:npl=100,"
        "format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
        "zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
    )


def to_bt709_scale(assume_bt709: bool = False) -> str:
    """Nach 8 Bit yuv420p bt709 (begrenzter Bereich) umrechnen."""
    src = "in_color_matrix=bt709:in_range=tv:" if assume_bt709 else ""
    return f"scale={src}out_color_matrix=bt709:out_range=tv,format=yuv420p"


def video_filter(info: VideoInfo) -> str:
    # Erst Bildrate und Größe reduzieren, dann das teure Tonemapping auf weniger Pixeln
    parts = [f"fps={FPS}:start_time=0"]
    if info.height > MAX_HEIGHT:
        parts.append(f"scale=-2:{MAX_HEIGHT}:flags=bicubic")
    else:
        parts.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    if info.hdr:
        parts.append(tonemap_filter(info))
    else:
        # 8 und 10 Bit SDR → 8 Bit bt709; ohne Farbangaben bt709 annehmen (nicht bt601)
        parts.append(to_bt709_scale(info.color_tags_missing))
    # Farbangaben fest auf bt709 setzen, sonst bleiben sie bei vielen Handyvideos "unbekannt"
    parts.append("setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv")
    parts.append("setsar=1")
    return ",".join(parts)


def normalize_key(src: Path) -> str:
    return make_key({"type": "normalize", "version": NORMALIZE_VERSION,
                     "source": file_fingerprint(src)})


def normalized_path(src: Path, cache_dir: Path | None = None) -> Path:
    """Zielpfad im Cache (die Datei existiert erst nach normalize_video)."""
    cache_dir = cache_dir or default_cache_dir()
    return cache_dir / "normalized" / f"{normalize_key(src)}.mp4"


def cached_normalized(src: Path, cache_dir: Path | None = None) -> Path | None:
    """Fertig umgewandelte Datei, falls schon vorhanden."""
    try:
        p = normalized_path(src, cache_dir)
    except OSError:
        return None
    return p if p.is_file() else None


def build_args(src: Path, info: VideoInfo, out: Path, encoder: str, *,
               hwaccel: bool) -> list[str]:
    from slideshow.render.encoder import encoder_args

    args: list[str] = []
    if hwaccel:
        # Hardware-Dekodierung, falls verfügbar; FFmpeg fällt sonst selbst auf die CPU zurück
        args += ["-hwaccel", "auto"]
    args += ["-i", str(src), "-map", "0:v:0"]
    if info.has_audio:
        args += ["-map", "0:a:0"]
    args += ["-vf", video_filter(info), "-fps_mode", "cfr", "-r", str(FPS),
             *encoder_args(encoder, MAX_HEIGHT, intermediate=True)]
    if info.has_audio:
        args += ["-af", f"aresample={SAMPLE_RATE}:async=1:first_pts=0",
                 "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", str(SAMPLE_RATE), "-ac", "2"]
    args += ["-map_metadata", "-1", "-movflags", "+faststart", str(out)]
    return args


def normalize_video(
    src: Path,
    cache_dir: Path | None = None,
    *,
    encoder: str = "libx264",
    hwaccel: bool = True,
    progress: NormalizeCallback | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Wandelt ein Video ins Arbeitsformat um oder liefert die Datei aus dem Cache."""
    final = normalized_path(src, cache_dir)
    if final.is_file():
        return final
    final.parent.mkdir(parents=True, exist_ok=True)
    info = video_info(src)
    start = time.monotonic()

    furthest = [0.0]

    def on_progress(done: float) -> None:
        # FFmpeg meldet die Zeit je Spur; für die Anzeige nur vorwärts zählen
        furthest[0] = max(furthest[0], min(done, info.duration))
        if progress is not None:
            progress(NormalizeProgress(furthest[0], info.duration, time.monotonic() - start))

    tmp = tmp_path(final)
    # Hardware kann das Format nicht? Dann über die CPU, zuletzt mit libx264
    attempts = [(hw, enc) for hw in ((True, False) if hwaccel else (False,))
                for enc in dict.fromkeys((encoder, "libx264"))]
    try:
        for i, (hw, enc) in enumerate(attempts):
            try:
                run_ffmpeg(build_args(src, info, tmp, enc, hwaccel=hw),
                           progress=on_progress, cancel=cancel)
                break
            except FFmpegError:
                cleanup_tmp(tmp)
                if i == len(attempts) - 1:
                    raise
        tmp.replace(final)
    finally:
        cleanup_tmp(tmp)
    if progress is not None:
        progress(NormalizeProgress(info.duration, info.duration, time.monotonic() - start))
    return final


class BackgroundNormalizer:
    """Wandelt Videos nacheinander in einem Hintergrund-Thread um.

    Die Rückrufe laufen im Hintergrund-Thread; die GUI muss sie selbst in den
    Hauptthread weiterreichen.
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        *,
        encoder: str = "libx264",
        on_progress: Callable[[Path, NormalizeProgress], None] | None = None,
        on_done: Callable[[Path, Path], None] | None = None,
        on_error: Callable[[Path, Exception], None] | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.encoder = encoder
        self.on_progress = on_progress
        self.on_done = on_done
        self.on_error = on_error
        self._queue: queue.Queue[Path | None] = queue.Queue()
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, name="normalize", daemon=True)
        self._thread.start()

    def submit(self, paths: Iterable[Path]) -> None:
        for p in paths:
            self._queue.put(p)

    def pending(self) -> int:
        return self._queue.qsize()

    def _run(self) -> None:
        while True:
            src = self._queue.get()
            try:
                if src is None:
                    return
                if self._cancel.is_set():
                    continue  # Warteschlange nach Abbruch nur noch leeren
                self._process(src)
            finally:
                self._queue.task_done()

    def _process(self, src: Path) -> None:
        def report(p: NormalizeProgress) -> None:
            if self.on_progress:
                self.on_progress(src, p)

        try:
            out = normalize_video(src, self.cache_dir, encoder=self.encoder,
                                  progress=report, cancel=self._cancel)
        except Cancelled:
            return
        except Exception as e:  # noqa: BLE001 - jeder Fehler wird gemeldet, Rest läuft weiter
            if self.on_error:
                self.on_error(src, e)
            return
        if self.on_done:
            self.on_done(src, out)

    def wait(self) -> None:
        """Wartet, bis alle eingereichten Videos fertig sind."""
        self._queue.join()

    def cancel(self) -> None:
        """Bricht die laufende Umwandlung ab; fertige Dateien bleiben im Cache."""
        self._cancel.set()

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join()
