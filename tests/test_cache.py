"""Cache: eindeutige Zwischendateien, atomares Umbenennen, gleichzeitige Läufe."""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from slideshow.core.project import Item, Project
from slideshow.core.thumbs import ThumbnailCache
from slideshow.render.cache import commit_tmp, sweep_stale_parts
from slideshow.render.cache import tmp_path as part_path


def test_tmp_path_is_unique_and_keeps_suffix(tmp_path: Path) -> None:
    final = tmp_path / "abc.mp4"
    parts = {part_path(final).name for _ in range(100)}
    assert len(parts) == 100
    for n in parts:
        assert n.startswith("abc.") and n.endswith(".part.mp4")
        assert str(os.getpid()) in n


def test_commit_tmp_keeps_finished_file_of_other_run(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    final = tmp_path / "x.wav"
    final.write_bytes(b"fertig")
    tmp = part_path(final)
    tmp.write_bytes(b"neu")

    def locked(self: Path, target: Path) -> Path:
        raise PermissionError("Datei wird verwendet")

    monkeypatch.setattr(Path, "replace", locked)
    commit_tmp(tmp, final)  # Windows: Ziel geöffnet -> fertiges Ergebnis des anderen Laufs gilt
    assert final.read_bytes() == b"fertig"
    assert not tmp.exists()

    lonely = tmp_path / "y.wav"
    tmp2 = part_path(lonely)
    tmp2.write_bytes(b"neu")
    with pytest.raises(PermissionError):
        commit_tmp(tmp2, lonely)  # ohne fertige Datei ist das ein echter Fehler


def test_parallel_writes_of_same_cache_file(synthetic: Path, tmp_path: Path) -> None:
    """Mehrere Threads (wie Vorschaubilder, Export und Hintergrund-Umwandlung) erzeugen
    gleichzeitig dieselbe Cache-Datei: kein Fehler, eine fertige Datei, keine Reste."""
    src = tmp_path / "Foto Ä.jpg"
    shutil.copy2(synthetic / "Querformat Wörthersee.jpg", src)
    caches = [ThumbnailCache(tmp_path / "cache") for _ in range(8)]
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(caches))

    def work(c: ThumbnailCache) -> None:
        try:
            barrier.wait()
            c.create(src)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=work, args=(c,)) for c in caches]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    files = list((tmp_path / "cache" / "thumbs").iterdir())
    assert len(files) == 1 and files[0].suffix == ".jpg"
    with Image.open(files[0]) as im:
        assert max(im.size) == 320


def test_sweep_stale_parts(tmp_path: Path) -> None:
    d = tmp_path / "segments"
    d.mkdir()
    old = part_path(d / "a.mp4")
    new = part_path(d / "b.mp4")
    keep = d / "c.mp4"
    for p in (old, new, keep):
        p.write_bytes(b"x")
    past = time.time() - 3 * 24 * 3600
    os.utime(old, (past, past))
    assert sweep_stale_parts(tmp_path) == 1
    assert not old.exists() and new.exists() and keep.exists()


def test_project_save_leaves_no_temp_files(umlaut_dir: Path) -> None:
    p = Project(items=[Item("photo", path="a.jpg")])
    target = umlaut_dir / "Projekt Ö.json"
    p.save(target)
    p.save(target)
    assert [f.name for f in umlaut_dir.iterdir()] == ["Projekt Ö.json"]
