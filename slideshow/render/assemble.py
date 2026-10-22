"""Zusammensetzen: Segmente kapitelweise in Blöcken von max. 20 per xfade verbinden,
Blöcke per concat (ohne neu zu kodieren) aneinanderhängen, Musik dazumischen.

Damit auch an Blockgrenzen ein Übergang entsteht, nimmt ein Block den Anfang des ersten
Segments des nächsten Blocks mit (nur die Übergangslänge). Der nächste Block beginnt dieses
Segment dann entsprechend später. So ist die Gesamtlänge immer
Summe(Segmentlängen) - Summe(Übergänge)."""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from slideshow.core.ffmpeg import Cancelled, run_ffmpeg
from slideshow.core.importer import clip_length
from slideshow.core.project import FPS, PREVIEW_SIZE, RESOLUTIONS, Project, TimelineEntry
from slideshow.render.audio import build_music, duck_ranges
from slideshow.render.cache import (
    cleanup_tmp,
    default_cache_dir,
    make_key,
    tmp_path,
)
from slideshow.render.encoder import bitrates, detect_encoder, encoder_args
from slideshow.render.segment import SAMPLES_PER_FRAME, RenderConfig, render_segment

MAX_BLOCK = 20
AUDIO_BITRATE = 192_000
FAT32_LIMIT = 4 * 1024**3

StatusCallback = Callable[[str, float], None]
"""Wird mit Statustext und Gesamtfortschritt (0..1) aufgerufen."""


class RenderError(RuntimeError):
    """Verständliche Fehlermeldung für den Nutzer."""


@dataclass
class BlockPart:
    entry: TimelineEntry
    skip: int = 0  # Bilder am Anfang, die schon im vorigen Block gezeigt wurden
    head: int | None = None  # nur die ersten Bilder (Vorgriff auf den nächsten Block)

    @property
    def frames(self) -> int:
        return self.head if self.head is not None else self.entry.frames - self.skip


@dataclass
class Block:
    parts: list[BlockPart]

    @property
    def frames(self) -> int:
        total = self.parts[0].frames
        for p in self.parts[1:]:
            total += p.frames - p.entry.transition_frames
        return total


def plan_blocks(timeline: list[TimelineEntry], max_block: int = MAX_BLOCK) -> list[Block]:
    """Teilt die Zeitleiste in Blöcke: neue Gruppe bei jedem Kapitelwechsel, max. max_block."""
    groups: list[list[TimelineEntry]] = []
    for e in timeline:
        if (
            not groups
            or len(groups[-1]) >= max_block
            or e.item.chapter != groups[-1][-1].item.chapter
        ):
            groups.append([])
        groups[-1].append(e)

    blocks = []
    for gi, group in enumerate(groups):
        first = group[0]
        parts = [BlockPart(first, skip=first.transition_frames if gi > 0 else 0)]
        parts += [BlockPart(e) for e in group[1:]]
        if gi + 1 < len(groups):
            nxt = groups[gi + 1][0]
            if nxt.transition_frames > 0:
                parts.append(BlockPart(nxt, head=nxt.transition_frames))
        blocks.append(Block(parts))
    return blocks


def block_graph(block: Block) -> str:
    """Filtergraph für einen Block. Eingang i = Segment von block.parts[i]."""
    graph = []
    for i, p in enumerate(block.parts):
        if p.head is not None:
            vtrim = f"trim=end_frame={p.head},"
            atrim = f"atrim=end_sample={p.head * SAMPLES_PER_FRAME},"
        elif p.skip:
            vtrim = f"trim=start_frame={p.skip},"
            atrim = f"atrim=start_sample={p.skip * SAMPLES_PER_FRAME},"
        else:
            vtrim = atrim = ""
        graph.append(f"[{i}:v]{vtrim}settb=1/{FPS},setpts=N[v{i}]")
        graph.append(f"[{i}:a]{atrim}asetpts=N/SR/TB[a{i}]")

    cur_v, cur_a = "v0", "a0"
    length = block.parts[0].frames
    for i, p in enumerate(block.parts[1:], start=1):
        t = p.entry.transition_frames
        if t > 0:
            graph.append(
                f"[{cur_v}][v{i}]xfade=transition={p.entry.transition}:"
                f"duration={t / FPS:.6f}:offset={(length - t) / FPS:.6f}[x{i}]"
            )
            graph.append(f"[{cur_a}][a{i}]acrossfade=d={t / FPS:.6f}:c1=tri:c2=tri[y{i}]")
        else:
            # concat liefert Zeitbasis 1/1000000; ein späteres xfade braucht wieder 1/30
            graph.append(f"[{cur_v}][{cur_a}][v{i}][a{i}]concat=n=2:v=1:a=1[c{i}][y{i}]")
            graph.append(f"[c{i}]settb=1/{FPS},setpts=N[x{i}]")
        cur_v, cur_a = f"x{i}", f"y{i}"
        length += p.frames - t
    graph.append(f"[{cur_v}]null[vout]")
    graph.append(f"[{cur_a}]anull[aout]")
    return ";".join(graph)


