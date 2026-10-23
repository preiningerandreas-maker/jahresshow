from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from mediacheck import BLUE, RED, make_jpeg, remux_with_date

from slideshow.core.importer import (
    _parse_exif_date,
    collect_files,
    date_from_name,
    import_media,
    open_photo,
    parse_creation_time,
    photo_info,
)
from slideshow.core.project import Item, Project
from slideshow.render.segment import prepare_photo


def _set_mtime(path: Path, dt: datetime) -> None:
    ts = dt.timestamp()
    os.utime(path, (ts, ts))


def _close(a: tuple[int, ...], b: tuple[int, ...], tol: int = 40) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


# --- (1) Sortierung nach EXIF-Datum, Fallback auf Dateidatum ---------------------


def test_sorted_by_exif_date_not_by_name_or_mtime(umlaut_dir: Path) -> None:
    a = make_jpeg(umlaut_dir / "A Sommer Wörthersee.jpg", date="2026:07:14 10:30:00")
    b = make_jpeg(umlaut_dir / "B Frühling.jpg", date="2026:04:01 08:00:00")
    c = make_jpeg(umlaut_dir / "C Neujahr.jpg", date="2026:01:01 00:15:00")
    # Dateidaten absichtlich in umgekehrter Reihenfolge
    _set_mtime(c, datetime(2026, 12, 1))
    _set_mtime(b, datetime(2026, 11, 1))
    _set_mtime(a, datetime(2026, 10, 1))

    res = import_media([umlaut_dir])
    assert [m.path.name for m in res.media] == [c.name, b.name, a.name]
    assert [m.date_source for m in res.media] == ["exif"] * 3
    assert res.media[0].taken == datetime(2026, 1, 1, 0, 15)
    assert res.skipped == [] and res.duplicates == []


def test_fallback_to_file_date(umlaut_dir: Path) -> None:
    exif = make_jpeg(umlaut_dir / "mit Datum.jpg", date="2026:03:10 12:00:00")
    plain = make_jpeg(umlaut_dir / "ohne Datum Ä.jpg")
    png = umlaut_dir / "Bildschirmfoto ö.png"
    make_jpeg(png, fmt="PNG")
    _set_mtime(plain, datetime(2026, 2, 20, 9, 0))
    _set_mtime(png, datetime(2026, 5, 1, 18, 30))
    _set_mtime(exif, datetime(2026, 1, 1))  # wird ignoriert, EXIF gewinnt

    res = import_media([umlaut_dir])
    assert [(m.path.name, m.date_source) for m in res.media] == [
        (plain.name, "file"), (exif.name, "exif"), (png.name, "file")]
    assert res.media[0].taken == datetime(2026, 2, 20, 9, 0)
    assert res.media[1].taken == datetime(2026, 3, 10, 12, 0)


def test_date_from_filename_before_file_date(umlaut_dir: Path) -> None:
    wa = make_jpeg(umlaut_dir / "IMG-20260415-WA0001.jpg")
    sam = make_jpeg(umlaut_dir / "20260302_161607.jpg")
    _set_mtime(wa, datetime(2026, 1, 1))
    _set_mtime(sam, datetime(2026, 1, 1))
    res = import_media([umlaut_dir])
    assert [(m.path.name, m.date_source, m.taken) for m in res.media] == [
        (sam.name, "name", datetime(2026, 3, 2, 16, 16, 7)),
        (wa.name, "name", datetime(2026, 4, 15)),
    ]


def test_video_date_from_creation_time(synthetic: Path, umlaut_dir: Path) -> None:
    src = synthetic / "Video mit Ton.mp4"
    dated = remux_with_date(src, umlaut_dir / "Schifahren Größe.mp4", "2026-02-10T08:00:00Z")
    undated = remux_with_date(src, umlaut_dir / "ohne Datum.mp4", None)
    _set_mtime(undated, datetime(2026, 6, 1, 12, 0))
    photo = make_jpeg(umlaut_dir / "Foto.jpg", date="2026:03:01 10:00:00")

    res = import_media([umlaut_dir])
    expected = datetime(2026, 2, 10, 8, 0, tzinfo=UTC).astimezone().replace(tzinfo=None)
    assert [(m.path, m.date_source) for m in res.media] == [
        (dated, "video"), (photo, "exif"), (undated, "file")]
    assert res.media[0].taken == expected
    assert res.media[0].duration == pytest.approx(6, abs=0.1)
    assert res.media[0].has_audio


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026:07:14 10:30:00", datetime(2026, 7, 14, 10, 30)),
        ("2026-07-14 10:30:00", datetime(2026, 7, 14, 10, 30)),
        (b"2026:07:14 10:30:00\x00", datetime(2026, 7, 14, 10, 30)),
        ("0000:00:00 00:00:00", None),
        ("    :  :     :  :  ", None),
        (None, None),
    ],
)
def test_parse_exif_date(value: object, expected: datetime | None) -> None:
    assert _parse_exif_date(value) == expected


