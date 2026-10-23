"""Beschädigte Bilder: beim Import erkennen (vollständig dekodieren), beim Export überspringen
statt abbrechen, „trotzdem verwenden“ für abgeschnittene JPGs mit genug lesbarem Teil.
Bilder, die zu mindestens 99 % lesbar sind, werden automatisch verwendet. Fallen alle
Elemente eines Kapitels weg, entfällt auch sein Zwischentitel. Jede Meldung nennt
Dateiname und Pfad."""

from __future__ import annotations

import random
import shutil
from pathlib import Path

import pytest
from conftest import requires_ffmpeg
from PIL import Image, ImageFile

from slideshow.core.chapters import apply_chapters, media_items
from slideshow.core.importer import (
    DamagedFile,
    check_photo,
    damage_message,
    describe_file,
    import_media,
    open_photo,
)
from slideshow.core.project import (
    DAMAGE_MINOR_MIN,
    DAMAGE_USABLE_MIN,
    SCHEMA_VERSION,
    Item,
    Project,
)
from slideshow.render.assemble import RenderError, RenderOptions, render_project

REAL_DAMAGED = ("20180606_122427.jpg", "20180607_151823.jpg")


def make_photo(path: Path, size: tuple[int, int] = (800, 600)) -> Path:
    """Foto mit Rauschen: keine Bildzeile ist einfarbig, der lesbare Anteil ist eindeutig."""
    Image.effect_noise(size, 80).convert("RGB").save(path, quality=90)
    return path


def truncated(src: Path, target: Path, fraction: float) -> Path:
    data = src.read_bytes()
    target.write_bytes(data[: int(len(data) * fraction)])
    return target


def garbage(target: Path, size: int = 40_000) -> Path:
    target.write_bytes(random.Random(7).randbytes(size))
    return target


def slightly_cut(target: Path, missing: int = 1000) -> Path:
    """Hohes Foto (400×2000), am Ende fehlen `missing` Bytes. 1000 Bytes: nur die letzte
    Blockzeile fehlt, lesbar über 99 %. 12 000 Bytes: lesbar knapp unter 99 %."""
    src = make_photo(target.with_name(f"{target.stem} Quelle.jpg"), (400, 2000))
    data = src.read_bytes()
    src.unlink()
    target.write_bytes(data[: len(data) - missing])
    return target


@pytest.fixture
def damaged_set(umlaut_dir: Path) -> dict[str, Path]:
    good = make_photo(umlaut_dir / "gut Ä.jpg")
    return {
        "good": good,
        "cut95": truncated(good, umlaut_dir / "abgeschnitten 95 Ö.jpg", 0.95),
        "cut40": truncated(good, umlaut_dir / "abgeschnitten 40.jpg", 0.40),
        "garbage": garbage(umlaut_dir / "Datenmüll ß.jpg"),
    }


# --- Erkennen -----------------------------------------------------------------------


def test_intact_photo_is_ok(damaged_set: dict[str, Path]) -> None:
    check = check_photo(damaged_set["good"])
    assert check.ok and check.reason is None and check.readable == 1.0


def test_truncated_jpeg_is_detected_with_readable_part(damaged_set: dict[str, Path]) -> None:
    cut95 = check_photo(damaged_set["cut95"])
    assert cut95.reason == "Datei ist abgeschnitten, ein Teil der Bilddaten fehlt"
    assert 0.85 < cut95.readable < 1.0
    assert cut95.usable
    cut40 = check_photo(damaged_set["cut40"])
    assert not cut40.ok
    assert 0.2 < cut40.readable < 0.5
    assert not cut40.usable  # zu wenig lesbar für „trotzdem verwenden“
    assert DAMAGE_USABLE_MIN == 0.8


def test_garbage_file_is_detected(damaged_set: dict[str, Path], umlaut_dir: Path) -> None:
    check = check_photo(damaged_set["garbage"])
    assert check.reason == "kein lesbares Bild (Datenmüll oder falsches Format)"
    assert check.readable == 0.0 and not check.usable
    # gültiger JPEG-Anfang, danach Datenmüll: Kopf lesbar, Bilddaten nicht
    head = damaged_set["good"].read_bytes()[:600]
    mixed = umlaut_dir / "Kopf und Müll.jpg"
    mixed.write_bytes(head + random.Random(3).randbytes(60_000))
    check = check_photo(mixed)
    assert not check.ok and not check.usable


