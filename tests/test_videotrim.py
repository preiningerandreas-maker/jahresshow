"""Video kürzen: Umrechnung von/bis, Eigenschaften-Panel und Render der gekürzten Länge."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from slideshow.app.properties import ItemPanel
from slideshow.core.ffmpeg import probe
from slideshow.core.importer import clip_length
from slideshow.core.project import FPS, Item, Project
from slideshow.core.videotrim import MIN_CLIP, set_trim, trim_end
from slideshow.render.segment import RenderConfig, render_segment

LENGTH = 10.0

# --- Umrechnung -----------------------------------------------------------------------


def video(**kw: object) -> Item:
    return Item("video", path="a.mp4", **kw)  # type: ignore[arg-type]


def test_untouched_video_plays_to_the_end() -> None:
    v = video()
    assert (v.start, v.duration, trim_end(v, LENGTH)) == (0.0, None, LENGTH)


def test_set_end_and_start() -> None:
    v = video()
    set_trim(v, LENGTH, end=6.0)
    assert (v.start, v.duration) == (0.0, 6.0)
    set_trim(v, LENGTH, start=2.0)  # Ende bleibt bei 6
    assert (v.start, v.duration) == (2.0, 4.0) and trim_end(v, LENGTH) == 6.0
    set_trim(v, LENGTH, end=0.0)  # 0 = bis zum Ende
    assert (v.start, v.duration) == (2.0, None)
    set_trim(v, LENGTH, start=0.0)
    assert (v.start, v.duration) == (0.0, None)


def test_old_projects_start_and_duration_mean_the_same() -> None:
    v = video(start=3.0, duration=4.0)  # so standen sie schon in alten Dateien
    assert trim_end(v, LENGTH) == 7.0
    assert trim_end(video(start=3.0, duration=20.0), LENGTH) == LENGTH  # über das Ende hinaus
    set_trim(v, LENGTH, end=7.0)  # unverändert gesetzt: nichts ändert sich
    assert (v.start, v.duration) == (3.0, 4.0)


@pytest.mark.parametrize("start,end", [(9.9, None), (-5.0, None), (None, 1.0), (None, 99.0),
                                       (8.0, 2.0)])
def test_trim_always_stays_valid(start: float | None, end: float | None) -> None:
    v = video(start=4.0, duration=3.0)
    set_trim(v, LENGTH, start=start, end=end)
    s, e = v.start, trim_end(v, LENGTH)
    assert 0.0 <= s < e <= LENGTH and e - s >= MIN_CLIP - 1e-9


def test_end_next_to_the_clip_end_counts_as_the_end() -> None:
    v = video()
    set_trim(v, LENGTH, end=LENGTH - 0.02)
    assert v.duration is None


def test_unknown_length_changes_nothing() -> None:
    v = video(start=1.0)
    set_trim(v, 0.0, start=5.0, end=6.0)
    assert (v.start, v.duration) == (1.0, None)


# --- Render ---------------------------------------------------------------------------


@pytest.fixture
def clip(synthetic: Path, tmp_path: Path) -> Path:
    src = tmp_path / "Video mit Ton.mp4"
    shutil.copy2(synthetic / src.name, src)
    return src


@pytest.mark.slow
def test_trimmed_video_renders_with_the_chosen_length(clip: Path, tmp_path: Path) -> None:
    length = clip_length(clip)
    item = Item("video", path=str(clip))
    set_trim(item, length, start=1.0, end=3.0)
    project = Project(items=[item], base_dir=tmp_path)
    entry = project.timeline(clip_length)[0]
    assert entry.frames == round(2.0 * FPS)
    seg, _ = render_segment(project, entry, RenderConfig(320, 180, "libx264", tmp_path / "cache"))
    assert abs(float(probe(seg)["format"]["duration"]) - 2.0) < 0.15


# --- Oberfläche -----------------------------------------------------------------------


def test_panel_sets_from_and_to(qtbot, clip: Path, tmp_path: Path) -> None:  # noqa: ANN001
    length = clip_length(clip)
    item = Item("video", path=str(clip))
    photo = Item("photo", path="p.jpg")
    project = Project(items=[item, photo], base_dir=tmp_path)
    panel = ItemPanel()
    qtbot.addWidget(panel)
    panel.resize(360, 900)
    panel.show()
    changes: list[int] = []
    panel.changed.connect(lambda: changes.append(1))

    panel.set_items(project, [item])
    assert panel.end.value() == 0.0 and panel.end.specialValueText() == "Ende des Videos"
    assert not panel.duration.isVisible()  # bei Videos bestimmen von/bis die Länge
    assert "lang" in panel.clip_info.text()

    panel.end.setValue(3.0)
    assert (item.start, item.duration) == (0.0, 3.0) and changes
    panel.start.setValue(1.0)
    assert (item.start, item.duration) == (1.0, 2.0)  # Ende bleibt bei 3 s
    panel.end.setValue(0.0)
    assert (item.start, item.duration) == (1.0, None)
    assert length > 3.0

    # Foto und Video markiert: Anzeigedauer ist da und gilt wie bisher für beide
    panel.set_items(project, [item, photo])
    assert panel.duration.isVisible() and panel.start.isVisible()
    panel.duration.setValue(7.0)
    assert photo.duration == 7.0 and item.duration == 7.0