def test_parse_dates_edge_cases() -> None:
    assert parse_creation_time("1970-01-01T00:00:00.000000Z") is None
    assert parse_creation_time("") is None
    assert parse_creation_time("kein Datum") is None
    assert date_from_name("Urlaub 2026") is None
    assert date_from_name("IMG_20261399_000000") is None  # ungültiger Monat
    assert date_from_name("Screenshot_2026-08-09-17-45-01") == datetime(2026, 8, 9, 17, 45, 1)


# --- (2) EXIF-Drehung -----------------------------------------------------------


@pytest.mark.parametrize(
    ("orientation", "size", "probe", "color"),
    [
        (1, (200, 100), (10, 50), RED),  # links rot
        (3, (200, 100), (10, 50), BLUE),  # 180°: links jetzt blau
        (6, (100, 200), (50, 10), RED),  # 90° im Uhrzeigersinn: oben rot
        (8, (100, 200), (50, 10), BLUE),  # 90° gegen den Uhrzeigersinn: oben blau
    ],
)
def test_exif_rotation(umlaut_dir: Path, orientation: int, size: tuple[int, int],
                       probe: tuple[int, int], color: tuple[int, int, int]) -> None:
    src = make_jpeg(umlaut_dir / f"Drehung {orientation} ü.jpg", orientation=orientation)
    info = photo_info(src)
    assert (info.width, info.height) == size
    assert info.orientation == orientation
    img = open_photo(src)
    assert img.size == size
    assert _close(img.getpixel(probe), color)

    res = import_media([src])
    assert (res.media[0].width, res.media[0].height) == size
    assert res.media[0].portrait == (size[1] > size[0])


def test_exif_rotation_in_render_input(synthetic: Path, tmp_path: Path) -> None:
    """Das gedrehte Testfoto (2000×1500, Orientierung 6) wird hochkant gerendert."""
    src = synthetic / "Gedreht EXIF.jpg"
    info = photo_info(src)
    assert (info.width, info.height, info.orientation) == (1500, 2000, 6)
    from PIL import Image

    out = prepare_photo(src, 1920, 1080, tmp_path / "prep.png")
    with Image.open(out) as im:
        assert im.height > im.width


# --- (3) HEIC -------------------------------------------------------------------


def test_heic_is_read(synthetic: Path, umlaut_dir: Path) -> None:
    heic = synthetic / "Winter Schnee.heic"
    res = import_media([heic])
    m = res.media[0]
    assert m.kind == "photo"
    assert (m.width, m.height) == (1600, 1200)
    assert m.taken == datetime(2026, 1, 5, 12, 0)
    assert m.date_source == "exif"
    img = open_photo(heic)
    assert img.size == (1600, 1200) and img.mode == "RGB"

    # Selbst erzeugtes HEIC mit Drehung in einem Ordner mit Umlauten
    own = make_jpeg(umlaut_dir / "Größe Ü.heic", date="2026:12:24 18:00:00",
                    orientation=6, fmt="HEIF")
    m2 = import_media([own]).media[0]
    assert m2.taken == datetime(2026, 12, 24, 18, 0)
    assert (m2.width, m2.height) == (100, 200)
    rotated = open_photo(own)
    assert rotated.size == (100, 200)
    assert _close(rotated.getpixel((50, 10)), RED)


# --- (7) Duplikate ----------------------------------------------------------------


def test_duplicates_reported_not_removed(synthetic: Path, umlaut_dir: Path) -> None:
    orig = umlaut_dir / "Wörthersee.jpg"
    shutil.copy2(synthetic / "Querformat Wörthersee.jpg", orig)
    sub = umlaut_dir / "Kopien Ä"
    sub.mkdir()
    copy = sub / "Wörthersee (Kopie).jpg"
    shutil.copy2(orig, copy)
    # gleiche Größe, anderer Inhalt: kein Duplikat
    data = bytearray(orig.read_bytes())
    data[-3] ^= 0xFF
    similar = sub / "ähnlich.jpg"
    similar.write_bytes(bytes(data))
    shutil.copystat(orig, similar)
    other = make_jpeg(umlaut_dir / "anderes.jpg", date="2026:07:14 10:30:00")

    res = import_media([umlaut_dir])
    assert len(res.media) == 4  # nichts wird stillschweigend entfernt
    assert len(res.duplicates) == 1
    dup, original = res.duplicates[0]
    assert {dup, original} == {orig, copy}
    by_path = {m.path: m for m in res.media}
    assert by_path[dup].duplicate_of == original
    assert by_path[original].duplicate_of is None
    assert by_path[similar].duplicate_of is None
    assert by_path[other].duplicate_of is None
    msgs = res.messages()
    assert len(msgs) == 1 and "Wörthersee" in msgs[0] and "doppelt" in msgs[0]


