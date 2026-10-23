"""Fertiges Video im Explorer zeigen (Ordner öffnen, Datei markiert), ohne echten Explorer."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

import pytest

from slideshow.app import reveal

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-Explorer")


def test_windows_finds_file_with_umlauts_and_spaces(umlaut_dir: Path) -> None:
    video = umlaut_dir / "Jahresrückblick 2026 – Größe.mp4"
    video.write_bytes(b"x")
    pidl = reveal.item_id(video)
    assert pidl is not None
    shell32 = reveal._shell32()
    buf = ctypes.create_unicode_buffer(1024)
    shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    assert shell32.SHGetPathFromIDListW(pidl, buf)
    shell32.ILFree(pidl)
    assert Path(buf.value) == video.resolve()
    assert reveal.item_id(umlaut_dir / "fehlt.mp4") is None


class FakeShell:
    """Ersatz für shell32: merkt sich, welche Datei markiert werden soll."""

    def __init__(self, result: int = 0) -> None:
        self.result = result
        self.selected: list[str] = []
        self.freed = 0
        self.real = reveal._shell32()

    def ILCreateFromPathW(self, path: str) -> int:  # noqa: N802
        return self.real.ILCreateFromPathW(path)

    def SHOpenFolderAndSelectItems(self, pidl: int, *_args: object) -> int:  # noqa: N802
        buf = ctypes.create_unicode_buffer(1024)
        self.real.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        self.real.SHGetPathFromIDListW(pidl, buf)
        self.selected.append(buf.value)
        return self.result

    def ILFree(self, pidl: int) -> None:  # noqa: N802
        self.freed += 1
        self.real.ILFree(pidl)


@pytest.fixture
def opened_urls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    urls: list[str] = []
    monkeypatch.setattr(reveal.QDesktopServices, "openUrl",
                        lambda url: urls.append(url.toLocalFile()) or True)
    return urls


def test_reveal_selects_the_file(umlaut_dir: Path, monkeypatch: pytest.MonkeyPatch,
                                 opened_urls: list[str]) -> None:
    video = umlaut_dir / "Show Ö.mp4"
    video.write_bytes(b"x")
    shell = FakeShell()
    monkeypatch.setattr(reveal, "_shell32", lambda: shell)
    reveal.reveal_in_explorer(video)
    assert [Path(p) for p in shell.selected] == [video.resolve()]
    assert shell.freed == 1 and opened_urls == []


def test_reveal_falls_back_to_folder(umlaut_dir: Path, monkeypatch: pytest.MonkeyPatch,
                                     opened_urls: list[str]) -> None:
    # Datei fehlt: nur den Ordner öffnen
    reveal.reveal_in_explorer(umlaut_dir / "fehlt.mp4")
    # Explorer meldet einen Fehler: ebenfalls nur den Ordner
    video = umlaut_dir / "Show.mp4"
    video.write_bytes(b"x")
    shell = FakeShell(result=-2147467259)  # E_FAIL
    monkeypatch.setattr(reveal, "_shell32", lambda: shell)
    reveal.reveal_in_explorer(video)
    assert [Path(u) for u in opened_urls] == [umlaut_dir, umlaut_dir]
    assert shell.freed == 1
