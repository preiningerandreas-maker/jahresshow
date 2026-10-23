"""FFmpeg/ffprobe finden und aufrufen (immer mit Argumentliste, nie shell=True)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path

ProgressCallback = Callable[[float], None]
"""Wird mit der bereits verarbeiteten Zeit in Sekunden aufgerufen."""

CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class FFmpegError(RuntimeError):
    """FFmpeg ist mit Fehler beendet worden."""


class Cancelled(Exception):
    """Der Nutzer hat abgebrochen."""


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).resolve().parent)
    dirs.append(Path(__file__).resolve().parents[2] / "bin")
    return dirs


def find_tool(name: str) -> Path:
    """Sucht ffmpeg/ffprobe: Umgebungsvariable, Programmordner, bin/, PATH."""
    env = os.environ.get(f"FLASHBACK_{name.upper()}")
    if env and Path(env).is_file():
        return Path(env)
    exe = f"{name}.exe" if os.name == "nt" else name
    for d in _candidate_dirs():
        if (d / exe).is_file():
            return d / exe
    found = shutil.which(name)
    if found:
        return Path(found)
    raise FileNotFoundError(
        f"{exe} wurde nicht gefunden. Bitte {exe} in den Ordner 'bin' neben dem Programm legen."
    )


def ffmpeg_path() -> Path:
    return find_tool("ffmpeg")


def ffprobe_path() -> Path:
    return find_tool("ffprobe")


def run_ffmpeg(
    args: Sequence[str | os.PathLike[str]],
    *,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """Führt ffmpeg mit den Argumenten aus. Fortschritt über -progress, Abbruch über cancel."""
    cmd = [
        str(ffmpeg_path()),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-nostats",
        *map(str, args),
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=CREATIONFLAGS,
        encoding="utf-8",
        errors="replace",
    )
    err_tail: deque[str] = deque(maxlen=30)

    def read_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            err_tail.append(line.rstrip())

    t = threading.Thread(target=read_stderr, daemon=True)
    t.start()

    def watch_cancel() -> None:
        assert cancel is not None
        while proc.poll() is None:
            if cancel.wait(0.2):
                proc.kill()
                return

    if cancel is not None:
        threading.Thread(target=watch_cancel, daemon=True).start()

    assert proc.stdout is not None
    for line in proc.stdout:
        key, _, value = line.strip().partition("=")
        if progress is not None and key == "out_time_us" and value.lstrip("-").isdigit():
            progress(max(0, int(value)) / 1_000_000)
    proc.wait()
    t.join(timeout=5)
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    if proc.returncode != 0:
        raise FFmpegError(
            f"FFmpeg-Fehler (Code {proc.returncode}):\n" + "\n".join(err_tail)
        )


def probe(path: str | os.PathLike[str]) -> dict:
    """ffprobe-Ausgabe (format + streams) als dict."""
    cmd = [
        str(ffprobe_path()),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    res = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATIONFLAGS,
        check=False,
    )
    if res.returncode != 0:
        raise FFmpegError(f"ffprobe konnte {path} nicht lesen:\n{res.stderr.strip()}")
    return json.loads(res.stdout)