# --- Robustheit -------------------------------------------------------------------


def test_collect_files_recursive_unique_and_filtered(synthetic: Path, umlaut_dir: Path) -> None:
    sub = umlaut_dir / "Unterordner ß"
    sub.mkdir()
    a = make_jpeg(umlaut_dir / "a.JPG")
    b = make_jpeg(sub / "b.jpeg")
    (umlaut_dir / "notiz.txt").write_text("x", "utf-8")
    shutil.copy2(synthetic / "Musik 1 – Frühling.mp3", umlaut_dir)
    found = collect_files([umlaut_dir, a, str(b)])
    assert found == [a, b]


def test_broken_files_are_reported_with_name_and_path(umlaut_dir: Path) -> None:
    """Kaputtes Foto: bleibt als „beschädigt“ in der Liste (Zeitleiste markiert es, der
    Export überspringt es). Kaputtes Video: wird wie bisher übersprungen. Jede Meldung nennt
    Dateiname und Pfad."""
    good = make_jpeg(umlaut_dir / "gut.jpg", date="2026:05:05 05:05:05")
    bad = umlaut_dir / "kaputt Ö.jpg"
    bad.write_bytes(b"\xff\xd8\xff kein richtiges Bild")
    bad_video = umlaut_dir / "kaputt.mp4"
    bad_video.write_bytes(b"\x00" * 100)
    res = import_media([umlaut_dir])
    assert {m.path for m in res.media} == {good, bad}
    damaged = {m.path: m for m in res.damaged}
    assert set(damaged) == {bad}
    assert damaged[bad].damage == "kein lesbares Bild (Datenmüll oder falsches Format)"
    assert damaged[bad].damage_readable == 0.0
    assert [p for p, _ in res.skipped] == [bad_video]
    messages = res.messages()
    assert len(messages) == 2
    assert any(f"„{bad.name}“ ({bad})" in m and "beschädigt" in m for m in messages)
    assert any(f"„{bad_video.name}“ ({bad_video})" in m and "übersprungen" in m
               for m in messages)


def test_motion_photo_uses_still_image(umlaut_dir: Path, synthetic: Path) -> None:
    still = make_jpeg(umlaut_dir / "still.jpg", date="2026:06:01 12:00:00")
    motion = umlaut_dir / "20260601_120000 Bewegung.jpg"
    video = (synthetic / "VFR Handy.mp4").read_bytes()
    motion.write_bytes(still.read_bytes() + b"MotionPhoto_Data" + video + b"\x00SEFT")
    still.unlink()
    res = import_media([umlaut_dir])
    m = res.media[0]
    assert m.kind == "photo" and m.motion_photo
    assert m.taken == datetime(2026, 6, 1, 12, 0)
    assert open_photo(motion).size == (200, 100)


# --- (8) Umlaute: Import bis Projektdatei ----------------------------------------------


def test_umlaut_paths_through_project(synthetic: Path, umlaut_dir: Path) -> None:
    from slideshow.core.chapters import build_items

    names = ["Querformat Wörthersee.jpg", "Hochkant Größe.jpg", "Winter Schnee.heic"]
    target = umlaut_dir / "Fotos – Äpfel & Öl"
    target.mkdir()
    for n in names:
        shutil.copy2(synthetic / n, target / n)
    res = import_media([target])
    assert {m.path.name for m in res.media} == set(names)
    items = build_items(res.media)
    project = Project(name="Jahresrückblick 2026", items=items, base_dir=umlaut_dir)
    project.save(umlaut_dir / "Projekt Ü.json")
    loaded = Project.load(umlaut_dir / "Projekt Ü.json")
    paths = [loaded.resolve(i.path) for i in loaded.items if i.path]
    assert all(p.is_file() for p in paths)
    assert [p.name for p in paths] == [m.path.name for m in res.media]
    assert all(isinstance(i, Item) for i in loaded.items)
    assert [i.taken for i in loaded.items if i.path] == [
        m.taken.isoformat(timespec="seconds") for m in res.media]
