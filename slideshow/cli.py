"""Kommandozeile:

python -m slideshow                      Oberfläche starten
python -m slideshow gui [dateien ...]    Oberfläche mit Projekt oder Fotos/Videos starten
python -m slideshow render <projekt.json> [-o out.mp4] [--encoder X] [--preview]
Flashback.exe --render <projekt.json> [-o out.mp4]    dasselbe im fertigen Paket
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
from slideshow.render.encoder import (
    ENCODERS,
    GPU_MODES,
    LABELS,
    choose_encoder,
    failed_text,
    load_prefs,
)


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
    for sk in result.skipped:
        print(f"ÜBERSPRUNGEN (beschädigt): {sk.message()}")
    for title in result.dropped_titles:
        print(f"ENTFÄLLT (Kapitel leer): Zwischentitel „{title}“")
    for n in result.notes:
        print(f"HINWEIS: {n}")
    if result.encoder_note:
        print(f"HINWEIS: {result.encoder_note}")
    print(f"Kodierer: {result.encoder} ({LABELS.get(result.encoder, '')})")
    print(f"Segmente: {result.segments_total} (aus Cache: {result.segments_cached}), "
          f"Abschnitte: {result.blocks_total} (aus Cache: {result.blocks_cached})")
    print(f"Fertig: {result.output} – Dauer {result.duration:.3f} s, "
          f"Renderzeit {result.seconds:.1f} s")
    return 0


def cmd_encoders(ns: argparse.Namespace) -> int:
    cache = Path(ns.cache_dir) if ns.cache_dir else default_cache_dir()
    prefs = load_prefs(cache)
    chosen = choose_encoder(cache).name
    for e in ENCODERS:
        mark = "*" if e == chosen else " "
        failed = " (hat beim Kodieren versagt)" if e in prefs.failed else ""
        print(f"{mark} {e:<12} {LABELS[e]}{failed}")
    print(f"Grafikkarte verwenden: {GPU_MODES[prefs.mode]}")
    if prefs.failed:
        print(f"Gemerkte Panne: {failed_text(prefs)}")
    return 0


def cmd_gui(ns: argparse.Namespace) -> int:
    from slideshow.app.main import run_gui

    return run_gui(ns.files)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="slideshow", description="Flashback – Slideshow als MP4")
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


COMMANDS = ("gui", "render", "encoders")


def _attach_console() -> None:
    """Das gepackte Programm ist ein Fensterprogramm: Ohne Umleitung gibt es keine Ausgabe.
    Für die Kommandozeile in die Konsole schreiben, aus der es gestartet wurde."""
    if sys.platform != "win32" or (sys.stdout is not None and sys.stderr is not None):
        return
    import ctypes

    if not ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
        return
    if sys.stdout is None:
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = sys.stdout


def normalize_args(args: list[str]) -> list[str]:
    """Aufrufe des fertigen Pakets auf die Befehle abbilden."""
    if not args:
        return ["gui"]
    if args[0] == "--render":  # Kurzform: Flashback.exe --render projekt.json
        return ["render", *args[1:]]
    if args[0] not in COMMANDS and not args[0].startswith("-"):
        # Dateien ohne Befehl, z. B. ein Projekt im Explorer auf die exe gezogen
        return ["gui", *args]
    return list(args)


def main(argv: list[str] | None = None) -> int:
    args = normalize_args(sys.argv[1:] if argv is None else list(argv))
    if args[0] != "gui":
        _attach_console()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ns = build_parser().parse_args(args)
    return int(ns.func(ns))
