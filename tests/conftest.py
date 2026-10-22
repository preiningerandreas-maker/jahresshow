from __future__ import annotations

import os
from pathlib import Path

# GUI-Tests ohne sichtbare Fenster; die Screenshot-Tests starten eigene Prozesse mit der
# echten Windows-Darstellung
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from slideshow.core.ffmpeg import find_tool

TESTS = Path(__file__).resolve().parent
REAL_MEDIA = TESTS / "media" / "real"


def _has_ffmpeg() -> bool:
    try:
        find_tool("ffmpeg")
        find_tool("ffprobe")
    except FileNotFoundError:
        return False
    return True


requires_ffmpeg = pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe fehlt")


@pytest.fixture(scope="session")
def synthetic() -> Path:
    if not _has_ffmpeg():
        pytest.skip("ffmpeg/ffprobe fehlt")
    from make_media import ensure_media

    return ensure_media()


@pytest.fixture(scope="session")
def real_media() -> Path:
    if not REAL_MEDIA.is_dir() or not any(REAL_MEDIA.iterdir()):
        pytest.skip("tests/media/real fehlt (echte Beispieldateien nicht im Git)")
    return REAL_MEDIA


@pytest.fixture
def umlaut_dir(tmp_path: Path) -> Path:
    d = tmp_path / "Urlaub Wörthersee 2026"
    d.mkdir()
    return d
