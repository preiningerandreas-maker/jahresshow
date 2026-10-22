"""Cache für Segmente, Blöcke und Musik. Dateien entstehen atomar (erst .tmp, dann umbenennen),
damit ein Abbruch nie halbe Dateien im Cache hinterlässt."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
from typing import Any

# Erhöhen, wenn sich die Render-Logik so ändert, dass alte Cache-Dateien ungültig werden
RENDER_VERSION = 3


def default_cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "Jahresshow" / "cache"


def file_fingerprint(path: Path) -> dict[str, Any]:
    st = path.stat()
    return {"path": str(path.resolve()), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def make_key(data: dict[str, Any]) -> str:
    raw = json.dumps({"v": RENDER_VERSION, **data}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def tmp_path(final: Path) -> Path:
    """Temporärer Name mit gleicher Endung, damit FFmpeg das Format erkennt."""
    return final.with_name(f"{final.stem}.part{final.suffix}")


def cleanup_tmp(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)
