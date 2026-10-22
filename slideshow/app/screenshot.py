"""Screenshot-Prüfung des Hauptfensters bei kleiner Bildschirmgröße und Skalierung.

python -m slideshow.app.screenshot --width 1366 --height 768 --scale 1.0 --out out/screens
    [--project projekt.json] [--cache-dir ordner]

Der Bildschirm wird in logischen Pixeln angegeben (1920×1080 bei 150 % = 1280×720). Vom
Bildschirm gehen Taskleiste und Titelleiste ab, der Rest ist der Fensterinhalt. Das Fenster
wird unsichtbar (ohne Anzeige auf dem Bildschirm) aufgebaut, in mehreren Zuständen geprüft
(siehe layoutcheck) und als PNG gespeichert. Ausgabe: JSON mit Problemen und Dateien.

Muss als eigener Prozess laufen, weil die Skalierung vor dem Start von Qt feststehen muss."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

TASKBAR_AND_TITLE = 80  # logische Pixel: Taskleiste (48) und Fenstertitel (ca. 32)


def native_scale() -> float:
    """Skalierung des Hauptbildschirms ohne eigene Vorgabe (eigener Prozess)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("QT_SCALE")}
    code = ("from PySide6.QtGui import QGuiApplication;"
            "app=QGuiApplication([]);print(app.primaryScreen().devicePixelRatio())")
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env=env, check=True, timeout=60)
    return float(res.stdout.strip().splitlines()[-1])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, required=True)
    ap.add_argument("--height", type=int, required=True)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--project")
    ap.add_argument("--cache-dir")
    ap.add_argument("--style", help="Qt-Stil, z. B. windows11 (T490s) oder windowsvista (T450s)")
    ns = ap.parse_args(argv)

    native = native_scale()
    os.environ["QT_SCALE_FACTOR"] = f"{ns.scale / native:.6f}"
    os.environ.pop("QT_QPA_PLATFORM", None)  # echte Windows-Darstellung inkl. Schriften

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from slideshow.app.layoutcheck import clipped_widgets
    from slideshow.app.main import _translate
    from slideshow.app.mainwindow import MainWindow
    from slideshow.app.timeline import ZOOM_MAX, ZOOM_MIN

    app = QApplication([])
    if ns.style:
        app.setStyle(ns.style)
    _translate(app)
    cache = Path(ns.cache_dir) if ns.cache_dir else None
    win = MainWindow(cache_dir=cache, encoder="libx264", open_file=lambda _p: None)
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    size = (ns.width, ns.height - TASKBAR_AND_TITLE)
    win.resize(*size)
    win.show()
    if ns.project:
        win.load_from(Path(ns.project))
    win.video_prep.shutdown()  # keine Umwandlung im Hintergrund während der Prüfung

    def settle(seconds: float = 0.3) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    end = time.monotonic() + 30
    while (not win.thumbs.idle() or win.duration_pending) and time.monotonic() < end:
        settle(0.05)
    settle()

    out = Path(ns.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = f"{ns.width}x{ns.height}@{round(ns.scale * 100)}"
    if ns.style:
        tag += f"-{ns.style}"
    report: dict = {"screen": [ns.width, ns.height], "scale": ns.scale,
                    "window": [win.width(), win.height()],
                    "dpr": win.devicePixelRatioF(), "style": app.style().name(),
                    "min_size": [
                        win.minimumSizeHint().width(), win.minimumSizeHint().height()],
                    "states": {}, "files": []}

    items = win.project.items
    first_of = {k: next((i for i, it in enumerate(items) if it.kind == k), None)
                for k in ("title", "photo", "video")}

    def busy() -> None:
        """Ungünstigster Fall: Hinweis und alle drei Aufgaben gleichzeitig sichtbar."""
        win.notice.show_message(
            "3 Foto(s) und Video(s) hinzugefügt, nach Aufnahmedatum einsortiert.\n"
            "1 doppelte Datei ist in der Zeitleiste mit „doppelt“ markiert.", "warning")
        win.import_row.start("Fotos und Videos werden eingelesen: 120 von 400", lambda: None)
        win.video_row.start("Videos werden für die Show umgewandelt: 2 von 7 "
                            "(„20260714_101500.mp4“), dieses Video noch ca. 12 min", lambda: None)
        win.video_row.update_progress(win.video_row.text, 0.2)
        win.render_row.start("Video wird erstellt: Element 12 von 240 wird vorbereitet",
                             lambda: None)
        win.render_row.update_progress(win.render_row.text, 0.43)
        win.import_row.update_progress(win.import_row.text, 0.3)

    def expanded() -> None:
        win.activity.set_expanded(True)

    def zoom(width: int) -> Callable[[], None]:
        return lambda: win.timeline.set_tile_width(width)

    states = [
        ("element-foto", 0, first_of["photo"], None),
        ("element-video", 0, first_of["video"], busy),
        ("aktivitaet-details", 0, first_of["video"], expanded),
        ("element-titel", 0, first_of["title"], None),
        ("show", 1, None, None),
        ("musik", 2, None, None),
        ("zoom-klein", 0, first_of["photo"], zoom(ZOOM_MIN)),
        ("zoom-gross", 0, first_of["photo"], zoom(ZOOM_MAX)),
    ]
    for name, tab, row, setup in states:
        if row is not None:
            win.timeline.select_rows([row])
        if setup is not None:
            setup()
        win.tabs.setCurrentIndex(tab)
        settle()
        end = time.monotonic() + 20
        while (win.still.pending or not win.thumbs.idle()) and time.monotonic() < end:
            settle(0.05)
        settle(0.2)
        problems = clipped_widgets(win)
        file = out / f"{tag}-{name}.png"
        win.grab().save(str(file))
        report["states"][name] = problems
        report["files"].append(str(file))

    report["problems"] = sorted({p for ps in report["states"].values() for p in ps})
    win.dirty = False
    win.shutdown()
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
