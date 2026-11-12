"""Text sanft ein- und ausblenden: Modell, Länge der Blende, Render (Foto, Titel, Video),
Standbild-Vorschau und Eigenschaften-Panel."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat

from slideshow.app.properties import ItemPanel
from slideshow.core.ffmpeg import run_ffmpeg
from slideshow.core.project import FPS, MAX_TEXT_FADE, Item, Project, TextOverlay
from slideshow.render.segment import (
    RenderConfig,
    render_segment,
    render_still,
    text_fade_seconds,
)

W, H = 640, 360


# --- Modell ---------------------------------------------------------------------------


def test_fade_default_roundtrip_and_old_projects(tmp_path: Path) -> None:
    assert TextOverlay("x").fade == 0.5
    p = Project(items=[Item("photo", path="a.jpg", text=TextOverlay("Hallo", fade=1.25))],
                base_dir=tmp_path)
    target = tmp_path / "p.json"
    p.save(target)
    assert Project.load(target).items[0].text.fade == 1.25  # type: ignore[union-attr]

    # Altes Projekt: Text ohne das Feld „fade“ blendet mit dem Standardwert
    data = json.loads(target.read_text("utf-8"))
    del data["items"][0]["text"]["fade"]
    target.write_text(json.dumps(data), "utf-8")
    assert Project.load(target).items[0].text.fade == 0.5  # type: ignore[union-attr]


@pytest.mark.parametrize("fade", [-0.1, MAX_TEXT_FADE + 0.1])
def test_invalid_fade_rejected(fade: float) -> None:
    with pytest.raises(ValueError, match="Ein-/Ausblenden"):
        TextOverlay("x", fade=fade).validate()


def test_fade_seconds_rules() -> None:
    t = TextOverlay("x", fade=0.5)
    assert text_fade_seconds(t, None) == 0.0  # Standbild-Vorschau: Text immer ganz
    assert text_fade_seconds(TextOverlay("x", fade=0.0), 90) == 0.0
    assert text_fade_seconds(t, 90) == 0.5
    # Kurzes Element: höchstens die halbe Länge, damit Ein- und Ausblenden nicht überlappen
    assert text_fade_seconds(t, 15) == pytest.approx(0.25)
    # Weniger als ein Bild lang: gar nicht
    assert text_fade_seconds(TextOverlay("x", fade=0.01), 90) == 0.0
    assert text_fade_seconds(t, 1) == 0.0


# --- Render ---------------------------------------------------------------------------


def frame_at(segment: Path, seconds: float, out: Path) -> Path:
    # Eine halbe Bilddauer früher, damit gerundete Zeiten nie hinter dem letzten Bild landen
    run_ffmpeg(["-ss", f"{max(0.0, seconds - 0.5 / FPS):.4f}", "-i", str(segment), "-frames:v", "1",
                "-vf", "scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=rgb24",
                "-update", "1", str(out)])
    return out


def diff(a: Path, b: Path) -> float:
    with Image.open(a) as ia, Image.open(b) as ib:
        d = ImageChops.difference(ia.convert("RGB"), ib.convert("RGB"))
        return sum(ImageStat.Stat(d).mean) / 3


def segment_for(project: Project, cache: Path) -> Path:
    entry = project.timeline(lambda _p: 3.0)[0]
    seg, _ = render_segment(project, entry, RenderConfig(W, H, "libx264", cache))
    return seg


def text_visibility(item_kind: str, src: Path | None, tmp_path: Path,
                    fade: float) -> tuple[float, float, float, int]:
    """Unterschied zum Segment ohne Text am Anfang, in der Mitte und im letzten Bild."""
    cache = tmp_path / "cache"
    kw: dict = {"path": str(src)} if src else {}
    base = Item(item_kind, duration=3.0, fit="crop", **kw)
    with_text = Item(item_kind, duration=3.0, fit="crop", **kw,
                     text=TextOverlay("Grüße vom See", position="center", size=120,
                                      fade=fade))
    ref = segment_for(Project(items=[base], base_dir=tmp_path), cache)
    seg = segment_for(Project(items=[with_text], base_dir=tmp_path), cache)
    n = round(3.0 * FPS)
    times = (0.0, 1.5, (n - 1) / FPS)
    d = [diff(frame_at(seg, t, tmp_path / f"a{i}.png"), frame_at(ref, t, tmp_path / f"b{i}.png"))
         for i, t in enumerate(times)]
    return d[0], d[1], d[2], n


@pytest.mark.slow
@pytest.mark.parametrize("kind", ["photo", "title"])
def test_text_fades_in_and_out(kind: str, synthetic: Path, tmp_path: Path) -> None:
    src = None
    if kind == "photo":
        src = tmp_path / "Querformat Wörthersee.jpg"
        shutil.copy2(synthetic / src.name, src)
    start, mid, end, n = text_visibility(kind, src, tmp_path, fade=0.5)
    assert mid > 3.0  # Mitte: Text voll da
    assert start < 1.0 and end < 1.0  # erstes und letztes Bild ohne Text
    assert n == 90


@pytest.mark.slow
def test_fade_zero_keeps_text_the_whole_time(synthetic: Path, tmp_path: Path) -> None:
    src = tmp_path / "Querformat Wörthersee.jpg"
    shutil.copy2(synthetic / src.name, src)
    start, mid, end, _n = text_visibility("photo", src, tmp_path, fade=0.0)
    assert start > 3.0 and mid > 3.0 and end > 3.0


@pytest.mark.slow
def test_video_text_fades(synthetic: Path, tmp_path: Path) -> None:
    src = tmp_path / "Video mit Ton.mp4"
    shutil.copy2(synthetic / src.name, src)
    start, mid, end, _n = text_visibility("video", src, tmp_path, fade=0.5)
    assert mid > 3.0 and start < 1.5 and end < 1.5


@pytest.mark.slow
def test_still_shows_text_fully_and_matches_export_middle(synthetic: Path,
                                                          tmp_path: Path) -> None:
    src = tmp_path / "Querformat Wörthersee.jpg"
    shutil.copy2(synthetic / src.name, src)
    item = Item("photo", path=str(src), duration=3.0, fit="black",
                text=TextOverlay("Grüße vom See", size=90, fade=0.5))
    project = Project(items=[item], base_dir=tmp_path)
    cache = tmp_path / "cache"
    still = render_still(project, item, W, H, tmp_path / "still.png", cache)
    seg = segment_for(project, cache)
    # Nach der Blende sieht das Segment aus wie das Standbild (nur Kompressionsrauschen)
    assert diff(still, frame_at(seg, 1.5, tmp_path / "mid.png")) < 3.0
    # Und der Text ist im Standbild wirklich da
    plain = Item("photo", path=str(src), duration=3.0, fit="black")
    plain_still = render_still(Project(items=[plain], base_dir=tmp_path), plain, W, H,
                               tmp_path / "plain.png", cache)
    assert diff(still, plain_still) > 3.0


@pytest.mark.slow
def test_segment_cache_key_depends_on_fade(synthetic: Path, tmp_path: Path) -> None:
    src = tmp_path / "Querformat Wörthersee.jpg"
    shutil.copy2(synthetic / src.name, src)
    cache = tmp_path / "cache"
    a = Item("photo", path=str(src), duration=1.0, text=TextOverlay("x", fade=0.0))
    b = Item("photo", path=str(src), duration=1.0, text=TextOverlay("x", fade=0.25))
    seg_a = segment_for(Project(items=[a], base_dir=tmp_path), cache)
    seg_b = segment_for(Project(items=[b], base_dir=tmp_path), cache)
    assert seg_a != seg_b
    assert not list(cache.rglob("*.part*"))


# --- Oberfläche -----------------------------------------------------------------------


def test_panel_sets_fade_and_copies_it_to_new_text(qtbot, tmp_path: Path) -> None:  # noqa: ANN001
    project = Project(items=[Item("photo", path="a.jpg", text=TextOverlay("A")),
                             Item("photo", path="b.jpg")], base_dir=tmp_path)
    panel = ItemPanel()
    qtbot.addWidget(panel)
    panel.show()
    changes: list[int] = []
    panel.changed.connect(lambda: changes.append(1))

    panel.set_items(project, [project.items[0]])
    assert panel.fade.value() == 0.5  # Standard
    panel.fade.setValue(1.25)
    assert project.items[0].text.fade == 1.25 and changes  # type: ignore[union-attr]
    panel.fade.setValue(0.0)
    assert project.items[0].text.fade == 0.0  # type: ignore[union-attr]
    assert panel.fade.specialValueText() == "Aus"

    # Mehrere markiert: Änderung gilt für alle, auch für Elemente ohne Text bisher
    panel.set_items(project, list(project.items))
    panel.fade.setValue(0.75)
    assert [it.text.fade for it in project.items if it.text] == [0.75, 0.75]
