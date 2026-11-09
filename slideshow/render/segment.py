"""Ein Segment pro Element: fertiges 1920×1080-Bild inkl. Text, 30 fps, Ton als FLAC (Stille
bei Fotos). Übergänge entstehen erst beim Zusammensetzen der Blöcke."""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image

from slideshow.core.ffmpeg import ProgressCallback, run_ffmpeg
from slideshow.core.importer import open_photo, video_info
from slideshow.core.normalize import cached_normalized, normalize_video, to_bt709_scale
from slideshow.core.photoedit import apply_crop, draft_size
from slideshow.core.project import FPS, Item, Project, TimelineEntry
from slideshow.render.cache import (
    cleanup_tmp,
    commit_tmp,
    file_fingerprint,
    make_key,
    tmp_path,
)
from slideshow.render.encoder import encoder_args
from slideshow.render.text import font_file, save_text_png

SAMPLE_RATE = 48000
SAMPLES_PER_FRAME = SAMPLE_RATE // FPS  # 1600, dadurch sind Ton und Bild immer gleich lang


@dataclass(frozen=True)
class RenderConfig:
    width: int
    height: int
    encoder: str
    cache_dir: Path
    # Nur 480p-Vorschau: Original verwenden, wenn die Umwandlung noch fehlt
    allow_original: bool = False

    @property
    def segment_dir(self) -> Path:
        return self.cache_dir / "segments"

    @property
    def work_dir(self) -> Path:
        return self.cache_dir / "work"


def to_bt709(assume_bt709: bool = False) -> str:
    """Einheitliches Ausgabeformat: bt709, begrenzter Bereich, yuv420p, quadratische Pixel.
    assume_bt709: Quelle ohne Farbangaben wird als bt709 gelesen (FFmpeg nähme sonst bt601)."""
    return (
        f"{to_bt709_scale(assume_bt709)},"
        # Farbangaben fest setzen, sonst bleiben sie bei Fotos/Handyvideos "unbekannt"
        "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv,"
        "setsar=1"
    )


# Text-PNG (RGBA) ausdrücklich mit bt709 nach YUV; overlay nähme sonst bt601
TEXT_TO_YUV = "format=rgba,scale=out_color_matrix=bt709:out_range=tv,format=yuva420p"


def fit_filter(fit: str, w: int, h: int, src: str, out: str) -> str:
    """Formatmodus als Filtergraph-Abschnitt von [src] nach [out]."""
    if fit == "black":
        return (
            f"[{src}]scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black[{out}]"
        )
    if fit == "crop":
        return (
            f"[{src}]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}[{out}]"
        )
    # blur: unscharfer, abgedunkelter Hintergrund aus dem Bild selbst
    return (
        f"[{src}]split=2[{out}_b][{out}_f];"
        f"[{out}_b]scale='max(2,trunc(iw/16)*2)':'max(2,trunc(ih/16)*2)',"
        f"boxblur=luma_radius='min(10,min(w,h)/4)':luma_power=2:"
        f"chroma_radius='min(10,min(cw,ch)/4)':chroma_power=2,"
        f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"eq=brightness=-0.15[{out}_bg];"
        f"[{out}_f]scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2"
        f"[{out}_fg];"
        f"[{out}_bg][{out}_fg]overlay=(W-w)/2:(H-h)/2[{out}]"
    )


def _item_settings(item: Item) -> dict:
    d = asdict(item)
    # Übergänge, Dauer (steckt in frames) und Kapitel gehören nicht in den Schlüssel
    for k in ("transition", "transition_duration", "duration", "chapter", "taken", "path",
              "damage", "damage_readable"):
        d.pop(k, None)
    # Neue Felder nur im Schlüssel, wenn gesetzt: Segmente aus früheren Versionen bleiben gültig
    if d.get("crop") is None:
        d.pop("crop", None)
    return d


def _picture_key_data(project: Project, item: Item, cache_dir: Path) -> dict:
    """Was das Bild eines Elements bestimmt: Einstellungen, Quelle, Schrift."""
    data: dict = {"item": _item_settings(item)}
    if item.path:
        src = project.resolve(item.path)
        data["source"] = file_fingerprint(src)
        if item.kind == "video":
            data["normalized"] = cached_normalized(src, cache_dir) is not None
    if item.text is not None:
        font = font_file(item.text.font)
        data["font"] = [font.name, font.stat().st_size]
    return data


