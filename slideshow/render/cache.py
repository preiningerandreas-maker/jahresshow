"""Cache für Segmente, Blöcke und Musik. Dateien entstehen atomar: erst unter einem eindeutigen
Zwischennamen schreiben, dann umbenennen. So hinterlässt ein Abbruch nie halbe Dateien, und
zwei gleichzeitige Läufe (z. B. Export neben Hintergrund-Umwandlung oder ein zweites Programm)
schreiben nie in dieselbe Zwischendatei."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

# Erhöhen, wenn sich die Render-Logik so ändert, dass alte Cache-Dateien ungültig werden
RENDER_VERSION = 4
PART_MARK = ".part"
STALE_PART_AGE = 24 * 3600  # Sekunden; ältere Zwischendateien stammen von Abstürzen


DATA_DIR = "Flashback"
OLD_DATA_DIR = "Jahresshow"  # Name bis Oktober 2026


def data_root() -> Path:
    """Datenordner (%LOCALAPPDATA%\\Flashback). Gibt es nur den Ordner unter dem alten
    Namen, wird er umbenannt: umgewandelte Videos, Vorschaubilder und Einstellungen bleiben."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".cache"
    new, old = root / DATA_DIR, root / OLD_DATA_DIR
    if not new.exists() and old.is_dir():
        with contextlib.suppress(OSError):  # z. B. vom alten Programm noch geöffnet
            old.rename(new)
    return new


def default_cache_dir() -> Path:
    return data_root() / "cache"


def file_fingerprint(path: Path) -> dict[str, Any]:
    st = path.stat()
    return {"path": str(path.resolve()), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def make_key(data: dict[str, Any]) -> str:
    raw = json.dumps({"v": RENDER_VERSION, **data}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def tmp_path(final: Path) -> Path:
    """Eindeutiger Zwischenname (Prozess + Zufall) mit gleicher Endung, damit FFmpeg das
    Format erkennt: name.<pid>-<zufall>.part.mp4"""
    tag = f"{os.getpid()}-{uuid.uuid4().hex[:12]}"
    return final.with_name(f"{final.stem}.{tag}{PART_MARK}{final.suffix}")


def commit_tmp(tmp: Path, final: Path) -> None:
    """Zwischendatei atomar an ihren Platz bringen. Hat ein anderer Lauf dieselbe Datei
    inzwischen fertig (und unter Windows gerade geöffnet), gilt dessen Ergebnis."""
    try:
        tmp.replace(final)
    except PermissionError:
        if not final.is_file():
            raise
        cleanup_tmp(tmp)


def atomic_write_text(path: Path, text: str) -> None:
    tmp = tmp_path(path)
    try:
        tmp.write_text(text, "utf-8")
        commit_tmp(tmp, path)
    finally:
        cleanup_tmp(tmp)


def cleanup_tmp(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def sweep_stale_parts(cache_dir: Path, max_age: float = STALE_PART_AGE) -> int:
    """Alte Zwischendateien abgestürzter Läufe entfernen. Liefert die Anzahl."""
    if not cache_dir.is_dir():
        return 0
    limit = time.time() - max_age
    removed = 0
    for p in cache_dir.rglob(f"*{PART_MARK}*"):
        with contextlib.suppress(OSError):
            if p.is_file() and p.stat().st_mtime < limit:
                p.unlink()
                removed += 1
    return removed
