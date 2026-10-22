"""Kommandozeile:

python -m slideshow                      Oberfläche starten
python -m slideshow gui [dateien ...]    Oberfläche mit Projekt oder Fotos/Videos starten
python -m slideshow render <projekt.json> [-o out.mp4] [--encoder X] [--preview]
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from slideshow.core.ffmpeg import Cancelled, FFmpegError
from slideshow.core.importer import clip_length
from slideshow.core.project import FPS, Project
from slideshow.render.assemble import (
    FAT32_LIMIT,
    RenderError,
    RenderOptions,
    estimate_size,
    render_project,
)
from slideshow.render.cache import default_cache_dir
from slideshow.render.encoder import ENCODERS, LABELS, detect_encoder


def _fmt_time(seconds: float) -> str:
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:05.2f}"


def cmd_render(ns: argparse.Namespace) -> int:
    project_path = Path(ns.project)
    project = Project.load(project_path)
    output = Path(ns.output) if ns.output else project_path.with_suffix(".mp4")
    opts = RenderOptions(
        output=output,
        encoder=ns.encoder,
        preview=ns.preview,
        first=ns.first,
        last=ns.last,
        cache_dir=Path(ns.cache_dir) if ns.cache_dir else None,
    )

    total = project.total_frames(clip_length) / FPS
    if ns.preview:
        print(f"Vorschau (480p) von {project.name!r}")
    else:
        size = estimate_size(total, 1080 if project.settings.resolution == "1080p" else 2160)
        print(f"Projekt {project.name!r}: {len(project.items)} Elemente, "
              f"Dauer {_fmt_time(total)} ({total:.3f} s)")
        print(f"Geschätzte Dateigröße: höchstens {size / 1024**2:.0f} MB")
        if size > FAT32_LIMIT:
            print("WARNUNG: Größer als 4 GB – passt nicht auf einen FAT32-USB-Stick.")

    last_msg = [""]

    def status(msg: str, frac: float) -> None:
        if msg != last_msg[0]:
            last_msg[0] = msg
            print(f"[{frac * 100:5.1f} %] {msg}", flush=True)

    cancel = threading.Event()
    try:
        result = render_project(project, opts, status=status, cancel=cancel)
    except KeyboardInterrupt:
        cancel.set()
        print("Abgebrochen. Bereits fertige Teile bleiben im Cache.", file=sys.stderr)
        return 130
    except Cancelled:
        print("Abgebrochen.", file=sys.stderr)
        return 130
    except (RenderError, FFmpegError, FileNotFoundError, ValueError) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1

    for w in result.warnings:
        print(f"WARNUNG: {w}")
    for n in result.notes:
        print(f"HINWEIS: {n}")
    print(f"Kodierer: {result.encoder} ({LABELS.get(result.encoder, '')})")
    print(f"Segmente: {result.segments_total} (aus Cache: {result.segments_cached}), "
          f"Abschnitte: {result.blocks_total} (aus Cache: {result.blocks_cached})")
    print(f"Fertig: {result.output} – Dauer {result.duration:.3f} s, "
          f"Renderzeit {result.seconds:.1f} s")
    return 0


def cmd_encoders(ns: argparse.Namespace) -> int:
    cache = Path(ns.cache_dir) if ns.cache_dir else default_cache_dir()
    chosen = detect_encoder(cache)
    for e in ENCODERS:
        mark = "*" if e == chosen else " "
        print(f"{mark} {e:<12} {LABELS[e]}")
    return 0


def cmd_gui(ns: argparse.Namespace) -> int:
    from slideshow.app.main import run_gui

    return run_gui(ns.files)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="slideshow", description="Jahresshow – Slideshow als MP4")
    sub = p.add_subparsers(dest="command")

    g = sub.add_parser("gui", help="Oberfläche starten (Standard)")
    g.add_argument("files", nargs="*", help="Projektdatei oder Fotos/Videos zum Öffnen")
    g.set_defaults(func=cmd_gui)

    r = sub.add_parser("render", help="Projekt als MP4 ausgeben")
    r.add_argument("project", help="Projektdatei (.json)")
    r.add_argument("-o", "--output", help="Ziel-MP4 (Standard: neben der Projektdatei)")
    r.add_argument("--encoder", choices=ENCODERS,
                   help="Videokodierer erzwingen (Standard: automatisch erkennen)")
    r.add_argument("--preview", action="store_true", help="Schnelle Vorschau in 480p")
    r.add_argument("--from", dest="first", type=int, help="Vorschau ab Element Nr.")
    r.add_argument("--to", dest="last", type=int, help="Vorschau bis Element Nr.")
    r.add_argument("--cache-dir", help="Ordner für Zwischendateien")
    r.set_defaults(func=cmd_render)

    e = sub.add_parser("encoders", help="Verfügbare Videokodierer anzeigen")
    e.add_argument("--cache-dir")
    e.set_defaults(func=cmd_encoders)
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:] if argv is None else argv
    ns = build_parser().parse_args(args or ["gui"])
    return int(ns.func(ns))