def segment_key(project: Project, entry: TimelineEntry, cfg: RenderConfig) -> str:
    return make_key({
        "type": "segment",
        "frames": entry.frames,
        "size": [cfg.width, cfg.height],
        "encoder": encoder_args(cfg.encoder, cfg.height, intermediate=True),
        **_picture_key_data(project, entry.item, cfg.cache_dir),
    })


def still_key(project: Project, item: Item, w: int, h: int, cache_dir: Path) -> str:
    return make_key({"type": "still", "size": [w, h],
                     **_picture_key_data(project, item, cache_dir)})


def prepare_photo(src: Path, w: int, h: int, out: Path, *, fast: bool = False,
                  tolerant: bool = False, crop: list[float] | None = None) -> Path:
    """Foto (auch HEIC) laden, EXIF-Drehung und Bildausschnitt anwenden, auf nötige Größe
    verkleinern, als PNG.
    fast: JPEG gleich verkleinert dekodieren (Vorschau; deutlich schneller bei 12 MP).
    tolerant: beschädigtes Bild mit seinem lesbaren Teil verwenden.
    crop: fixer Ausschnitt [links, oben, Breite, Höhe] in Anteilen des gedrehten Bildes."""
    img = open_photo(src, draft=draft_size(w, h, crop) if fast else None, tolerant=tolerant)
    img = apply_crop(img, crop)
    factor = max(w / img.width, h / img.height)  # Größe, die den Rahmen füllend abdeckt
    if factor < 1:
        size = (max(2, round(img.width * factor)), max(2, round(img.height * factor)))
        img = img.resize(size, Image.Resampling.LANCZOS)
    img.save(out, "PNG", compress_level=1)
    return out


@dataclass
class Picture:
    """Bildteil eines Segments: Eingänge und Filtergraph bis zum fertigen Einzelbild
    (Formatmodus, bt709, Text). Segment und Standbild-Vorschau verwenden denselben."""

    args: list[str]
    graph: list[str]
    last: str  # Name des letzten Ausgangs im Graphen
    work: list[Path] = field(default_factory=list)  # Arbeitsdateien, danach löschen
    has_audio: bool = False


def build_picture(project: Project, item: Item, w: int, h: int, cache_dir: Path,
                  work_base: Path, *, fast: bool = False) -> Picture:
    """work_base: Pfad ohne Endung für Arbeitsdateien (bekommen eindeutige Namen).
    fast: Fotos beschleunigt verkleinert laden (nur für Vorschauen)."""
    work: list[Path] = []
    args: list[str] = []
    graph: list[str] = []
    work_base.parent.mkdir(parents=True, exist_ok=True)

    text_png: Path | None = None
    if item.text is not None and item.text.text.strip():
        text_png = save_text_png(item.text, w, h,
                                 tmp_path(work_base.with_name(f"{work_base.name}_text.png")))
        work.append(text_png)

    if item.kind == "video":
        assert item.path is not None
        # Umgewandelte Fassung (30 fps CFR, SDR); das Original nur in der Vorschau
        orig = project.resolve(item.path)
        src = cached_normalized(orig, cache_dir) or orig
        info = video_info(src)
        if item.start > 0:
            args += ["-ss", f"{item.start:.3f}"]
        args += ["-i", str(src)]
        graph.append(f"[0:v]fps={FPS}:start_time=0[src]")
        has_audio = info.has_audio and item.audio
        fit = item.fit
        assume_bt709 = info.color_tags_missing
    else:
        pre = tmp_path(work_base.with_name(f"{work_base.name}_src.png"))
        if item.kind == "title" and not item.path:
            Image.new("RGB", (w, h), item.background).save(pre, "PNG")
            fit = "black"
        else:
            assert item.path is not None
            prepare_photo(project.resolve(item.path), w, h, pre, fast=fast,
                          tolerant=item.tolerant_load,
                          crop=item.crop if item.kind == "photo" else None)
            fit = item.fit
        work.append(pre)
        args += ["-framerate", str(FPS), "-i", str(pre)]
        graph.append("[0:v]null[src]")
        has_audio = False
        assume_bt709 = False

    graph.append(fit_filter(fit, w, h, "src", "fit"))
    # Erst Bild und Text getrennt nach bt709 YUV, dann in YUV überblenden
    graph.append(f"[fit]{to_bt709(assume_bt709)}[base]")
    last = "base"
    if text_png is not None:
        args += ["-i", str(text_png)]
        graph.append(f"[1:v]{TEXT_TO_YUV}[text]")
        graph.append(f"[{last}][text]overlay=0:0:format=yuv420[txt]")
        last = "txt"
    return Picture(args, graph, last, work, has_audio)


