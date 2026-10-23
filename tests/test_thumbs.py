from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
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


def _alive(pid: int) -> bool:
    """Läuft der Prozess noch? (Windows: Exit-Code abfragen, ohne ihn zu beenden)"""
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-Prozessabfrage")
def test_thumb_workers_exit_when_app_is_killed(tmp_path: Path) -> None:
    """Absturz oder Task-Manager: Die Hilfsprozesse dürfen nicht liegen bleiben."""
    code = (
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "from slideshow.core.thumbs import ThumbProcessPool\n"
        "if __name__ == '__main__':\n"
        "    pool = ThumbProcessPool(Path(sys.argv[1]))\n"
        "    pool.start()\n"
        "    print(json.dumps(list(pool._pool._processes)), flush=True)\n"
        "    time.sleep(120)\n"
    )
    script = tmp_path / "app.py"
    script.write_text(code, "utf-8")
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.Popen([sys.executable, str(script), str(tmp_path / "cache")],
                            cwd=root, stdout=subprocess.PIPE, text=True,
                            env={**os.environ, "PYTHONPATH": str(root)})
    try:
        assert proc.stdout is not None
        workers = json.loads(proc.stdout.readline())
        assert len(workers) == 2 and all(_alive(pid) for pid in workers)
    finally:
        proc.kill()  # hart beenden wie im Task-Manager
        proc.wait(10)
    deadline = time.monotonic() + 15
    while any(_alive(pid) for pid in workers) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not any(_alive(pid) for pid in workers)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-Job-Objekt")
def test_thumb_workers_are_in_kill_on_close_job(tmp_path: Path) -> None:
    """Jeder Hilfsprozess steckt gleich nach dem Start im Job-Objekt, das Windows beim Ende
    des Hauptprogramms schließt. Das Hauptprogramm selbst nicht: Ein von dort geöffneter
    Videoplayer soll weiterlaufen."""
    import ctypes
    from ctypes import wintypes

    from slideshow.core.thumbs import ThumbProcessPool, _kill_on_close_job, _warm_up

    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE,
                                        ctypes.POINTER(wintypes.BOOL)]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE

    def in_job(handle: int) -> bool:
        result = wintypes.BOOL()
        assert kernel32.IsProcessInJob(handle, _kill_on_close_job(), ctypes.byref(result))
        return bool(result.value)

    tp = ThumbProcessPool(tmp_path / "cache")
    try:
        pool = tp._get_pool()
        for f in [pool.submit(_warm_up) for _ in range(2)]:
            f.result(timeout=60)
        handles = [p._popen._handle for p in pool._processes.values()]
        assert len(handles) == 2 and all(in_job(h) for h in handles)
        assert not in_job(kernel32.GetCurrentProcess())
    finally:
        tp.shutdown()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-Prozessabfrage")
def test_thumb_workers_exit_when_app_is_killed_during_their_start(tmp_path: Path) -> None:
    """Das Hauptprogramm stirbt, während die Hilfsprozesse noch starten (gleich nach dem
    Programmstart): Auch dann dürfen sie nicht liegen bleiben. Im fertigen Paket blieben
    sie vorher hängen; jetzt beendet Windows sie über ein Job-Objekt."""
    code = (
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "from slideshow.core.thumbs import ThumbProcessPool, _warm_up\n"
        "if __name__ == '__main__':\n"
        "    pool = ThumbProcessPool(Path(sys.argv[1]))._get_pool()\n"
        "    for _ in range(2):\n"
        "        pool.submit(_warm_up)\n"
        "    print(json.dumps(list(pool._processes)), flush=True)\n"
        "    time.sleep(120)\n"
    )
    script = tmp_path / "app.py"
    script.write_text(code, "utf-8")
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.Popen([sys.executable, str(script), str(tmp_path / "cache")],
                            cwd=root, stdout=subprocess.PIPE, text=True,
                            env={**os.environ, "PYTHONPATH": str(root)})
    try:
        assert proc.stdout is not None
        workers = json.loads(proc.stdout.readline())
        assert workers and all(_alive(pid) for pid in workers)
    finally:
        proc.kill()  # sofort, ohne auf den Start der Hilfsprozesse zu warten
        proc.wait(10)
    deadline = time.monotonic() + 15
    while any(_alive(pid) for pid in workers) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not any(_alive(pid) for pid in workers)
