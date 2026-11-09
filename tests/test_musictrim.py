"""Musik kürzen: Modell und alte Projekte, Render der Playlist, Oberfläche."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from slideshow.app.music import MusicPanel, trim_text
from slideshow.core.ffmpeg import probe
from slideshow.core.project import Music, Project
from slideshow.render.audio import build_music, trim_filter

TRACK = "Musik 2 – Sommer.m4a"  # 9 s


def durations(cache: Path) -> list[float]:
    return sorted(float(probe(w)["format"]["duration"]) for w in (cache / "music").glob("*.wav"))


# --- Modell ---------------------------------------------------------------------------


def test_trims_roundtrip_and_old_projects(tmp_path: Path) -> None:
    song = tmp_path / "a.mp3"
    p = Project(music=Music(tracks=[str(song), str(tmp_path / "b.mp3")],
                            trims={str(song): [2.0, 7.5]}), base_dir=tmp_path)
    target = tmp_path / "p.json"
    p.save(target)
    data = json.loads(target.read_text("utf-8"))
    assert data["music"]["trims"] == {"a.mp3": [2.0, 7.5]}  # Schlüssel relativ wie die Stücke
    m = Project.load(target).music
    assert m.trim_of(m.tracks[0]) == (2.0, 7.5) and m.trim_of(m.tracks[1]) == (0.0, None)

    # Altes Projekt ohne das Feld: alles läuft ganz
    del data["music"]["trims"]
    target.write_text(json.dumps(data), "utf-8")
    old = Project.load(target).music
    assert old.trims == {} and old.trim_of("a.mp3") == (0.0, None)


@pytest.mark.parametrize("trim", [[-1.0, None], [5.0, 5.0], [5.0, 3.0], [1.0]])
def test_invalid_trim_rejected(trim: list) -> None:
    with pytest.raises(ValueError, match="Musikausschnitt"):
        Music(tracks=["a.mp3"], trims={"a.mp3": trim}).validate()


def test_open_end_is_valid() -> None:
    Music(tracks=["a.mp3"], trims={"a.mp3": [3.0, None]}).validate()


def test_trim_filter_and_text() -> None:
    assert trim_filter(0.0, None) == ""
    assert trim_filter(2.0, None) == "atrim=start=2.000,asetpts=PTS-STARTPTS,"
    assert trim_filter(0.0, 5.0) == "atrim=end=5.000,asetpts=PTS-STARTPTS,"
    assert trim_text(10, 150) == "0:10 bis 2:30"
    assert trim_text(10, None) == "ab 0:10" and trim_text(0, 150) == "bis 2:30"


# --- Render ---------------------------------------------------------------------------


@pytest.fixture
def media(synthetic: Path, tmp_path: Path) -> Path:
    for name in (TRACK, "Musik 1 – Frühling.mp3"):
        shutil.copy2(synthetic / name, tmp_path / name)
    return tmp_path


@pytest.mark.slow
@pytest.mark.parametrize("trim,expected", [([2.0, 5.0], 3.0), ([2.0, None], 7.0),
                                           ([0.0, 4.0], 4.0)])
def test_playlist_is_cut(media: Path, trim: list, expected: float) -> None:
    p = Project(music=Music(tracks=[TRACK], trims={TRACK: trim}, fade_out=0.0), base_dir=media)
    build_music(p, 3.0, [], media / "cache")
    assert any(abs(d - expected) < 0.1 for d in durations(media / "cache"))


@pytest.mark.slow
def test_untrimmed_playlist_and_cache_key_unchanged(media: Path) -> None:
    plain = Project(music=Music(tracks=[TRACK], fade_out=0.0), base_dir=media)
    empty = Project(music=Music(tracks=[TRACK], trims={}, fade_out=0.0), base_dir=media)
    a = build_music(plain, 3.0, [], media / "cache")
    assert build_music(empty, 3.0, [], media / "cache") == a  # derselbe Zwischenstand
    assert any(abs(d - 9.0) < 0.1 for d in durations(media / "cache"))
    cut = Project(music=Music(tracks=[TRACK], trims={TRACK: [1.0, 4.0]}, fade_out=0.0),
                  base_dir=media)
    assert build_music(cut, 3.0, [], media / "cache") != a  # Ausschnitt: neuer Schlüssel


# --- Oberfläche -----------------------------------------------------------------------


def test_panel_sets_and_resets_trim(qtbot, media: Path) -> None:  # noqa: ANN001
    project = Project(music=Music(tracks=[str(media / TRACK),
                                          str(media / "Musik 1 – Frühling.mp3")]),
                      base_dir=media)
    panel = MusicPanel()
    qtbot.addWidget(panel)
    panel.show()
    changes: list[int] = []
    panel.changed.connect(lambda: changes.append(1))
    panel.set_project(project)
    first = project.music.tracks[0]
    assert not panel.trim_box.isEnabled()  # nichts markiert

    panel.list.setCurrentRow(0)
    assert panel.trim_box.isEnabled() and panel.trim_end.value() == 0.0
    panel.trim_start.setValue(2.0)
    assert project.music.trim_of(first) == (2.0, None) and changes
    assert "ab 0:02" in panel.list.item(0).text()
    panel.trim_end.setValue(6.0)
    assert project.music.trim_of(first) == (2.0, 6.0)
    assert "Musik insgesamt" in panel.summary.text()

    # Ende vor dem Start wird nachgezogen, nie ungültig
    panel.trim_end.setValue(1.0)
    start, end = project.music.trim_of(first)
    assert end is not None and end > start
    project.music.validate()

    # Zweites Stück bleibt unberührt, Zurücksetzen löscht den Eintrag
    assert project.music.trim_of(project.music.tracks[1]) == (0.0, None)
    panel.trim_reset.click()
    assert project.music.trims == {}

    # Entfernen räumt den Ausschnitt mit auf
    panel.trim_start.setValue(1.0)
    panel.list.setCurrentRow(0)
    panel.remove_selected()
    assert project.music.trims == {}