def render_still(project: Project, item: Item, w: int, h: int, out: Path, cache_dir: Path,
                 *, cancel: threading.Event | None = None) -> Path:
    """Ein Standbild des Elements über dieselben Filter wie im Export (Formatmodus, Text,
    Farben), als PNG in kleiner Auflösung für die Vorschau im Eigenschaftenbereich."""
    out.parent.mkdir(parents=True, exist_ok=True)
    pic = build_picture(project, item, w, h, cache_dir, cache_dir / "work" / out.stem,
                        fast=True)
    # YUV (bt709, begrenzter Bereich) zurück nach RGB, wie ein Abspielgerät es zeigt
    pic.graph.append(f"[{pic.last}]scale=in_color_matrix=bt709:in_range=tv:out_range=pc,"
                     "format=rgb24[v]")
    tmp = tmp_path(out)
    try:
        run_ffmpeg([*pic.args, "-filter_complex", ";".join(pic.graph), "-map", "[v]",
                    "-frames:v", "1", "-update", "1", "-f", "image2", "-c:v", "png", str(tmp)],
                   cancel=cancel)
        commit_tmp(tmp, out)
    finally:
        cleanup_tmp(tmp)
        for p in pic.work:
            cleanup_tmp(p)
    return out


def render_segment(
    project: Project,
    entry: TimelineEntry,
    cfg: RenderConfig,
    *,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> tuple[Path, bool]:
    """Rendert ein Segment oder holt es aus dem Cache. Rückgabe: (Pfad, aus_cache)."""
    item = entry.item
    if item.kind == "video" and not cfg.allow_original:
        assert item.path is not None
        # Nie das Original: fehlt die Umwandlung, jetzt nachholen (oder auf sie warten)
        normalize_video(project.resolve(item.path), cfg.cache_dir, encoder=cfg.encoder,
                        cancel=cancel)
    key = segment_key(project, entry, cfg)
    final = cfg.segment_dir / f"{key}.mp4"
    if final.is_file():
        return final, True
    cfg.segment_dir.mkdir(parents=True, exist_ok=True)
    cfg.work_dir.mkdir(parents=True, exist_ok=True)

    n = entry.frames
    seconds = f"{n / FPS:.6f}"
    samples = n * SAMPLES_PER_FRAME
    pic = build_picture(project, item, cfg.width, cfg.height, cfg.cache_dir,
                        cfg.work_dir / key)
    args, graph, last, work = pic.args, pic.graph, pic.last, pic.work
    has_audio = pic.has_audio

    if item.kind == "video":
        # Zu kurze Clips mit dem letzten Bild auffüllen, dann exakt auf n Bilder schneiden
        graph.append(
            f"[{last}]tpad=stop_mode=clone:stop_duration={seconds},"
            f"trim=end_frame={n},settb=1/{FPS},setpts=N[v]"
        )
    else:
        # Standbild nur einmal berechnen und dann wiederholen
        graph.append(
            f"[{last}]loop=loop={n - 1}:size=1:start=0,settb=1/{FPS},setpts=N[v]"
        )

    if has_audio:
        graph.append(
            f"[0:a]aresample={SAMPLE_RATE}:async=1:first_pts=0,"
            f"aformat=sample_fmts=s16:channel_layouts=stereo,volume={item.volume:.3f},"
            f"apad,atrim=end_sample={samples}[a]"
        )
    else:
        graph.append(
            f"anullsrc=r={SAMPLE_RATE}:cl=stereo,aformat=sample_fmts=s16,"
            f"atrim=end_sample={samples}[a]"
        )

    tmp = tmp_path(final)
    args += [
        "-filter_complex", ";".join(graph),
        "-map", "[v]", "-map", "[a]",
        "-frames:v", str(n),
        *encoder_args(cfg.encoder, cfg.height, intermediate=True),
        "-c:a", "flac",
        str(tmp),
    ]
    try:
        run_ffmpeg(args, progress=progress, cancel=cancel)
        commit_tmp(tmp, final)
    finally:
        cleanup_tmp(tmp)
        for p in work:
            cleanup_tmp(p)
    return final, False
