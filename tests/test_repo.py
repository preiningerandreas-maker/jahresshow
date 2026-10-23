"""Einheitliche Zeilenenden laut .gitattributes."""

from __future__ import annotations

import shutil
import subprocess

import pytest
from conftest import TESTS

ROOT = TESTS.parent
CRLF = {"build.ps1", "packaging/README.txt"}


def _eol() -> list[tuple[str, str, str, str]]:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("kein Git-Arbeitsordner")
    out = subprocess.run(["git", "ls-files", "--eol", "-z"], cwd=ROOT, capture_output=True,
                         check=True).stdout.decode("utf-8")
    rows = []
    for entry in filter(None, out.split("\0")):
        info, path = entry.split("\t", 1)
        index, work, attr = info.split(maxsplit=2)
        rows.append((path, index, work, attr.strip()))
    return rows


def test_every_file_has_fixed_line_endings() -> None:
    """Jede Datei hat eine feste Regel: LF, CRLF (Windows-Skripte) oder binär. Im Repository
    stehen Textdateien immer mit LF."""
    rows = _eol()
    assert rows
    for path, index, _work, attr in rows:
        if index == "i/-text":
            assert attr in ("attr/-text", "attr/text=auto eol=lf"), (path, attr)
            continue
        assert index in ("i/lf", "i/none"), (path, index)
        expected = "attr/text eol=crlf" if path in CRLF else "attr/text=auto eol=lf"
        assert attr == expected, (path, attr)


def test_binary_media_marked_binary() -> None:
    for path, _index, _work, attr in _eol():
        if path.lower().endswith((".jpg", ".png", ".heic", ".mp3", ".m4a", ".mp4", ".ttf")):
            assert attr == "attr/-text", (path, attr)