def _concat_line(path: Path) -> str:
    return "file '" + path.resolve().as_posix().replace("'", "'\\''") + "'"


@dataclass
class RenderOptions:
    output: Path
    encoder: str | None = None
    preview: bool = False
    first: int | None = None  # Vorschau-Abschnitt: Element von (1-basiert, inklusive)
    last: int | None = None  # bis
    cache_dir: Path | None = None
    max_block: int = MAX_BLOCK


@dataclass
class RenderResult:
    output: Path
    encoder: str
    frames: int
    segments_total: int = 0
    segments_cached: int = 0
    blocks_total: int = 0
    blocks_cached: int = 0
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.frames / FPS


def select_section(project: Project, first: int | None, last: int | None) -> Project:
    if first is None and last is None:
        return project
    lo = max(1, first or 1)
    hi = min(len(project.items), last or len(project.items))
    if lo > hi:
        raise RenderError("Der gewählte Abschnitt enthält keine Elemente.")
    return replace(project, items=project.items[lo - 1 : hi])


def estimate_size(seconds: float, height: int) -> int:
    """Obergrenze der Dateigröße in Bytes (Maximalbitrate Video + Ton)."""
    _, peak = bitrates(height)
    return int(seconds * (peak + AUDIO_BITRATE) / 8)


def check_free_space(project: Project, cache_dir: Path) -> None:
    """Faustregel: 3× Quellgröße muss im Cache-/Temp-Ordner frei sein."""
    total = 0
    for item in project.items:
        if item.path:
            p = project.resolve(item.path)
            if p.is_file():
                total += p.stat().st_size
    needed = 3 * total
    cache_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(cache_dir).free
    if free < needed:
        raise RenderError(
            f"Zu wenig freier Speicherplatz in {cache_dir}: benötigt etwa "
            f"{needed / 1024**3:.1f} GB, frei sind {free / 1024**3:.1f} GB."
        )


