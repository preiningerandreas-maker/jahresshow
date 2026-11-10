"""Zusätzliche Übergänge (Zoom, Kreis, Drehwischer, Pixel): Modell, Auswahl in der
Oberfläche und Render."""

from __future__ import annotations

from pathlib import Path

import pytest
from mediacheck import framemd5
from PIL import Image, ImageChops, ImageStat

from slideshow.app.properties import TRANSITION_LABELS, ItemPanel, ProjectPanel
from slideshow.core.ffmpeg import probe, run_ffmpeg
from slideshow.core.importer import clip_length
from slideshow.core.project import FPS, TRANSITIONS, Item, Project, Settings
from slideshow.render.assemble import RenderOptions, render_project

NEW = ("zoomin", "circleopen", "radial", "pixelize")


def test_new_transitions_are_known_and_labelled() -> None:
    assert set(NEW) <= set(TRANSITIONS)
    assert set(TRANSITIONS) == set(TRANSITION_LABELS)  # jede Auswahl hat einen deutschen Namen
    for kind in NEW:
        Item("title", transition=kind).validate()
        Settings(transition=kind).validate()
    for unknown in ("spirale", "dissolve"):  # Auflösen ist bewusst nicht angeboten
        with pytest.raises(ValueError, match="Übergang"):
            Item("title", transition=unknown).validate()


def test_old_transitions_unchanged() -> None:
    assert TRANSITIONS[:5] == ("fade", "fadeblack", "wipeleft", "slideleft", "none")


def test_panels_offer_the_new_transitions(qtbot, tmp_path: Path) -> None:  # noqa: ANN001
    item = Item("title", text=None)
    project = Project(items=[item], base_dir=tmp_path)
    ip = ItemPanel()
    qtbot.addWidget(ip)
    ip.show()
    ip.set_items(project, [item])
    for kind in NEW:
        assert ip.transition.findData(kind) >= 0
    ip.transition.setCurrentIndex(ip.transition.findData("radial"))
    assert item.transition == "radial"

    pp = ProjectPanel()
    qtbot.addWidget(pp)
    pp.set_project(project)
    pp.transition.setCurrentIndex(pp.transition.findData("pixelize"))
    assert project.settings.transition == "pixelize"


# --- Render ---------------------------------------------------------------------------


def two_titles(kind: str, tmp_path: Path) -> Project:
    items = [Item("title", duration=2.0, background="#FF0000"),
             Item("title", duration=2.0, background="#0000FF")]
    return Project(items=items, settings=Settings(transition=kind, transition_duration=0.5),
                   base_dir=tmp_path)


def frame_at(video: Path, seconds: float, out: Path) -> Path:
    run_ffmpeg(["-ss", f"{seconds:.3f}", "-i", str(video), "-frames:v", "1",
                "-vf", "scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=rgb24",
                "-update", "1", str(out)])
    return out


def diff(a: Path, b: Path) -> float:
    with Image.open(a) as ia, Image.open(b) as ib:
        d = ImageChops.difference(ia.convert("RGB"), ib.convert("RGB"))
        return sum(ImageStat.Stat(d).mean) / 3


@pytest.mark.slow
@pytest.mark.parametrize("kind", NEW)
def test_transition_renders_and_blends(kind: str, tmp_path: Path) -> None:
    project = two_titles(kind, tmp_path)
    out = tmp_path / f"{kind}.mp4"
    render_project(project, RenderOptions(out, preview=True, encoder="libx264",
                                          cache_dir=tmp_path / "cache"))
    expected = project.total_frames(clip_length)
    assert expected == 3.5 * FPS  # 2 + 2 s minus 0,5 s Übergang
    video = next(s for s in probe(out)["streams"] if s["codec_type"] == "video")
    assert int(video["nb_frames"]) == expected

    first = frame_at(out, 0.3, tmp_path / "a.png")
    last = frame_at(out, 3.2, tmp_path / "c.png")
    assert diff(first, last) > 50  # Anfang rot, Ende blau
    # Im Übergang (1,5 s bis 2,0 s) gibt es ein Zwischenbild, das weder Anfang noch Ende ist
    # (beim Zoom erscheint das neue Bild erst in der zweiten Hälfte)
    between = [frame_at(out, sec, tmp_path / f"m{i}.png") for i, sec in enumerate((1.7, 1.8, 1.9))]
    assert any(diff(m, first) > 5 and diff(m, last) > 5 for m in between)


@pytest.mark.slow
@pytest.mark.parametrize("kind", NEW)
def test_transition_is_reproducible(kind: str, tmp_path: Path) -> None:
    """Zwei Exporte desselben Projekts ergeben dieselben Bilder."""
    outs = []
    for i in (1, 2):
        out = tmp_path / f"d{i}.mp4"
        render_project(two_titles(kind, tmp_path), RenderOptions(
            out, preview=True, encoder="libx264", cache_dir=tmp_path / f"cache{i}"))
        outs.append(out)
    assert framemd5(outs[0]) == framemd5(outs[1])
