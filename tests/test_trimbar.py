"""Schnitt-Leiste: Schieber, Synchron mit den Sekundenfeldern, Live-Bild und Vorhören."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from slideshow.app.music import MusicPanel
from slideshow.app.properties import ItemPanel
from slideshow.app.scrubwidgets import AudioScrub
from slideshow.app.trimbar import END, MARGIN, START, TrimBar
from slideshow.core.importer import clip_length
from slideshow.core.project import Item, Music, Project
from slideshow.core.scrub import PCM_BYTES_PER_SECOND


def _bar(qtbot, length: float = 10.0) -> TrimBar:  # noqa: ANN001
    bar = TrimBar(min_gap=0.5)
    qtbot.addWidget(bar)
    bar.resize(400, 44)
    bar.show()
    bar.set_length(length)
    bar.set_values(0.0, 0.0)
    return bar


def _pos(bar: TrimBar, t: float) -> QPoint:
    return QPoint(round(bar._x(t)), bar.height() // 2)


def test_bar_drag_start_and_end_with_limits(qtbot) -> None:  # noqa: ANN001
    bar = _bar(qtbot)
    moves: list[tuple[str, float, float]] = []
    done: list[tuple[float, float]] = []
    bar.changing.connect(lambda w, s, e: moves.append((w, round(s, 2), round(e, 2))))
    bar.committed.connect(lambda s, e: done.append((round(s, 2), round(e, 2))))
    assert (bar.start, bar.end) == (0.0, 10.0)

    # Anfang ziehen: laufend gemeldet, erst beim Loslassen festgeschrieben
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=_pos(bar, 0.0))
    QTest.mouseMove(bar, _pos(bar, 3.0))
    assert moves and moves[-1][0] == START and 2.9 < moves[-1][1] < 3.1 and not done
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=_pos(bar, 3.0))
    assert len(done) == 1 and 2.9 < done[0][0] < 3.1 and done[0][1] == 10.0

    # Ende ziehen, bis auf den Mindestabstand zum Anfang
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=_pos(bar, 10.0))
    QTest.mouseMove(bar, _pos(bar, 0.0))
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=_pos(bar, 0.0))
    assert moves[-1][0] == END
    assert bar.end - bar.start == pytest.approx(0.5, abs=0.01)
    # Anfang lässt sich nicht hinter das Ende ziehen
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=_pos(bar, bar.start))
    QTest.mouseMove(bar, _pos(bar, 10.0))
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=_pos(bar, 10.0))
    assert bar.start <= bar.end - 0.5 + 1e-6 and bar.end <= 10.0


def test_bar_keyboard_and_external_values(qtbot) -> None:  # noqa: ANN001
    bar = _bar(qtbot)
    done: list[tuple[float, float]] = []
    bar.committed.connect(lambda s, e: done.append((s, e)))
    bar.set_values(2.0, 6.0)
    assert (bar.start, bar.end) == (2.0, 6.0)
    bar.set_values(1.0, 0.0)  # 0 = bis zum Ende
    assert (bar.start, bar.end) == (1.0, 10.0)
    bar.setFocus()
    QTest.keyClick(bar, Qt.Key.Key_Right)  # Anfang +0,5 s
    assert done[-1] == (1.5, 10.0)
    QTest.keyClick(bar, Qt.Key.Key_Down)  # Ende wählen
    QTest.keyClick(bar, Qt.Key.Key_Left)
    assert done[-1] == (1.5, 9.5)
    assert MARGIN > 0


def _wait_frames(qtbot, panel: ItemPanel, clip: Path) -> None:  # noqa: ANN001
    assert panel.scrub is not None
    qtbot.waitUntil(lambda: bool(panel.scrub._frames), timeout=30000)


def test_video_bar_syncs_with_fields_and_shows_live_frame(
        qtbot, synthetic: Path, tmp_path: Path) -> None:  # noqa: ANN001
    clip = synthetic / "Video mit Ton.mp4"
    length = clip_length(clip)
    item = Item("video", path=str(clip))
    photo = Item("photo", path="p.jpg")
    project = Project(items=[item, photo], base_dir=tmp_path)
    panel = ItemPanel(cache_dir=tmp_path / "cache")
    qtbot.addWidget(panel)
    panel.resize(360, 900)
    panel.show()
    changes: list[int] = []
    panel.changed.connect(lambda: changes.append(1))
    panel.set_items(project, [item])
    assert panel.trim_bar.isVisible() and panel.trim_bar.length == pytest.approx(length)
    assert panel.scrub is not None and panel.scrub.isVisible()
    _wait_frames(qtbot, panel, clip)

    # Ziehen: Sekundenfeld und Live-Bild laufen mit, das Projekt bleibt bis zum Loslassen
    bar = panel.trim_bar
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=_pos(bar, 0.0))
    QTest.mouseMove(bar, _pos(bar, length / 2))
    assert panel.start.value() == pytest.approx(length / 2, abs=0.2)
    assert item.start == 0.0 and not changes
    pix = panel.scrub.pixmap()
    assert pix is not None and not pix.isNull()
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=_pos(bar, length / 2))
    assert item.start == pytest.approx(length / 2, abs=0.2) and changes
    assert panel.start.value() == pytest.approx(item.start, abs=0.05)

    # Sekundenfeld ändern: die Leiste folgt
    panel.end.setValue(length - 0.5)
    assert bar.end == pytest.approx(length - 0.5, abs=0.05)
    panel.start.setValue(0.0)
    assert bar.start == 0.0 and item.start == 0.0

    # Leiste nur bei genau einem Video: Foto oder Mehrfachauswahl blenden sie aus
    panel.set_items(project, [photo])
    assert not bar.isVisible()
    panel.set_items(project, [item, photo])
    assert not bar.isVisible()
    panel.set_items(project, [item])
    assert bar.isVisible()


def test_music_bar_syncs_and_listens(qtbot, synthetic: Path,
                                     tmp_path: Path) -> None:  # noqa: ANN001
    track = synthetic / "Musik 1 – Frühling.mp3"
    project = Project(music=Music(tracks=[str(track)]), base_dir=synthetic)
    panel = MusicPanel(cache_dir=tmp_path / "cache")
    qtbot.addWidget(panel)
    panel.resize(360, 700)
    panel.show()
    played: list[bytes] = []
    assert panel.listen is not None
    panel.listen._play = played.append  # type: ignore[method-assign]
    changes: list[int] = []
    panel.changed.connect(lambda: changes.append(1))
    panel.set_project(project)
    panel.list.setCurrentRow(0)
    bar = panel.trim_bar
    assert bar.isVisible() and bar.length > 2
    qtbot.waitUntil(lambda: panel.listen.ready, timeout=30000)

    # Ziehen: Sekundenfeld läuft mit, es wird an der Stelle vorgehört, noch nichts gespeichert
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=_pos(bar, 0.0))
    QTest.mouseMove(bar, _pos(bar, 1.0))
    assert panel.trim_start.value() == pytest.approx(1.0, abs=0.2)
    qtbot.waitUntil(lambda: bool(played), timeout=2000)
    assert len(played[-1]) == int(1.5 * PCM_BYTES_PER_SECOND)
    assert project.music.trims == {}
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=_pos(bar, 1.0))
    start, end = project.music.trim_of(project.music.tracks[0])
    assert start == pytest.approx(1.0, abs=0.2) and end is None and changes

    # Sekundenfeld ändern: die Leiste folgt
    panel.trim_end.setValue(2.5)
    assert bar.end == pytest.approx(2.5, abs=0.05)


def test_audio_scrub_opens_real_device(qtbot, synthetic: Path,
                                       tmp_path: Path) -> None:  # noqa: ANN001
    if not AudioScrub.available():
        pytest.skip("kein Audiogerät")
    scrub = AudioScrub(tmp_path / "cache")
    scrub._play(b"\x00\x00" * 22050)
    assert scrub._sink is not None
    qtbot.wait(100)
    assert scrub._sink.error().name == "NoError"
    scrub.stop()
    assert scrub._sink is None