def render_project(
    project: Project,
    opts: RenderOptions,
    *,
    status: StatusCallback | None = None,
    cancel: threading.Event | None = None,
) -> RenderResult:
    started = time.perf_counter()
    project = select_section(project, opts.first, opts.last)
    if not project.items:
        raise RenderError("Das Projekt enthält keine Elemente.")
    for item in project.items:
        if item.path and not project.resolve(item.path).is_file():
            raise RenderError(f"Datei nicht gefunden: {project.resolve(item.path)}")
    for t in project.music.tracks:
        if not project.resolve(t).is_file():
            raise RenderError(f"Musikdatei nicht gefunden: {project.resolve(t)}")

    cache_dir = opts.cache_dir or default_cache_dir()
    check_free_space(project, cache_dir)
    if opts.preview:
        width, height = PREVIEW_SIZE
        # 480p ist mit dem Prozessor schnell genug und läuft überall gleich
        encoder = detect_encoder(cache_dir, opts.encoder) if opts.encoder else "libx264"
    else:
        width, height = RESOLUTIONS[project.settings.resolution]
        encoder = detect_encoder(cache_dir, opts.encoder)
    cfg = RenderConfig(width, height, encoder, cache_dir)

    timeline = project.timeline(clip_length)
    total = timeline[-1].start_frame + timeline[-1].frames
    blocks = plan_blocks(timeline, opts.max_block)
    result = RenderResult(opts.output, encoder, total, len(timeline), 0, len(blocks), 0)
    if estimate_size(total / FPS, height) > FAT32_LIMIT:
        result.warnings.append(
            "Die Datei wird voraussichtlich größer als 4 GB und passt nicht auf einen "
            "USB-Stick mit FAT32."
        )

    # Fortschritt grob gewichtet: Segmente 45 %, Blöcke 45 %, Ton und Ausgabe 10 %
    def report(msg: str, frac: float) -> None:
        if status:
            status(msg, min(1.0, max(0.0, frac)))

    def check_cancel() -> None:
        if cancel is not None and cancel.is_set():
            raise Cancelled()

    seg_paths: dict[int, Path] = {}
    done = 0
    for e in timeline:
        check_cancel()
        label = f"Element {e.index + 1} von {len(timeline)} wird vorbereitet"
        base = done

        def seg_progress(sec: float, base: int = base, n: int = e.frames,
                         label: str = label) -> None:
            report(label, 0.45 * (base + min(sec * FPS, n)) / total)

        report(label, 0.45 * base / total)
        path, cached = render_segment(project, e, cfg, progress=seg_progress, cancel=cancel)
        seg_paths[e.index] = path
        result.segments_cached += cached
        done += e.frames

    final_args = encoder_args(encoder, height)
    block_files: list[Path] = []
    block_dir = cache_dir / "blocks"
    block_dir.mkdir(parents=True, exist_ok=True)
    done = 0
    for bi, block in enumerate(blocks):
        check_cancel()
        key = make_key({
            "type": "block",
            "segments": [seg_paths[p.entry.index].stem for p in block.parts],
            "parts": [[p.skip, p.head, p.entry.transition, p.entry.transition_frames]
                      for p in block.parts],
            "encoder": final_args,
        })
        out = block_dir / f"{key}.mp4"
        block_files.append(out)
        label = f"Abschnitt {bi + 1} von {len(blocks)} wird zusammengesetzt"
        if out.is_file():
            result.blocks_cached += 1
        else:
            base = done

            def blk_progress(sec: float, base: int = base, n: int = block.frames,
                             label: str = label) -> None:
                report(label, 0.45 + 0.45 * (base + min(sec * FPS, n)) / total)

            report(label, 0.45 + 0.45 * base / total)
            args: list[str] = []
            for p in block.parts:
                args += ["-threads", "2", "-i", str(seg_paths[p.entry.index])]
            tmp = tmp_path(out)
            try:
                run_ffmpeg([*args, "-filter_complex", block_graph(block),
                            "-map", "[vout]", "-map", "[aout]",
                            "-frames:v", str(block.frames), *final_args,
                            "-c:a", "flac", str(tmp)],
                           progress=blk_progress, cancel=cancel)
                tmp.replace(out)
            finally:
                cleanup_tmp(tmp)
        done += block.frames

    check_cancel()
    report("Musik wird vorbereitet", 0.9)
    music = build_music(project, total / FPS, duck_ranges(project, timeline), cache_dir,
                        cancel=cancel)

    check_cancel()
    report("Video wird gespeichert", 0.93)
    work = cache_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    concat_list = work / f"concat_{make_key({'blocks': [b.stem for b in block_files]})}.txt"
    concat_list.write_text("\n".join(_concat_line(b) for b in block_files) + "\n", "utf-8")

    output = opts.output
    output.parent.mkdir(parents=True, exist_ok=True)
    args = ["-f", "concat", "-safe", "0", "-i", str(concat_list)]
    if music is not None:
        args += ["-i", str(music), "-filter_complex",
                 "[0:a][1:a]amix=inputs=2:duration=first:normalize=0,"
                 "alimiter=limit=0.97:level=0[aout]",
                 "-map", "0:v", "-map", "[aout]"]
    else:
        args += ["-map", "0:v", "-map", "0:a"]
    tmp = tmp_path(output)
    try:
        run_ffmpeg([*args, "-c:v", "copy", "-c:a", "aac", "-b:a", str(AUDIO_BITRATE),
                    "-ar", "48000", "-ac", "2", "-movflags", "+faststart",
                    "-f", "mp4", str(tmp)],
                   progress=lambda s: report("Video wird gespeichert",
                                             0.93 + 0.07 * s * FPS / total),
                   cancel=cancel)
        tmp.replace(output)
    finally:
        cleanup_tmp(tmp)
        cleanup_tmp(concat_list)

    report("Fertig", 1.0)
    result.seconds = time.perf_counter() - started
    return result