def test_open_photo_names_file_and_path(damaged_set: dict[str, Path]) -> None:
    for key in ("cut95", "garbage"):
        path = damaged_set[key]
        with pytest.raises(DamagedFile) as exc:
            open_photo(path)
        assert exc.value.path == path
        assert f"„{path.name}“ ({path})" in str(exc.value)
    assert describe_file(damaged_set["good"]) == (
        f"„{damaged_set['good'].name}“ ({damaged_set['good']})")


def test_tolerant_load_keeps_readable_part_without_global_switch(
        damaged_set: dict[str, Path]) -> None:
    img = open_photo(damaged_set["cut95"], tolerant=True)
    assert img.size == (800, 600)
    top = img.crop((0, 0, 800, 1)).getextrema()
    assert any(lo != hi for lo, hi in top)  # oben echtes Bild
    # Pillows globaler Schalter bleibt aus: andere Threads laden weiter streng
    assert ImageFile.LOAD_TRUNCATED_IMAGES is False
    with pytest.raises(DamagedFile):
        open_photo(damaged_set["cut95"])
    with pytest.raises(DamagedFile):  # Datenmüll bleibt auch tolerant unlesbar
        open_photo(damaged_set["garbage"], tolerant=True)


def test_missing_file_is_not_reported_as_damaged(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        open_photo(tmp_path / "fehlt.jpg")


def test_import_marks_damaged_files(damaged_set: dict[str, Path]) -> None:
    res = import_media(damaged_set.values())
    by_path = {m.path: m for m in res.media}
    assert set(by_path) == set(damaged_set.values())  # nichts fällt still heraus
    assert by_path[damaged_set["good"]].damage is None
    assert {m.path for m in res.damaged} == {damaged_set[k]
                                            for k in ("cut95", "cut40", "garbage")}
    assert not res.skipped
    messages = res.messages()
    for key in ("cut95", "cut40", "garbage"):
        p = damaged_set[key]
        msg = next(m for m in messages if f"„{p.name}“ ({p})" in m)
        assert "beschädigt" in msg and "übersprungen" in msg
        assert ("Trotzdem verwenden" in msg) == (key == "cut95")
    items = {Path(it.path or ""): it for it in media_items(res.media)}
    cut = items[damaged_set["cut95"].resolve()]
    assert cut.damage is not None and cut.damage_usable and cut.skip_damaged
    cut.use_damaged = True
    assert not cut.skip_damaged
    garbage_item = items[damaged_set["garbage"].resolve()]
    garbage_item.use_damaged = True  # nicht lesbar: wird trotzdem übersprungen
    assert garbage_item.skip_damaged


def test_damage_is_saved_in_project(damaged_set: dict[str, Path], tmp_path: Path) -> None:
    items = media_items(import_media([damaged_set["cut95"]]).media)
    items[0].use_damaged = True
    p = Project(items=items)
    p.save(tmp_path / "p.json")
    loaded = Project.load(tmp_path / "p.json").items[0]
    assert loaded.damage == items[0].damage
    assert loaded.damage_readable == pytest.approx(items[0].damage_readable)
    assert loaded.use_damaged


def test_damage_without_readable_share_is_not_used_automatically() -> None:
    """Schadensmarkierung ohne lesbaren Anteil (z. B. ältere Projektdatei): gilt als
    beschädigt, wird übersprungen und bietet kein „trotzdem verwenden“."""
    it = Item("photo", path="kaputt.jpg", damage="Bilddaten sind fehlerhaft")
    assert not it.damage_minor and not it.damage_usable
    assert it.skip_damaged and not it.tolerant_load
    it.use_damaged = True
    assert it.skip_damaged
    data = {"schema_version": SCHEMA_VERSION,
            "items": [{"kind": "photo", "path": "kaputt.jpg",
                       "damage": "Bilddaten sind fehlerhaft"}]}
    loaded = Project.from_dict(data, Path(".")).items[0]
    assert not loaded.damage_minor and loaded.skip_damaged


def test_minor_damage_is_used_automatically(umlaut_dir: Path) -> None:
    """Ab 99 % lesbar: automatisch verwenden, ohne „trotzdem verwenden“. Darunter bleibt es
    beim manuellen „trotzdem verwenden“."""
    assert DAMAGE_MINOR_MIN == 0.99
    minor = slightly_cut(umlaut_dir / "leicht beschädigt Ä.jpg", 1000)
    almost = slightly_cut(umlaut_dir / "knapp unter 99.jpg", 12000)
    check = check_photo(minor)
    assert not check.ok and check.readable >= 0.99 and check.minor
    check = check_photo(almost)
    assert 0.8 <= check.readable < 0.99 and check.usable and not check.minor

    res = import_media([minor, almost])
    by_name = {m.path.name: m for m in res.media}
    assert by_name[minor.name].damage_minor and not by_name[almost.name].damage_minor
    text = damage_message(by_name[minor.name])
    assert f"„{minor.name}“ ({minor})" in text
    assert "leicht beschädigt" in text and "vollständig lesbar und wird verwendet" in text
    assert "Trotzdem verwenden" in damage_message(by_name[almost.name])

    items = {Path(it.path or "").name: it for it in media_items(res.media)}
    m, a = items[minor.name], items[almost.name]
    assert m.damage_minor and not m.skip_damaged and m.tolerant_load and not m.use_damaged
    assert not a.damage_minor and a.skip_damaged and not a.tolerant_load and a.damage_usable
    a.use_damaged = True
    assert not a.skip_damaged and a.tolerant_load


# --- Export -------------------------------------------------------------------------


def photo_project(paths: list[Path]) -> Project:
    """Fotos à 1 s ohne Übergänge: Länge = Anzahl verwendeter Fotos × 30 Bilder."""
    items = media_items(import_media(paths).media)
    for it in items:
        it.duration = 1.0
    p = Project(items=items)
    p.settings.transition = "none"
    return p


def render(project: Project, tmp_path: Path) -> tuple[object, Path]:
    out = tmp_path / "Show Ü.mp4"
    res = render_project(project, RenderOptions(output=out, encoder="libx264", preview=True,
                                                cache_dir=tmp_path / "cache"))
    return res, out


@requires_ffmpeg
def test_export_skips_damaged_and_lists_them(damaged_set: dict[str, Path],
                                             tmp_path: Path) -> None:
    p = photo_project(list(damaged_set.values()))
    res, out = render(p, tmp_path)
    assert out.is_file()
    assert res.frames == 30  # nur das intakte Foto
    assert {s.path for s in res.skipped} == {damaged_set[k].resolve()
                                            for k in ("cut95", "cut40", "garbage")}
    for s in res.skipped:
        assert f"„{s.path.name}“ ({s.path})" in s.message()
    usable = {s.path.name for s in res.skipped if s.usable}
    assert usable == {damaged_set["cut95"].name}


@requires_ffmpeg
def test_export_detects_unmarked_damage(damaged_set: dict[str, Path], tmp_path: Path) -> None:
    """Älteres Projekt ohne Prüfung beim Import: Der Export erkennt den Schaden selbst,
    überspringt das Element und rechnet die Zeitleiste ohne es neu."""
    p = Project(items=[Item("photo", path=str(damaged_set[k]), duration=1.0)
                       for k in ("good", "cut95", "good", "garbage")])
    p.settings.transition = "fade"
    p.settings.transition_duration = 0.5
    res, out = render(p, tmp_path)
    assert out.is_file()
    # zwei intakte Fotos mit einem Übergang; der ist auf (30 - 1) // 2 = 14 Bilder begrenzt
    assert res.frames == 30 + 30 - 14
    skipped = {s.path: s for s in res.skipped}
    assert set(skipped) == {damaged_set["cut95"], damaged_set["garbage"]}
    assert skipped[damaged_set["cut95"]].usable  # lesbarer Anteil nachträglich bestimmt
    assert not skipped[damaged_set["garbage"]].usable


@requires_ffmpeg
def test_export_uses_damaged_photo_when_chosen(damaged_set: dict[str, Path],
                                               tmp_path: Path) -> None:
    p = photo_project([damaged_set["good"], damaged_set["cut95"], damaged_set["garbage"]])
    for it in p.items:
        it.use_damaged = True
    res, out = render(p, tmp_path)
    assert res.frames == 60  # gut + abgeschnitten (lesbarer Teil); Müll bleibt draußen
    assert [s.path.name for s in res.skipped] == [damaged_set["garbage"].name]


@requires_ffmpeg
def test_export_with_only_damaged_files_names_them(damaged_set: dict[str, Path],
                                                   tmp_path: Path) -> None:
    p = photo_project([damaged_set["cut40"], damaged_set["garbage"]])
    with pytest.raises(RenderError) as exc:
        render(p, tmp_path)
    for key in ("cut40", "garbage"):
        path = damaged_set[key].resolve()
        assert f"„{path.name}“ ({path})" in str(exc.value)


@requires_ffmpeg
def test_export_uses_minor_damage_automatically(damaged_set: dict[str, Path], umlaut_dir: Path,
                                                tmp_path: Path) -> None:
    minor = slightly_cut(umlaut_dir / "leicht beschädigt.jpg", 1000)
    almost = slightly_cut(umlaut_dir / "knapp unter 99.jpg", 12000)
    p = photo_project([damaged_set["good"], minor, almost])
    res, out = render(p, tmp_path)
    assert out.is_file()
    assert res.frames == 60  # gut + leicht beschädigt; knapp unter 99 % wartet auf Zustimmung
    assert [s.path.name for s in res.skipped] == [almost.name]
    assert not res.minor  # schon beim Import erkannt


@requires_ffmpeg
def test_export_uses_unmarked_minor_damage(damaged_set: dict[str, Path], umlaut_dir: Path,
                                           tmp_path: Path) -> None:
    """Älteres Projekt: Erst der Export merkt, dass ein Bild leicht beschädigt ist. Es wird
    mit dem lesbaren Teil verwendet statt übersprungen und als leicht beschädigt gemeldet."""
    minor = slightly_cut(umlaut_dir / "leicht beschädigt.jpg", 1000)
    p = Project(items=[Item("photo", path=str(path), duration=1.0)
                       for path in (damaged_set["good"], minor)])
    p.settings.transition = "none"
    res, out = render(p, tmp_path)
    assert out.is_file() and res.frames == 60
    assert not res.skipped
    assert [s.path for s in res.minor] == [minor]
    assert res.minor[0].readable >= DAMAGE_MINOR_MIN
    assert p.items[1].damage is None  # Projekt des Aufrufers bleibt unverändert


def chapter_project(good: Path, bad: Path) -> Project:
    """März: gut · April: nur kaputt · Mai: gut · Juni: kaputt und gut. Alles 1 s."""
    dated = [("2026-03-01", good), ("2026-04-01", bad), ("2026-05-01", good),
             ("2026-06-01", bad), ("2026-06-02", good)]
    items = apply_chapters([Item("photo", path=str(path), taken=f"{day}T10:00:00")
                            for day, path in dated])
    for it in items:
        it.duration = 1.0
    p = Project(items=items)
    p.settings.transition = "none"
    return p


@requires_ffmpeg
@pytest.mark.parametrize("marked", [True, False], ids=["beim Import", "erst beim Export"])
def test_empty_chapter_loses_its_title(damaged_set: dict[str, Path], tmp_path: Path,
                                       marked: bool) -> None:
    p = chapter_project(damaged_set["good"], damaged_set["garbage"])
    assert sum(1 for it in p.items if it.kind == "title") == 4
    if marked:
        for it in p.items:
            if it.path == str(damaged_set["garbage"]):
                it.damage, it.damage_readable = "kein lesbares Bild", 0.0
    res, out = render(p, tmp_path)
    assert out.is_file()
    # Zwischentitel März, Mai, Juni und drei gute Fotos; April fällt ganz weg
    assert res.frames == 6 * 30
    assert res.dropped_titles == ["April 2026"]
    assert [s.path for s in res.skipped] == [damaged_set["garbage"]]
    assert len(p.items) == 9  # Projekt des Aufrufers bleibt unverändert


# --- Echte Dateien ------------------------------------------------------------------


@pytest.fixture
def real_damaged(real_media: Path, tmp_path: Path) -> list[Path]:
    paths = [real_media / n for n in REAL_DAMAGED]
    if not all(p.is_file() for p in paths):
        pytest.skip("beschädigte Beispieldateien fehlen in tests/media/real")
    return paths


def test_real_damaged_files_are_detected(real_damaged: list[Path]) -> None:
    """Panoramen von Andreas: Pillow meldete beim Export „broken data stream“ ohne
    Dateinamen. Der Fehler sitzt am Ende, das Bild selbst ist ganz lesbar."""
    for path in real_damaged:
        check = check_photo(path)
        assert check.reason == "Bilddaten sind fehlerhaft"
        assert check.readable == 1.0 and check.usable and check.minor
        with pytest.raises(DamagedFile) as exc:
            open_photo(path)
        assert f"„{path.name}“ ({path})" in str(exc.value)
        assert open_photo(path, draft=(854, 480), tolerant=True).width > 0


@requires_ffmpeg
def test_real_damaged_files_used_automatically(real_damaged: list[Path], tmp_path: Path,
                                               synthetic: Path) -> None:
    """Beide Panoramen sind vollständig lesbar und werden ohne Nachfrage verwendet."""
    good = shutil.copy(synthetic / "Querformat Wörthersee.jpg", tmp_path / "gut.jpg")
    p = photo_project([Path(good), *real_damaged])
    damaged = [it for it in p.items if it.damage]
    assert len(damaged) == 2
    assert all(it.damage_minor and not it.skip_damaged for it in damaged)
    res, out = render(p, tmp_path)
    assert res.frames == 90 and not res.skipped and out.is_file()
