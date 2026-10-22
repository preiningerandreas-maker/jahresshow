"""Ein Segment pro Element: fertiges 1920×1080-Bild inkl. Text, 30 fps, Ton als FLAC (Stille
bei Fotos). Übergänge entstehen erst beim Zusammensetzen der Blöcke."""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image

from slideshow.core.ffmpeg import ProgressCallback, run_ffmpeg
from slideshow.core.importer import open_photo, video_info
from slideshow.core.normalize import cached_normalized, to_bt709_scale
from slideshow.core.project import FPS, Item, Project, TimelineEntry
from slideshow.render.cache import cleanup_tmp, file_fingerprint, make_key, tmp_path
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
    for k in ("transition", "transition_duration", "duration", "chapter", "taken", "path"):
        d.pop(k, None)
    return d


def segment_key(project: Project, entry: TimelineEntry, cfg: RenderConfig) -> str:
    item = entry.item
    data: dict = {
        "type": "segment",
        "item": _item_settings(item),
        "frames": entry.frames,
        "size": [cfg.width, cfg.height],
        "encoder": encoder_args(cfg.encoder, cfg.height, intermediate=True),
    }
    if item.path:
        src = project.resolve(item.path)
        data["source"] = file_fingerprint(src)
        if item.kind == "video":
            data["normalized"] = cached_normalized(src, cfg.cache_dir) is not None
    if item.text is not None:
        font = font_file(item.text.font)
        data["font"] = [font.name, font.stat().st_size]
    return make_key(data)


def prepare_photo(src: Path, w: int, h: int, out: Path) -> Path:
    """Foto (auch HEIC) laden, EXIF-Drehung anwenden, auf nötige Größe verkleinern, als PNG."""
    img = open_photo(src)
    factor = max(w / img.width, h / img.height)  # Größe, die den Rahmen füllend abdeckt
    if factor < 1:
        size = (max(2, round(img.width * factor)), max(2, round(img.height * factor)))
        img = img.resize(size, Image.Resampling.LANCZOS)
    img.save(out, "PNG", compress_level=1)
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
    key = segment_key(project, entry, cfg)
    final = cfg.segment_dir / f"{key}.mp4"
    if final.is_file():
        return final, True
    cfg.segment_dir.mkdir(parents=True, exist_ok=True)
    cfg.work_dir.mkdir(parents=True, exist_ok=True)

    item = entry.item
    n = entry.frames
    seconds = f"{n / FPS:.6f}"
    samples = n * SAMPLES_PER_FRAME
    w, h = cfg.width, cfg.height
    work: list[Path] = []
    args: list[str] = []
    graph: list[str] = []

    text_png: Path | None = None
    if item.text is not None and item.text.text.strip():
        text_png = save_text_png(item.text, w, h, cfg.work_dir / f"{key}_text.png")
        work.append(text_png)

    if item.kind == "video":
        assert item.path is not None
        # Umgewandelte Fassung (30 fps CFR, SDR) verwenden, sobald sie im Cache liegt
        orig = project.resolve(item.path)
        src = cached_normalized(orig, cfg.cache_dir) or orig
        info = video_info(src)
        if item.start > 0:
            args += ["-ss", f"{item.start:.3f}"]
        args += ["-i", str(src)]
        graph.append(f"[0:v]fps={FPS}:start_time=0[src]")
        has_audio = info.has_audio and item.audio
    else:
        pre = cfg.work_dir / f"{key}_src.png"
        if item.kind == "title" and not item.path:
            Image.new("RGB", (w, h), item.background).save(pre, "PNG")
            fit = "black"
        else:
            assert item.path is not None
            prepare_photo(project.resolve(item.path), w, h, pre)
            fit = item.fit
        work.append(pre)
        args += ["-framerate", str(FPS), "-i", str(pre)]
        graph.append("[0:v]null[src]")
        has_audio = False

    fit_mode = item.fit if item.kind == "video" else fit
    graph.append(fit_filter(fit_mode, w, h, "src", "fit"))
    last = "fit"
    if text_png is not None:
        args += ["-i", str(text_png)]
        graph.append(f"[{last}][1:v]overlay=0:0[txt]")
        last = "txt"

    if item.kind == "video":
        # Zu kurze Clips mit dem letzten Bild auffüllen, dann exakt auf n Bilder schneiden
        graph.append(
            f"[{last}]{to_bt709(info.color_tags_missing)},tpad=stop_mode=clone:stop_duration={seconds},"
            f"trim=end_frame={n},settb=1/{FPS},setpts=N[v]"
        )
    else:
        # Standbild nur einmal berechnen und dann wiederholen
        graph.append(
            f"[{last}]{to_bt709()},loop=loop={n - 1}:size=1:start=0,settb=1/{FPS},setpts=N[v]"
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
        *encoder_args(cfg.encoder, h, intermediate=True),
        "-c:a", "flac",
        str(tmp),
    ]
    try:
        run_ffmpeg(args, progress=progress, cancel=cancel)
        tmp.replace(final)
    finally:
        cleanup_tmp(tmp)
        for p in work:
            cleanup_tmp(p)
    return final, False
