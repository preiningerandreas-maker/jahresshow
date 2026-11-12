"""Zusammensetzen: Segmente kapitelweise in Blöcken von max. 20 per xfade verbinden,
Blöcke per concat (ohne neu zu kodieren) aneinanderhängen, Musik dazumischen.

Damit auch an Blockgrenzen ein Übergang entsteht, nimmt ein Block den Anfang des ersten
Segments des nächsten Blocks mit (nur die Übergangslänge). Der nächste Block beginnt dieses
Segment dann entsprechend später. So ist die Gesamtlänge immer
Summe(Segmentlängen) - Summe(Übergänge)."""

from __future__ import annotations

import contextlib
import logging
import math
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TypeVar

from slideshow.core.chapters import chapter_title, is_chapter_title
from slideshow.core.ffmpeg import Cancelled, FFmpegError, run_ffmpeg
from slideshow.core.importer import (
    DamagedFile,
    check_photo,
    clip_length,
    describe_file,
    media_kind,
)
from slideshow.core.normalize import (
    NormalizeProgress,
    cached_normalized,
    is_running,
    normalize_video,
)
from slideshow.core.project import (
    DAMAGE_MINOR_MIN,
    DAMAGE_USABLE_MIN,
    FPS,
    PREVIEW_SIZE,
    RESOLUTIONS,
    Item,
    Project,
    TimelineEntry,
    readable_at_least,
    readable_text,
)
from slideshow.render.audio import build_music, duck_ranges
from slideshow.render.cache import (
    cleanup_tmp,
    commit_tmp,
    default_cache_dir,
    make_key,
    tmp_path,
)
from slideshow.render.encoder import (
    CPU_ENCODER,
    EncoderChoice,
    bitrates,
    choose_encoder,
    encoder_args,
    is_hardware,
    remember_failure,
)
from slideshow.render.segment import (
    INTERMEDIATE_AUDIO,
    SAMPLES_PER_FRAME,
    RenderConfig,
    render_segment,
)

log = logging.getLogger(__name__)

MAX_BLOCK = 20
AUDIO_BITRATE = 192_000
FAT32_LIMIT = 4 * 1024**3

StatusCallback = Callable[[str, float], None]
"""Wird mit Statustext und Gesamtfortschritt (0..1) aufgerufen."""

T = TypeVar("T")


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
class SkippedFile:
    """Beim Export übersprungene, beschädigte Datei."""

    path: Path
    reason: str
    readable: float | None = 0.0  # lesbarer Anteil (Fotos); None = unbekannt

    @property
    def usable(self) -> bool:
        return readable_at_least(self.readable, DAMAGE_USABLE_MIN)

    def message(self) -> str:
        text = f"{describe_file(self.path)}: {self.reason}"
        if self.usable:
            text += (f" ({readable_text(self.readable)}, in den Eigenschaften "
                     "„Trotzdem verwenden“ möglich)")
        return text


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
    notes: list[str] = field(default_factory=list)  # Hinweise, keine Fehler
    videos_normalized: int = 0  # im Render-Lauf umgewandelte Videos
    skipped: list[SkippedFile] = field(default_factory=list)  # beschädigt, ausgelassen
    # erst beim Export als leicht beschädigt erkannt und trotzdem verwendet
    minor: list[SkippedFile] = field(default_factory=list)
    dropped_titles: list[str] = field(default_factory=list)  # Zwischentitel leerer Kapitel
    # Grafikkarte hat versagt (in diesem Lauf oder früher): Hinweis für die Zusammenfassung
    encoder_note: str | None = None
    fallback_element: int | None = None  # ab diesem Element hat der Prozessor gerechnet

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


def title_text(item: Item) -> str:
    raw = item.text.text.strip() if item.text else ""
    if raw:
        return " ".join(raw.split())
    return chapter_title(item.chapter) if item.chapter else "Zwischentitel"


