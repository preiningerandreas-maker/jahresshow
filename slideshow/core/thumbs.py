"""Vorschaubilder für die Zeitleiste, dauerhaft im Cache. Erzeugt werden sie erst bei Bedarf
(lazy) in wenigen Hintergrund-Threads, damit die Zeitleiste auch bei 1000 Elementen flüssig
bleibt."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

from PIL import Image

from slideshow.core.ffmpeg import run_ffmpeg
from slideshow.core.importer import media_kind, open_photo, video_info
from slideshow.core.normalize import cached_normalized, tonemap_filter
from slideshow.render.cache import (
    cleanup_tmp,
    commit_tmp,
    default_cache_dir,
    file_fingerprint,
    make_key,
    tmp_path,
)

log = logging.getLogger(__name__)

SYNCHRONIZE = 0x00100000  # Windows: Recht, auf das Ende eines Prozesses zu warten
INFINITE = 0xFFFFFFFF
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

THUMB_SIZE = 320  # längere Kante in Pixeln
THUMB_VERSION = 1


def thumb_path(src: Path, cache_dir: Path, size: int = THUMB_SIZE) -> Path:
    key = make_key({"type": "thumb", "version": THUMB_VERSION, "size": size,
                    "source": file_fingerprint(src)})
    return cache_dir / "thumbs" / f"{key}.jpg"


def make_photo_thumb(src: Path, out: Path, size: int = THUMB_SIZE) -> None:
    # JPEG direkt verkleinert dekodieren: deutlich schneller. Beschädigte Bilder zeigen ihren
    # lesbaren Teil, damit man sieht, ob „trotzdem verwenden“ sich lohnt
    img = open_photo(src, draft=(size, size), tolerant=True)
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


def create_thumb(src: Path, cache_dir: Path, size: int = THUMB_SIZE) -> Path:
    """Vorschaubild erzeugen, falls noch nicht im Cache. Läuft auch in einem eigenen Prozess
    (siehe ThumbProcessPool), daher eine freie Funktion."""
    final = thumb_path(src, cache_dir, size)
    if final.is_file():
        return final
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = tmp_path(final)
    try:
        if media_kind(src) == "video":
            make_video_thumb(src, tmp, size, cache_dir)
        else:
            make_photo_thumb(src, tmp, size)
        commit_tmp(tmp, final)
    finally:
        cleanup_tmp(tmp)
    return final


def _warm_up() -> None:
    """Nichts tun: lädt im Hilfsprozess nur die Module."""


def _exit_with_parent(parent_pid: int) -> None:
    """Hilfsprozess beendet sich, sobald das Hauptprogramm weg ist. Beim normalen Schließen
    räumt das Programm selbst auf; nach einem Absturz oder Beenden per Task-Manager blieben
    die Hilfsprozesse sonst liegen und belegen Speicher."""

    def watch() -> None:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(SYNCHRONIZE, False, parent_pid)
            if not handle:  # schon beendet
                os._exit(0)
            kernel32.WaitForSingleObject(handle, INFINITE)
        else:
            while os.getppid() == parent_pid:
                time.sleep(1)
        os._exit(0)

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


_job_lock = threading.Lock()
_job_handle: int | None = None


def _kill_on_close_job() -> int | None:
    """Windows-Job-Objekt, das alle Prozesse darin beendet, sobald das Hauptprogramm weg ist,
    auch nach Absturz oder Beenden per Task-Manager. _exit_with_parent greift erst, wenn der
    Hilfsprozess fertig gestartet ist; stirbt das Hauptprogramm vorher, bliebe er hängen."""
    global _job_handle
    with _job_lock:
        if _job_handle is None:
            import ctypes
            from ctypes import wintypes

            class BasicLimits(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                            ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t),
                            ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class ExtendedLimits(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BasicLimits),
                            ("IoInfo", ctypes.c_uint64 * 6),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                         ctypes.c_void_p, wintypes.DWORD]
            job = kernel32.CreateJobObjectW(None, None)
            info = ExtendedLimits()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not job or not kernel32.SetInformationJobObject(
                    job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
                    ctypes.sizeof(info)):
                log.warning("Job-Objekt für Hilfsprozesse nicht verfügbar (%s)",
                            ctypes.get_last_error())
                return None
            _job_handle = job  # bleibt offen, bis das Hauptprogramm endet
        return _job_handle


def _assign_to_job(process_handle: int) -> None:
    """Hilfsprozess in das Job-Objekt aufnehmen. Fehler sind nicht schlimm: Dann bleibt es
    bei _exit_with_parent."""
    job = _kill_on_close_job()
    if job is None:
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    if not kernel32.AssignProcessToJobObject(job, process_handle):
        log.warning("Hilfsprozess nicht im Job-Objekt (%s)", ctypes.get_last_error())


if sys.platform == "win32":
    from multiprocessing.context import SpawnContext, SpawnProcess

    class _JobProcess(SpawnProcess):
        """Hilfsprozess, der gleich nach dem Start ins Job-Objekt kommt."""

        def start(self) -> None:
            super().start()
            _assign_to_job(self._popen._handle)  # type: ignore[union-attr]

    class _JobContext(SpawnContext):
        Process = _JobProcess

    def _mp_context() -> SpawnContext | None:
        return _JobContext()
else:
    def _mp_context() -> None:
        return None


class ThumbProcessPool:
    """Vorschaubilder in eigenen Prozessen erzeugen. Fotos zu öffnen ist viel Python-Code
    (Pillow); in Threads würde das die Oberfläche über den GIL ausbremsen."""

    def __init__(self, cache_dir: Path, size: int = THUMB_SIZE, workers: int = 2) -> None:
        self.cache_dir = cache_dir
        self.size = size
        self._workers = workers
        self._pool: ProcessPoolExecutor | None = None
        self._lock = threading.Lock()
        self._failed = False

    def _get_pool(self) -> ProcessPoolExecutor:
        with self._lock:
            if self._pool is None:
                self._pool = ProcessPoolExecutor(max_workers=self._workers,
                                                 mp_context=_mp_context(),
                                                 initializer=_exit_with_parent,
                                                 initargs=(os.getpid(),))
            return self._pool

    def start(self) -> None:
        """Prozesse vorab starten (dauert etwas), damit das erste Scrollen nicht hakt."""
        try:
            pool = self._get_pool()
            for f in [pool.submit(_warm_up) for _ in range(self._workers)]:
                f.result()
        except (RuntimeError, OSError, BrokenProcessPool):
            self._fail()

    def create(self, src: Path) -> Path:
        if not self._failed:
            try:
                future = self._get_pool().submit(create_thumb, src, self.cache_dir, self.size)
            except (RuntimeError, OSError, BrokenProcessPool):
                self._fail()
            else:
                try:
                    return future.result()
                except BrokenProcessPool:
                    self._fail()
        # Notlösung, wenn keine Hilfsprozesse starten können: im eigenen Thread
        return create_thumb(src, self.cache_dir, self.size)

    def _fail(self) -> None:
        log.warning("Hilfsprozesse für Vorschaubilder nicht verfügbar, verwende Threads")
        self._failed = True

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=wait, cancel_futures=True)


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
        return create_thumb(src, self.cache_dir, self.size)

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
