"""Screenshot-Tests: Hauptfenster bei 1366×768 (100 %) und 1920×1080 mit 150 % Skalierung
(= 1280×720 logisch) ohne abgeschnittene Bedienelemente. Die Bilder landen in out/screens/.

Jede Größe läuft in einem eigenen Prozess mit der echten Windows-Darstellung (Schriften,
Skalierung), siehe slideshow/app/screenshot.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from slideshow.core.chapters import build_items
from slideshow.core.importer import import_media
from slideshow.core.project import Item, Project, TextOverlay

ROOT = Path(__file__).resolve().parents[1]
SCREENS = ROOT / "out" / "screens"

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-Darstellung nötig")


@pytest.fixture(scope="module")
def screen_project(synthetic: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("screens") / "Urlaub Wörthersee 2026"
    folder.mkdir()
    res = import_media([synthetic])
    title = Item("title", duration=5.0, text=TextOverlay(
        "Jahresrückblick 2026 – Grüße aus Kärnten", position="center", size=110, bold=True))
    p = Project(name="Urlaub Wörthersee 2026", items=[title, *build_items(res.media)])
    p.music.tracks = [str(synthetic / "Musik 1 – Frühling.mp3"),
                      str(synthetic / "Musik 2 – Sommer.m4a")]
    target = folder / "Projekt Wörthersee.json"
    p.save(target)
    return target


@pytest.mark.parametrize(("width", "height", "scale"), [(1366, 768, 1.0), (1280, 720, 1.5)],
                         ids=["1366x768", "1920x1080-150pct"])
def test_window_fits_without_clipped_controls(width: int, height: int, scale: float,
                                              screen_project: Path, tmp_path: Path) -> None:
    """Das Theme legt Fusion als Grundlage fest: Windows 10 und 11 sehen gleich aus."""
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    env["PYTHONIOENCODING"] = "utf-8"
    res = subprocess.run(
        [sys.executable, "-m", "slideshow.app.screenshot", "--width", str(width),
         "--height", str(height), "--scale", str(scale), "--out", str(SCREENS),
         "--project", str(screen_project), "--cache-dir", str(tmp_path / "cache")],
        cwd=ROOT, env=env, capture_output=True, encoding="utf-8", timeout=300, check=False)
    assert res.returncode == 0, res.stderr[-3000:]
    report = json.loads(res.stdout.strip().splitlines()[-1])
    from slideshow.app.screenshot import TASKBAR_AND_TITLE

    # Fenster hat genau die verfügbare Größe (nicht vergrößert, weil der Inhalt nicht passt)
    assert report["window"] == [width, height - TASKBAR_AND_TITLE]
    assert report["dpr"] == pytest.approx(scale)
    assert report["min_size"][0] <= width and report["min_size"][1] <= height - TASKBAR_AND_TITLE
    assert report["problems"] == [], "\n".join(report["problems"])
    # „Video erstellen“ in jedem Zustand sichtbar, nicht im Überlaufmenü
    assert len(report["export_button"]) == 8
    for name, state in report["export_button"].items():
        assert state == {"visible": True, "in_window": True, "in_toolbar": True,
                         "in_overflow": False}, name
    # Element (Foto, Video mit allen Aufgaben, aufgeklappt, Titel), Show, Musik, Zoom klein/groß
    assert len(report["files"]) == 8
    for f in report["files"]:
        assert Path(f).is_file() and Path(f).stat().st_size > 10_000


def test_layout_check_detects_clipping(qtbot, tmp_path: Path) -> None:  # noqa: ANN001
    """Gegenprobe: in einem viel zu kleinen Fenster meldet die Prüfung Probleme."""
    from slideshow.app.layoutcheck import clipped_widgets
    from slideshow.app.mainwindow import MainWindow

    w = MainWindow(cache_dir=tmp_path / "cache", encoder="libx264")
    qtbot.addWidget(w)
    w.setMinimumSize(1, 1)
    w.resize(560, 300)
    w.show()
    qtbot.wait(100)
    problems = clipped_widgets(w)
    w.shutdown()
    assert any("Werkzeugleiste" in p for p in problems)
