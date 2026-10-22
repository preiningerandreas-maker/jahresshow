"""Import: Dateien sammeln, Metadaten lesen (EXIF, ffprobe), nach Aufnahmedatum sortieren,
Duplikate melden."""

from __future__ import annotations

import hashlib
import os
import re
import threading
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageOps

from slideshow.core.ffmpeg import Cancelled, FFmpegError, probe

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover - HEIC dann nicht verfügbar
    pass

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
MUSIC_EXTS = {".mp3", ".m4a", ".wav", ".flac"}

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
# Dolby Vision Profil 8: Basisschicht ist HDR10 (1), SDR (2) oder HLG (4); DV-Metadaten ignorieren
DOVI_BASE_TRANSFER = {1: "smpte2084", 2: "bt709", 4: "arib-std-b67"}
APPLE_CONTENT_ID = "com.apple.quicktime.content.identifier"
TAG_MAKER_NOTE = 0x927C
_UUID = re.compile(rb"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")

EXIF_IFD = 0x8769
TAG_ORIENTATION = 0x0112
TAG_DATETIME = 0x0132
TAG_DATETIME_ORIGINAL = 0x9003
TAG_DATETIME_DIGITIZED = 0x9004

# Woher das Aufnahmedatum stammt (für die Anzeige in der GUI)
DATE_SOURCES = {
    "exif": "Aufnahmedatum (EXIF)",
    "video": "Aufnahmedatum (Video)",
    "name": "Datum im Dateinamen",
    "file": "Dateidatum (kein Aufnahmedatum gefunden)",
}


# --- Videos ------------------------------------------------------------------


@dataclass(frozen=True)
class VideoInfo:
    duration: float
    width: int  # Breite und Höhe nach Drehung
    height: int
    has_audio: bool
    rotation: int = 0
    color_transfer: str = "unknown"
    color_primaries: str = "unknown"
    color_space: str = "unknown"
    color_range: str = "tv"
    creation_time: str | None = None
    dovi_profile: int | None = None  # Dolby Vision
    dovi_compat: int | None = None  # Kompatibilität der Basisschicht
    content_id: str | None = None  # Apple Live Photo: Kennung des zugehörigen Fotos

    @property
    def transfer(self) -> str:
        """Tatsächliche Übertragungskennlinie. Dolby Vision 8.x zählt als seine Basisschicht."""
        if self.color_transfer in ("unknown", "") and self.dovi_profile == 8:
            return DOVI_BASE_TRANSFER.get(self.dovi_compat or 0, self.color_transfer)
        return self.color_transfer

    @property
    def hdr(self) -> bool:
        return self.transfer in HDR_TRANSFERS

    @property
    def wide_gamut(self) -> bool:
        """SDR mit bt2020-Primärfarben (manche Handys ohne HDR): Farben umrechnen."""
        return not self.hdr and self.color_primaries == "bt2020"

    @property
    def color_tags_missing(self) -> bool:
        """WhatsApp, Bearbeitungs-Apps: ohne Farbangaben wird bt709 angenommen."""
        return self.color_space in ("unknown", "", "reserved")


def video_info(path: Path) -> VideoInfo:
    st = path.stat()
    return _video_info(str(path.resolve()), st.st_size, st.st_mtime_ns)


@lru_cache(maxsize=4096)
def _video_info(path: str, size: int, mtime_ns: int) -> VideoInfo:
    data = probe(path)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError(f"Keine Videospur in {path}")
    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or video.get("duration") or 0)
    w, h = int(video.get("width", 0)), int(video.get("height", 0))
    rotation = _rotation(video)
    if rotation in (90, 270):
        w, h = h, w
    tags = fmt.get("tags", {})
    created = tags.get("creation_time") or video.get("tags", {}).get("creation_time")
    dovi = next((sd for sd in video.get("side_data_list", [])
                 if "dv_profile" in sd), {})
    return VideoInfo(
        duration=duration,
        width=w,
        height=h,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        rotation=rotation,
        color_transfer=video.get("color_transfer", "unknown"),
        color_primaries=video.get("color_primaries", "unknown"),
        color_space=video.get("color_space", "unknown"),
        color_range=video.get("color_range", "tv"),
        creation_time=created,
        dovi_profile=dovi.get("dv_profile"),
        dovi_compat=dovi.get("dv_bl_signal_compatibility_id"),
        content_id=tags.get(APPLE_CONTENT_ID),
    )