def without(project: Project, bad: list[Item], dropped_titles: list[str]) -> Project:
    """Elemente weglassen. Bleibt dadurch von einem Kapitel nichts übrig, entfällt auch
    sein Zwischentitel; dessen Text kommt in dropped_titles."""
    ids = {id(it) for it in bad}
    left = [it for it in project.items if id(it) not in ids]
    lost = {it.chapter for it in bad if it.chapter is not None and not is_chapter_title(it)}
    empty = lost - {it.chapter for it in left if not is_chapter_title(it)}
    if empty:
        titles = [it for it in left if is_chapter_title(it) and it.chapter in empty]
        dropped_titles += [title_text(it) for it in titles]
        left = [it for it in left if all(it is not t for t in titles)]
    return replace(project, items=left)


def skip_damaged(project: Project, skipped: list[SkippedFile],
                 dropped_titles: list[str]) -> Project:
    """Als beschädigt bekannte Elemente weglassen (außer „trotzdem verwenden“)."""
    bad = [it for it in project.items if it.path and it.skip_damaged]
    for it in bad:
        assert it.path is not None and it.damage is not None
        add_skipped(skipped, SkippedFile(project.resolve(it.path), it.damage,
                                         it.damage_readable))
    return without(project, bad, dropped_titles)


def add_skipped(skipped: list[SkippedFile], new: SkippedFile) -> None:
    if all(s.path != new.path for s in skipped):
        skipped.append(new)


def damaged_at_render(path: Path, reason: str) -> SkippedFile:
    """Erst beim Export als beschädigt erkannt (z. B. ältere Projekte): lesbaren Anteil
    bestimmen, damit die Oberfläche „trotzdem verwenden“ anbieten kann."""
    readable = 0.0
    if media_kind(path) == "photo":
        with contextlib.suppress(OSError):
            readable = check_photo(path).readable
    return SkippedFile(path, reason, readable)


class EncoderFallback:
    """Kodierer eines Laufs. Versagt die Grafikkarte bei einem Schritt, wird er mit dem
    Prozessor wiederholt, und der Rest des Laufs bleibt beim Prozessor. Abbruch nur, wenn
    auch der Prozessor scheitert. Die Panne wird für dieses Gerät gemerkt."""

    def __init__(self, encoder: str, cache_dir: Path) -> None:
        self.encoder = encoder
        self.cache_dir = cache_dir
        self.element: int | None = None
        self.note: str | None = None

    def run(self, step: Callable[[str], T], element: int, source: str) -> T:
        """step(kodierer) ausführen; element und source benennen den Schritt für Meldungen."""
        failed = self.encoder
        try:
            return step(failed)
        except FFmpegError as err:
            if not is_hardware(failed):
                raise
            log.warning("%s hat bei Element %d %s versagt, Wiederholung mit dem Prozessor: %s",
                        failed, element, source, err)
            hw_error = err
        result = step(CPU_ENCODER)  # scheitert auch das, bricht der Export ab
        self.failed(failed, hw_error, element, source)
        return result

    def failed(self, encoder: str, err: FFmpegError, element: int, source: str) -> None:
        """Der Hardware-Kodierer hat versagt, der Prozessor hat es geschafft."""
        log.warning("%s hat bei Element %d %s versagt, weiter mit dem Prozessor: %s",
                    encoder, element, source, err)
        self.encoder = CPU_ENCODER
        if self.element is None:
            self.element = element
            self.note = (f"Grafikkarte hat beim Kodieren versagt, ab Element {element} mit "
                         f"Prozessor weitergerechnet. Betroffen war {source}. Beim nächsten "
                         "Export rechnet gleich der Prozessor.")
        try:
            remember_failure(self.cache_dir, encoder)
        except OSError as e:
            log.warning("Panne der Grafikkarte konnte nicht gespeichert werden: %s", e)


