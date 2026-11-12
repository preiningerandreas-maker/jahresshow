"""Projektmodell: Dataclasses und JSON mit versioniertem Schema ("schema_version")."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
FPS = 30

KINDS = ("title", "photo", "video")
FIT_MODES = ("blur", "black", "crop")
# fade = Überblenden (Standard), fadeblack = über Schwarz, wipeleft = Wischen,
# slideleft = Schieben, none = harter Schnitt. Neu in 2.0 (Namen der xfade-Übergänge von
# FFmpeg): zoomin = Zoom, circleopen = Kreis öffnen, radial = Drehwischer, pixelize = Pixel.
# Bewusst nicht dabei: dissolve (Auflösen), das Bild wird bei jedem Export etwas anders.
TRANSITIONS = ("fade", "fadeblack", "wipeleft", "slideleft", "none",
               "zoomin", "circleopen", "radial", "pixelize")
POSITIONS = ("top", "center", "bottom")
DEFAULT_TEXT_SIZE = 56
DEFAULT_TEXT_COLOR = "#FFFFFF"
MAX_TEXT_FADE = 2.0
ROTATIONS = (0, 90, 180, 270)
RESOLUTIONS = {"1080p": (1920, 1080), "2160p": (3840, 2160)}
PREVIEW_SIZE = (854, 480)
# Beschädigte Bilder: „trotzdem verwenden“ nur, wenn mindestens dieser Anteil lesbar ist
DAMAGE_USABLE_MIN = 0.8
# Ab diesem Anteil gilt ein Bild als nur leicht beschädigt und wird automatisch verwendet
DAMAGE_MINOR_MIN = 0.99


def readable_at_least(readable: float | None, minimum: float) -> bool:
    """Lesbarer Anteil mindestens minimum; unbekannt (None) zählt als nicht lesbar."""
    return readable is not None and readable >= minimum


def readable_text(readable: float | None) -> str:
    """„lesbar 95 %“ oder „lesbarer Anteil unbekannt“."""
    if readable is None:
        return "lesbarer Anteil unbekannt"
    return f"lesbar {round(readable * 100)} %"


def validate_crop(crop: list[float]) -> None:
    """Ausschnitt [links, oben, Breite, Höhe] in Anteilen des Bildes."""
    if len(crop) != 4:
        raise ValueError("Bildausschnitt braucht vier Werte (links, oben, Breite, Höhe)")
    x, y, w, h = crop
    eps = 1e-6
    if x < -eps or y < -eps or w <= 0 or h <= 0 or x + w > 1 + eps or y + h > 1 + eps:
        raise ValueError(f"Bildausschnitt liegt nicht im Bild: {crop!r}")


def to_frames(seconds: float) -> int:
    return round(seconds * FPS)


@dataclass
class TextOverlay:
    text: str
    position: str = "bottom"
    font: str = "Noto Sans"  # mitgelieferte Schrift oder Pfad zu einer .ttf/.otf
    size: int = DEFAULT_TEXT_SIZE  # Pixel bezogen auf 1080p
    color: str = DEFAULT_TEXT_COLOR
    bold: bool = False
    bar: bool = True  # halbtransparenter Balken hinter dem Text
    bar_color: str = "#000000"
    bar_opacity: float = 0.5
    # Sekunden für sanftes Ein- und Ausblenden des Textes; 0 = Text steht die ganze Zeit
    fade: float = 0.5
    # Freie Position: Mitte des Textblocks als Anteil des Bildes (0..1). Beide None = die
    # Voreinstellung in "position" (oben/Mitte/unten, mit Balken über die ganze Breite).
    x: float | None = None
    y: float | None = None
    # Eigene Größe/Farbe dieses Textes. Sonst gelten Größe und Farbe aus den Projekteinstellungen
    # (Settings.text_size/text_color) und size/color hier werden nicht beachtet.
    own_size: bool = False
    own_color: bool = False

    @property
    def free(self) -> bool:
        return self.x is not None and self.y is not None

    def validate(self) -> None:
        if (self.x is None) != (self.y is None):
            raise ValueError("Freie Textposition braucht x und y")
        if self.free:
            assert self.x is not None and self.y is not None
            if not (0.0 <= self.x <= 1.0 and 0.0 <= self.y <= 1.0):
                raise ValueError("Freie Textposition muss im Bild liegen (0 bis 1)")
        if not 0.0 <= self.fade <= MAX_TEXT_FADE:
            raise ValueError(f"Ein-/Ausblenden muss zwischen 0 und {MAX_TEXT_FADE} s liegen")
        if self.position not in POSITIONS:
            raise ValueError(f"Unbekannte Textposition: {self.position!r}")
        if not 0.0 <= self.bar_opacity <= 1.0:
            raise ValueError("Deckkraft des Balkens muss zwischen 0 und 1 liegen")
        if self.size <= 0:
            raise ValueError("Schriftgröße muss größer als 0 sein")


def _load_text(raw: dict[str, Any]) -> TextOverlay:
    """Text aus der Projektdatei. Alte Projekte kennen den Projektstandard nicht: Eine
    Größe/Farbe, die vom früheren Standard abweicht, war eine bewusste Wahl und bleibt eigen."""
    raw = dict(_known(TextOverlay, raw))
    if "own_size" not in raw:
        raw["own_size"] = raw.get("size", DEFAULT_TEXT_SIZE) != DEFAULT_TEXT_SIZE
    if "own_color" not in raw:
        raw["own_color"] = (str(raw.get("color", DEFAULT_TEXT_COLOR)).upper()
                            != DEFAULT_TEXT_COLOR)
    return TextOverlay(**raw)


@dataclass
class Item:
    kind: str  # title | photo | video
    path: str | None = None  # Titelkarte: optionales Hintergrundfoto
    duration: float | None = None  # None: Foto/Titel = Projektstandard, Video = Cliplänge
    start: float = 0.0  # Video: Startpunkt im Clip in Sekunden
    fit: str = "blur"
    # Foto: fixer Bildausschnitt [links, oben, Breite, Höhe], jeweils 0..1 vom (gedrehten)
    # Bild; None = ganzes Bild
    crop: list[float] | None = None
    # Foto: zusätzliche Drehung im Uhrzeigersinn in Grad (0, 90, 180, 270), nach der
    # EXIF-Drehung und vor dem Ausschnitt
    rotation: int = 0
    text: TextOverlay | None = None
    transition: str | None = None  # Übergang vom vorherigen Element; None = Projektstandard
    transition_duration: float | None = None
    background: str = "#1E2A3A"  # Titelkarte ohne Foto
    audio: bool = True  # Originalton eines Videos verwenden
    volume: float = 1.0
    chapter: str | None = None  # Kapitel "JJJJ-MM"; Titel mit Kapitel = Zwischentitel
    taken: str | None = None  # Aufnahmedatum ISO 8601, für die Kapitel
    damage: str | None = None  # Bilddaten beschädigt (Grund); None = in Ordnung
    # lesbarer Anteil eines beschädigten Bildes; None = unbekannt, zählt als nicht lesbar,
    # damit ein Schaden nie stillschweigend als „leicht beschädigt“ verwendet wird
    damage_readable: float | None = None
    use_damaged: bool = False  # beschädigtes Bild trotzdem verwenden (lesbarer Teil)

    @property
    def damage_usable(self) -> bool:
        return self.damage is not None and readable_at_least(self.damage_readable,
                                                             DAMAGE_USABLE_MIN)

    @property
    def damage_minor(self) -> bool:
        """Leicht beschädigt, aber vollständig lesbar: wird ohne Nachfrage verwendet."""
        return self.damage is not None and readable_at_least(self.damage_readable,
                                                             DAMAGE_MINOR_MIN)

    @property
    def skip_damaged(self) -> bool:
        """Beim Export überspringen: beschädigt, nicht leicht beschädigt und nicht
        „trotzdem verwenden“."""
        return (self.damage is not None and not self.damage_minor
                and not (self.use_damaged and self.damage_usable))

    @property
    def tolerant_load(self) -> bool:
        """Beschädigtes Bild mit seinem lesbaren Teil laden."""
        return self.damage is not None and not self.skip_damaged

    def validate(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"Unbekannter Elementtyp: {self.kind!r}")
        if self.kind in ("photo", "video") and not self.path:
            raise ValueError(f"Element vom Typ {self.kind!r} braucht eine Datei")
        if self.fit not in FIT_MODES:
            raise ValueError(f"Unbekannter Formatmodus: {self.fit!r}")
        if self.transition is not None and self.transition not in TRANSITIONS:
            raise ValueError(f"Unbekannter Übergang: {self.transition!r}")
        if self.duration is not None and self.duration <= 0:
            raise ValueError("Anzeigedauer muss größer als 0 sein")
        if self.rotation not in ROTATIONS:
            raise ValueError(f"Drehung muss 0, 90, 180 oder 270 Grad sein: {self.rotation!r}")
        if self.crop is not None:
            validate_crop(self.crop)
        if self.text is not None:
            self.text.validate()


@dataclass
class Music:
    tracks: list[str] = field(default_factory=list)
    volume: float = 1.0
    duck_volume: float = 0.25  # Musiklautstärke während Clips mit Originalton
    fade_out: float = 3.0
    # Ausschnitt pro Musikstück: Pfad wie in "tracks" -> [Start, Ende] in Sekunden.
    # Ende None = bis zum Ende der Datei. Stücke ohne Eintrag laufen ganz.
    trims: dict[str, list[float | None]] = field(default_factory=dict)

    def trim_of(self, track: str) -> tuple[float, float | None]:
        start, end = self.trims.get(track) or (0.0, None)
        return float(start or 0.0), (None if end is None else float(end))

    def validate(self) -> None:
        for track, trim in self.trims.items():
            if len(trim) != 2:
                raise ValueError(f"Musikausschnitt braucht Start und Ende: {track!r}")
            start, end = self.trim_of(track)
            if start < 0:
                raise ValueError("Musikausschnitt: Start darf nicht negativ sein")
            if end is not None and end <= start:
                raise ValueError("Musikausschnitt: Ende muss nach dem Start liegen")


@dataclass
class Settings:
    photo_duration: float = 4.0
    transition: str = "fade"
    transition_duration: float = 1.0
    resolution: str = "1080p"
    chapters: bool = True  # Zwischentitel pro Monat (nur für die Oberfläche)
    # Standard für alle Texte, die keine eigene Größe/Farbe haben (TextOverlay.own_size/own_color)
    text_size: int = DEFAULT_TEXT_SIZE
    text_color: str = DEFAULT_TEXT_COLOR
    # Monate ("2022-09"), deren Zwischentitel der Nutzer gelöscht hat: kein neuer Titel
    skipped_chapters: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if self.transition not in TRANSITIONS:
            raise ValueError(f"Unbekannter Übergang: {self.transition!r}")
        if self.resolution not in RESOLUTIONS:
            raise ValueError(f"Unbekannte Auflösung: {self.resolution!r}")
        if self.text_size <= 0:
            raise ValueError("Standard-Schriftgröße muss größer als 0 sein")


@dataclass
class TimelineEntry:
    """Ein Element mit aufgelösten Zeiten, alles in Frames bei 30 fps."""

    index: int
    item: Item
    frames: int  # Länge des Segments
    transition: str  # Übergang vom vorherigen Element
    transition_frames: int  # Überlappung mit dem vorherigen Element
    start_frame: int  # Startzeitpunkt in der fertigen Show


@dataclass
class Project:
    name: str = "Jahresrückblick"
    items: list[Item] = field(default_factory=list)
    music: Music = field(default_factory=Music)
    settings: Settings = field(default_factory=Settings)
    schema_version: int = SCHEMA_VERSION
    base_dir: Path = field(default_factory=Path.cwd, repr=False, compare=False)

    def styled(self, item: Item) -> Item:
        """Das Element mit der tatsächlich geltenden Textgröße und -farbe (Projektstandard,
        wo der Text keine eigene hat). Das Original bleibt unverändert."""
        t = item.text
        if t is None:
            return item
        size = t.size if t.own_size else self.settings.text_size
        color = t.color if t.own_color else self.settings.text_color
        if (size, color) == (t.size, t.color):
            return item
        return replace(item, text=replace(t, size=size, color=color))

    def validate(self) -> None:
        self.settings.validate()
        self.music.validate()
        for i, item in enumerate(self.items, start=1):
            try:
                item.validate()
            except ValueError as e:
                raise ValueError(f"Element {i}: {e}") from e

    def resolve(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else (self.base_dir / path)

    def timeline(self, clip_length: Callable[[Path], float]) -> list[TimelineEntry]:
        """Berechnet die Zeitleiste. clip_length liefert die Länge eines Videos in Sekunden."""
        frames: list[int] = []
        for item in self.items:
            if item.duration is not None:
                d = item.duration
            elif item.kind == "video":
                assert item.path is not None
                d = max(0.0, clip_length(self.resolve(item.path)) - item.start)
            else:
                d = self.settings.photo_duration
            frames.append(max(1, to_frames(d)))

        entries: list[TimelineEntry] = []
        pos = 0
        for i, (item, n) in enumerate(zip(self.items, frames, strict=True)):
            kind = item.transition or self.settings.transition
            t = 0
            if i > 0 and kind != "none":
                tdur = item.transition_duration
                if tdur is None:
                    tdur = self.settings.transition_duration
                # Ein Element muss Ein- und Ausblendung aufnehmen können
                t = min(to_frames(tdur), (min(frames[i - 1], n) - 1) // 2)
                t = max(0, t)
            if t == 0:
                kind = "none"
            pos -= t
            entries.append(TimelineEntry(i, item, n, kind if i > 0 else "none", t, pos))
            pos += n
        return entries

    def total_frames(self, clip_length: Callable[[Path], float]) -> int:
        tl = self.timeline(clip_length)
        return tl[-1].start_frame + tl[-1].frames if tl else 0

    # --- JSON -----------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        def rel(p: str | None) -> str | None:
            if p is None:
                return None
            path = Path(p)
            if path.is_absolute():
                try:
                    return Path(os.path.relpath(path, self.base_dir)).as_posix()
                except ValueError:  # anderes Laufwerk
                    return str(path)
            return path.as_posix()

        items = []
        for item in self.items:
            d = asdict(item)
            d["path"] = rel(item.path)
            items.append(d)
        music = asdict(self.music)
        music["tracks"] = [rel(t) for t in self.music.tracks]
        music["trims"] = {rel(t): v for t, v in self.music.trims.items()}
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "settings": asdict(self.settings),
            "music": music,
            "items": items,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], base_dir: Path) -> Project:
        version = data.get("schema_version")
        if version is None:
            raise ValueError("Keine gültige Projektdatei (schema_version fehlt)")
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"Projektdatei stammt aus einer neueren Programmversion (Schema {version})"
            )
        items = []
        for raw in data.get("items", []):
            raw = _known(Item, raw)
            if raw.get("text") is not None:
                raw["text"] = _load_text(raw["text"])
            items.append(Item(**raw))
        project = cls(
            name=data.get("name", "Jahresrückblick"),
            items=items,
            music=Music(**_known(Music, data.get("music", {}))),
            settings=Settings(**_known(Settings, data.get("settings", {}))),
            schema_version=SCHEMA_VERSION,
            base_dir=base_dir,
        )
        project.validate()
        return project

    def save(self, path: str | os.PathLike[str]) -> None:
        path = Path(path)
        self.base_dir = path.resolve().parent
        # Eindeutiger Zwischenname, dann umbenennen: nie eine halb geschriebene Projektdatei
        tmp = path.with_name(f"{path.name}.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), "utf-8")
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> Project:
        path = Path(path)
        data = json.loads(path.read_text("utf-8"))
        return cls.from_dict(data, path.resolve().parent)


def _known(cls: type, raw: dict[str, Any]) -> dict[str, Any]:
    """Nur bekannte Felder übernehmen, damit ältere Versionen neuere Dateien lesen können."""
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in raw.items() if k in names}