def _rotation(stream: dict) -> int:
    for sd in stream.get("side_data_list", []):
        if "rotation" in sd:
            return abs(int(float(sd["rotation"]))) % 360
    rot = stream.get("tags", {}).get("rotate")
    return abs(int(rot)) % 360 if rot else 0


def clip_length(path: Path) -> float:
    return video_info(path).duration


# --- Fotos -------------------------------------------------------------------


def open_photo(path: Path, draft: tuple[int, int] | None = None) -> Image.Image:
    """Foto (auch HEIC) laden und EXIF-Drehung anwenden. Ergebnis ist RGB.
    draft: JPEG verkleinert dekodieren, mindestens so groß, dass (b, h) bedeckt bleibt."""
    with Image.open(path) as im:
        if draft is not None:
            w, h = draft
            side = max(w, h)  # Drehung erst danach bekannt: beide Seiten abdecken
            im.draft("RGB", (side, side))
        img = ImageOps.exif_transpose(im)
        return img.convert("RGB")


def _parse_exif_date(value: object) -> datetime | None:
    if isinstance(value, bytes):
        value = value.decode("ascii", "replace")
    if not isinstance(value, str):
        return None
    # "2026:07:14 10:30:00", manche Programme schreiben "2026-07-14 10:30:00"
    value = value.strip().rstrip("\x00")[:19].replace("-", ":").replace("T", " ")
    try:
        dt = datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
    except ValueError:  # auch "0000:00:00 00:00:00" von Kameras ohne Uhrzeit
        return None
    return dt if dt.year >= 1980 else None


@dataclass(frozen=True)
class PhotoInfo:
    width: int  # nach EXIF-Drehung
    height: int
    orientation: int
    taken: datetime | None
    content_ids: frozenset[str] = frozenset()  # Apple MakerNote: Kennungen für Live Photos


def photo_info(path: Path) -> PhotoInfo:
    with Image.open(path) as im:
        exif = im.getexif()
        w, h = im.size
    orientation = int(exif.get(TAG_ORIENTATION, 1) or 1)
    if orientation in (5, 6, 7, 8):  # um 90° gedreht
        w, h = h, w
    sub = exif.get_ifd(EXIF_IFD)
    taken = None
    for value in (sub.get(TAG_DATETIME_ORIGINAL), sub.get(TAG_DATETIME_DIGITIZED),
                  exif.get(TAG_DATETIME)):
        taken = _parse_exif_date(value)
        if taken is not None:
            break
    note = sub.get(TAG_MAKER_NOTE)
    ids: frozenset[str] = frozenset()
    if isinstance(note, bytes):
        ids = frozenset(m.decode("ascii").upper() for m in _UUID.findall(note))
    return PhotoInfo(w, h, orientation, taken, ids)


def is_motion_photo(path: Path) -> bool:
    """Samsung/Google Motion Photo: JPEG mit angehängtem Video. In V1 zählt nur das Standbild."""
    try:
        with path.open("rb") as f:
            head = f.read(256 * 1024)
            if b"MotionPhoto" in head or b"MicroVideo" in head:
                return True
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 4))
            if f.read(4) != b"SEFT":  # Samsung-Anhang am Dateiende
                return False
            f.seek(0)
            return b"MotionPhoto_Data" in f.read()
    except OSError:
        return False


# --- Datum -------------------------------------------------------------------

_NAME_DATE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})"
    r"(?:[-_ T.]?(\d{2})[-_.]?(\d{2})[-_.]?(\d{2}))?(?!\d)"
)


def date_from_name(name: str) -> datetime | None:
    """Datum aus Dateinamen wie 20260529_161607.jpg oder IMG-20260101-WA0001.jpg."""
    for m in _NAME_DATE.finditer(name):
        y, mo, d, hh, mm, ss = (int(g) if g else 0 for g in m.groups())
        try:
            return datetime(y, mo, d, hh, mm, ss)
        except ValueError:
            continue
    return None