def render_project(
    project: Project,
    opts: RenderOptions,
    *,
    status: StatusCallback | None = None,
    cancel: threading.Event | None = None,
) -> RenderResult:
    started = time.perf_counter()
    # Nummern wie in der Zeitleiste, auch bei Vorschau-Abschnitt und übersprungenen Elementen
    numbers = {id(it): i + 1 for i, it in enumerate(project.items)}
    project = select_section(project, opts.first, opts.last)
    if not project.items:
        raise RenderError("Das Projekt enthält keine Elemente.")
    for item in project.items:
        if item.path and not project.resolve(item.path).is_file():
            raise RenderError(
                f"Datei nicht gefunden: {describe_file(project.resolve(item.path))}")
    for t in project.music.tracks:
        if not project.resolve(t).is_file():
            raise RenderError(f"Musikdatei nicht gefunden: {describe_file(project.resolve(t))}")
    skipped: list[SkippedFile] = []
    dropped_titles: list[str] = []
    project = skip_damaged(project, skipped, dropped_titles)

    def check_left() -> None:
        if not project.items:
            raise RenderError("Alle Elemente sind beschädigt, es bleibt nichts für ein Video:\n"
                              + "\n".join(f"• {s.message()}" for s in skipped))

    check_left()

    cache_dir = opts.cache_dir or default_cache_dir()
    check_free_space(project, cache_dir)
    if opts.preview:
        width, height = PREVIEW_SIZE
        # 480p ist mit dem Prozessor schnell genug und läuft überall gleich
        choice = (choose_encoder(cache_dir, opts.encoder) if opts.encoder
                  else EncoderChoice(CPU_ENCODER))
    else:
        width, height = RESOLUTIONS[project.settings.resolution]
        choice = choose_encoder(cache_dir, opts.encoder)
    gpu = EncoderFallback(choice.name, cache_dir)
    cfg = RenderConfig(width, height, choice.name, cache_dir, allow_original=opts.preview)

    result = RenderResult(opts.output, choice.name, 0, skipped=skipped,
                          dropped_titles=dropped_titles, encoder_note=choice.note)

    def number(item: Item) -> int:
        return numbers[id(item)]

    def source(item: Item) -> str:
        return describe_file(project.resolve(item.path)) if item.path else "eine Titelkarte"

    def emit(msg: str, frac: float) -> None:
        if status:
            status(msg, min(1.0, max(0.0, frac)))

    def check_cancel() -> None:
        if cancel is not None and cancel.is_set():
            raise Cancelled()

    # Videos ohne Umwandlung im Cache: Export wandelt sie zuerst selbst um, die Vorschau
    # nimmt das Original und vermerkt das
    missing: list[Path] = []
    for path in dict.fromkeys(it.path for it in project.items if it.kind == "video"):
        assert path is not None
        src = project.resolve(path)
        if src not in missing and cached_normalized(src, cache_dir) is None:
            missing.append(src)
    if opts.preview:
        for src in missing:
            note = (f"Vorschau verwendet das Original von „{src.name}“, weil es noch nicht "
                    "umgewandelt ist. Farben und Bildrate können im Export abweichen.")
            log.info(note)
            result.notes.append(note)
        missing = []

    # Fortschritt grob gewichtet: Umwandeln 30 % (falls nötig), danach Segmente 45 %,
    # Blöcke 45 %, Ton und Ausgabe 10 % vom Rest
    norm_share = 0.3 if missing else 0.0

    def report(msg: str, frac: float) -> None:
        emit(msg, norm_share + (1 - norm_share) * frac)

    for k, src in enumerate(missing):
        check_cancel()
        label = f"Video {k + 1} von {len(missing)} wird umgewandelt: {src.name}"
        if is_running(src, cache_dir):
            emit(f"{label} (wartet auf laufende Umwandlung)", norm_share * k / len(missing))

        def norm_progress(p: NormalizeProgress, k: int = k, label: str = label) -> None:
            # Restzeit in ganzen Minuten, damit die Meldung nicht ständig wechselt
            eta = f" (noch ca. {math.ceil(p.eta / 60)} min)" if p.eta is not None else ""
            emit(f"{label}{eta}",
                 norm_share * (k + p.fraction) / len(missing))

        emit(label, norm_share * k / len(missing))
        users = [it for it in project.items if it.path and project.resolve(it.path) == src]

        def norm_failed(err: FFmpegError, failed: str = gpu.encoder,
                        element: int = number(users[0]), src: Path = src) -> None:
            gpu.failed(failed, err, element, describe_file(src))

        try:
            normalize_video(src, cache_dir, encoder=gpu.encoder, progress=norm_progress,
                            cancel=cancel, on_encoder_failure=norm_failed)
        except FFmpegError as err:
            # Video lässt sich nicht lesen: überspringen statt den ganzen Export abbrechen
            log.warning("Video übersprungen (Element %d %s): %s", number(users[0]),
                        describe_file(src), err)
            add_skipped(skipped, SkippedFile(
                src, "Video lässt sich nicht umwandeln (beschädigt oder unbekanntes Format)"))
            project = without(project, users, dropped_titles)
            check_left()
            continue
        result.videos_normalized += 1

    # Segmente. Ist ein Bild beschädigt, fällt sein Element heraus und die Zeitleiste wird
    # neu berechnet; schon fertige Segmente kommen dann aus dem Cache. Ist es nur leicht
    # beschädigt (vollständig lesbar), wird es mit seinem lesbaren Teil neu versucht.
    while True:
        timeline = project.timeline(clip_length)
        total = timeline[-1].start_frame + timeline[-1].frames
        seg_paths: dict[int, Path] = {}
        segments_cached = 0
        done = 0
        bad: Item | None = None
        marked: tuple[Item, Item] | None = None  # (Element, Element mit Schadensangabe)
        for e in timeline:
            check_cancel()
            label = f"Element {e.index + 1} von {len(timeline)} wird vorbereitet"
            base = done

            def seg_progress(sec: float, base: int = base, n: int = e.frames,
                             label: str = label, total: int = total) -> None:
                report(label, 0.45 * (base + min(sec * FPS, n)) / total)

            report(label, 0.45 * base / total)

            def segment(enc: str, e: TimelineEntry = e, project: Project = project,
                        seg_progress: Callable[[float], None] = seg_progress
                        ) -> tuple[Path, bool]:
                return render_segment(project, e, replace(cfg, encoder=enc),
                                      progress=seg_progress, cancel=cancel)

            try:
                path, cached = gpu.run(segment, number(e.item), source(e.item))
            except DamagedFile as err:
                info = damaged_at_render(err.path, err.reason)
                if e.item.damage is None and readable_at_least(info.readable,
                                                               DAMAGE_MINOR_MIN):
                    log.info("Leicht beschädigt, wird verwendet: %s", err)
                    add_skipped(result.minor, info)
                    marked = (e.item, replace(e.item, damage=info.reason,
                                              damage_readable=info.readable))
                    numbers[id(marked[1])] = number(e.item)
                else:
                    log.warning("Element übersprungen: %s", err)
                    add_skipped(skipped, info)
                    bad = e.item
                break
            except (FFmpegError, OSError, ValueError) as err:
                raise RenderError(
                    f"Fehler bei Element {number(e.item)} {source(e.item)}: {err}") from err
            seg_paths[e.index] = path
            segments_cached += cached
            done += e.frames
        if marked is not None:
            old, new = marked
            project = replace(project, items=[new if it is old else it for it in project.items])
            continue
        if bad is None:
            break
        project = without(project, [bad], dropped_titles)
        check_left()

    blocks = plan_blocks(timeline, opts.max_block)
    result.frames = total
    result.segments_total, result.segments_cached = len(timeline), segments_cached
    result.blocks_total = len(blocks)
    if estimate_size(total / FPS, height) > FAT32_LIMIT:
        result.warnings.append(
            "Die Datei wird voraussichtlich größer als 4 GB und passt nicht auf einen "
            "USB-Stick mit FAT32."
        )

    block_dir = cache_dir / "blocks"
    block_dir.mkdir(parents=True, exist_ok=True)

    def encode_block(enc: str, bi: int, block: Block, base: int) -> tuple[Path, bool]:
        final_args = encoder_args(enc, height)
        key = make_key({
            "type": "block",
            "segments": [seg_paths[p.entry.index].stem for p in block.parts],
            "parts": [[p.skip, p.head, p.entry.transition, p.entry.transition_frames]
                      for p in block.parts],
            "encoder": final_args,
            "audio": INTERMEDIATE_AUDIO,
        })
        out = block_dir / f"{key}.mp4"
        if out.is_file():
            return out, True
        label = f"Abschnitt {bi + 1} von {len(blocks)} wird zusammengesetzt"

        def blk_progress(sec: float) -> None:
            report(label, 0.45 + 0.45 * (base + min(sec * FPS, block.frames)) / total)

        report(label, 0.45 + 0.45 * base / total)
        args: list[str] = []
        for p in block.parts:
            args += ["-threads", "2", "-i", str(seg_paths[p.entry.index])]
        tmp = tmp_path(out)
        try:
            run_ffmpeg([*args, "-filter_complex", block_graph(block),
                        "-map", "[vout]", "-map", "[aout]",
                        "-frames:v", str(block.frames), *final_args,
                        *INTERMEDIATE_AUDIO, str(tmp)],
                       progress=blk_progress, cancel=cancel)
            commit_tmp(tmp, out)
        finally:
            cleanup_tmp(tmp)
        return out, False

    while True:
        block_encoder = gpu.encoder
        block_files: list[Path] = []
        result.blocks_cached = 0
        done = 0
        for bi, block in enumerate(blocks):
            check_cancel()
            # Elemente, die dieser Abschnitt zeigt (ohne Vorgriff auf den nächsten)
            shown = [p.entry.item for p in block.parts if p.head is None]
            first = shown[0]
            span = (f"Element {number(first)}" if len(shown) == 1
                    else f"Element {number(first)} bis {number(shown[-1])}")

            def step(enc: str, bi: int = bi, block: Block = block,
                     base: int = done) -> tuple[Path, bool]:
                return encode_block(enc, bi, block, base)

            try:
                out, hit = gpu.run(step, number(first), source(first))
            except (FFmpegError, OSError) as err:
                raise RenderError(f"Fehler bei Abschnitt {bi + 1} ({span}, beginnt mit "
                                  f"{source(first)}): {err}") from err
            block_files.append(out)
            result.blocks_cached += hit
            done += block.frames
        if gpu.encoder == block_encoder:
            break
        # Die Grafikkarte hat erst bei den Abschnitten versagt: Alle Abschnitte müssen vom
        # selben Kodierer stammen, sonst passen sie beim Aneinanderhängen ohne neu zu
        # kodieren nicht sicher zusammen. Schon mit dem Prozessor fertige kommen aus dem Cache.
        log.info("Abschnitte werden mit dem Prozessor neu zusammengesetzt")

    check_cancel()
    report("Musik wird vorbereitet", 0.9)
    music = build_music(project, total / FPS, duck_ranges(project, timeline), cache_dir,
                        cancel=cancel)

    check_cancel()
    report("Video wird gespeichert", 0.93)
    work = cache_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    concat_key = make_key({"blocks": [b.stem for b in block_files]})
    concat_list = tmp_path(work / f"concat_{concat_key}.txt")
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
        # Die Farbangabe (BT.709, TV-Bereich) im Videostrom nachsetzen, ohne neu zu kodieren:
        # Manche Grafikkarten-Kodierer (AMD AMF) schreiben sie trotz Parametern nicht.
        run_ffmpeg([*args, "-c:v", "copy", "-bsf:v",
                    "h264_metadata=colour_primaries=1:transfer_characteristics=1"
                    ":matrix_coefficients=1:video_full_range_flag=0",
                    "-c:a", "aac", "-b:a", str(AUDIO_BITRATE),
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
    result.encoder = gpu.encoder
    if gpu.note is not None:
        result.encoder_note, result.fallback_element = gpu.note, gpu.element
    result.seconds = time.perf_counter() - started
    return result