def parse_creation_time(value: str | None) -> datetime | None:
    """creation_time aus dem Video (UTC) in lokale Zeit ohne Zeitzone."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    if dt.year < 1980:  # 1904/1970: Kamera hat kein Datum gesetzt
        return None
    return dt.astimezone().replace(tzinfo=None)


def file_date(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime)


# --- Import ------------------------------------------------------------------


@dataclass
class MediaFile:
    path: Path
    kind: str  # photo | video
    taken: datetime
    date_source: str  # exif | video | name | file
    width: int  # Anzeigegröße nach Drehung
    height: int
    size: int
    orientation: int = 1
    duration: float | None = None
    has_audio: bool = False
    hdr: bool = False
    motion_photo: bool = False
    live_photo: bool = False  # iPhone Live Photo: zugehöriges MOV wird in V1 nicht verwendet
    duplicate_of: Path | None = None
    content_ids: frozenset[str] = frozenset()
    content_id: str | None = None

    @property
    def portrait(self) -> bool:
        return self.height > self.width


@dataclass
class ImportResult:
    media: list[MediaFile]
    duplicates: list[tuple[Path, Path]]  # (Duplikat, Original)
    skipped: list[tuple[Path, str]]  # (Datei, Grund)
    live_videos: list[tuple[Path, Path]] = field(default_factory=list)  # (MOV, Foto)

    def messages(self) -> list[str]:
        """Meldungen in einfacher Sprache für die Oberfläche."""
        out = [f"„{d.name}“ ist identisch mit „{o.name}“ (doppelt vorhanden)."
               for d, o in self.duplicates]
        out += [f"„{p.name}“ wurde übersprungen: {reason}" for p, reason in self.skipped]
        return out


def media_kind(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in PHOTO_EXTS:
        return "photo"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def collect_files(paths: Iterable[Path | str]) -> list[Path]:
    """Fotos und Videos aus Dateien und Ordnern (mit Unterordnern), jede Datei nur einmal."""
    seen: set[str] = set()
    out: list[Path] = []

    def add(p: Path) -> None:
        key = os.path.normcase(str(p.resolve()))
        if key not in seen and media_kind(p) is not None:
            seen.add(key)
            out.append(p)

    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for name in sorted(files):
                    if not name.startswith("."):
                        add(Path(root) / name)
        elif p.is_file():
            add(p)
    return out


def read_media(path: Path) -> MediaFile:
    kind = media_kind(path)
    if kind is None:
        raise ValueError("Dateityp wird nicht unterstützt")
    size = path.stat().st_size
    if kind == "photo":
        info = photo_info(path)
        taken, source = info.taken, "exif"
        mf = MediaFile(path, kind, datetime.min, "", info.width, info.height, size,
                       orientation=info.orientation, content_ids=info.content_ids,
                       motion_photo=path.suffix.lower() in (".jpg", ".jpeg")
                       and is_motion_photo(path))
    else:
        vi = video_info(path)
        taken, source = parse_creation_time(vi.creation_time), "video"
        mf = MediaFile(path, kind, datetime.min, "", vi.width, vi.height, size,
                       duration=vi.duration, has_audio=vi.has_audio, hdr=vi.hdr,
                       content_id=vi.content_id)
    if taken is None:
        taken, source = date_from_name(path.stem), "name"
    if taken is None:
        taken, source = file_date(path), "file"
    mf.taken, mf.date_source = taken, source
    return mf


def file_hash(path: Path, cancel: threading.Event | None = None) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            h.update(chunk)
    return h.hexdigest()


def find_duplicates(
    media: list[MediaFile], cancel: threading.Event | None = None
) -> list[tuple[Path, Path]]:
    """Inhaltsgleiche Dateien finden. Nur Dateien gleicher Größe werden gehasht.
    Das erste Vorkommen in der Liste gilt als Original."""
    by_size: dict[int, list[MediaFile]] = defaultdict(list)
    for m in media:
        by_size[m.size].append(m)
    dups: list[tuple[Path, Path]] = []
    for group in by_size.values():
        if len(group) < 2:
            continue
        first: dict[str, MediaFile] = {}
        for m in group:
            digest = file_hash(m.path, cancel)
            if digest in first:
                m.duplicate_of = first[digest].path
            else:
                first[digest] = m
    for m in media:
        if m.duplicate_of is not None:
            dups.append((m.path, m.duplicate_of))
    return dups


def find_existing_duplicates(
    media: list[MediaFile], existing: Iterable[Path], cancel: threading.Event | None = None
) -> tuple[list[tuple[Path, Path]], list[Path]]:
    """Neu importierte Dateien mit den schon im Projekt vorhandenen vergleichen.

    Rückgabe: (inhaltsgleiche Paare (neu, vorhanden), Dateien, die selbst schon im Projekt
    sind). Gehasht werden nur Dateien gleicher Größe."""
    by_size: dict[int, list[Path]] = defaultdict(list)
    already: list[Path] = []
    known = {os.path.normcase(str(p.resolve())) for p in existing}
    for p in existing:
        try:
            by_size[p.stat().st_size].append(p)
        except OSError:
            continue
    dups: list[tuple[Path, Path]] = []
    hashes: dict[Path, str] = {}

    def digest(p: Path) -> str:
        if p not in hashes:
            hashes[p] = file_hash(p, cancel)
        return hashes[p]

    for m in media:
        if os.path.normcase(str(m.path.resolve())) in known:
            already.append(m.path)
            continue
        for old in by_size.get(m.size, []):
            try:
                if digest(m.path) == digest(old):
                    m.duplicate_of = old
                    dups.append((m.path, old))
                    break
            except OSError:
                continue
    return dups, already


def sort_key(m: MediaFile) -> tuple[datetime, str]:
    return (m.taken, m.path.name.casefold())


def import_media(
    paths: Iterable[Path | str],
    *,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> ImportResult:
    """Liest alle Fotos/Videos ein, sortiert nach Aufnahmedatum und meldet Duplikate.
    Duplikate bleiben in der Liste (markiert mit duplicate_of); entfernen entscheidet der Nutzer."""
    files = collect_files(paths)
    media: list[MediaFile] = []
    skipped: list[tuple[Path, str]] = []
    for i, p in enumerate(files, start=1):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        try:
            media.append(read_media(p))
        except (OSError, ValueError, FFmpegError, Image.DecompressionBombError) as e:
            skipped.append((p, _reason(e)))
        except Exception as e:  # noqa: BLE001 - beschädigte Datei darf den Import nicht stoppen
            skipped.append((p, f"Datei konnte nicht gelesen werden ({e})"))
        if progress is not None:
            progress(i, len(files))
    media, live = split_live_photos(media)
    media.sort(key=sort_key)
    return ImportResult(media, find_duplicates(media, cancel), skipped, live)


def split_live_photos(media: list[MediaFile]) -> tuple[list[MediaFile], list[tuple[Path, Path]]]:
    """iPhone Live Photos: Das MOV trägt dieselbe Kennung wie die MakerNote des Fotos.
    Zuordnung nur über diese Metadaten, nicht über Dateinamen. V1 verwendet nur das Foto."""
    photos = {cid: m for m in media if m.kind == "photo" for cid in m.content_ids}
    keep: list[MediaFile] = []
    live: list[tuple[Path, Path]] = []
    for m in media:
        photo = photos.get(m.content_id.upper()) if m.kind == "video" and m.content_id else None
        if photo is None:
            keep.append(m)
        else:
            photo.live_photo = True
            live.append((m.path, photo.path))
    return keep, live


def _reason(e: Exception) -> str:
    if isinstance(e, FFmpegError):
        return "Video konnte nicht gelesen werden (beschädigt oder unbekanntes Format)"
    if isinstance(e, OSError) and not isinstance(e, FileNotFoundError):
        return f"Datei konnte nicht gelesen werden ({e.strerror or e})"
    return str(e) or type(e).__name__
