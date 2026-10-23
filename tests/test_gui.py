"""Oberfläche mit pytest-qt: Import, Duplikate, Laden/Speichern, Reihenfolge per Drag & Drop,
Kapitel, Eigenschaften, Musik, Vorschau-Hinweis, Export starten/abbrechen, Größenwarnung,
eine Instanz, Zeitleiste mit 1000 Elementen."""

from __future__ import annotations

import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QDropEvent, QImage
from PySide6.QtWidgets import QApplication, QMessageBox

from slideshow.app.mainwindow import ImportOutcome, MainWindow, export_summary
from slideshow.app.single import KEY_ENV, SingleInstance
from slideshow.core.chapters import is_chapter_title
from slideshow.core.project import Item, Project, TextOverlay

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_dialogs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Modale Dialoge würden den Test blockieren: mitschreiben und Standard wählen."""
    shown: list[str] = []

    def record(*args: object, **_kw: object) -> QMessageBox.StandardButton:
        shown.append(" ".join(str(a) for a in args[1:3]))
        return QMessageBox.StandardButton.No

    for name in ("warning", "information", "question", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(record))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()) or 0)
    return shown


@pytest.fixture
def opened() -> list[Path]:
    return []


@pytest.fixture
def window(qtbot, tmp_path: Path, opened: list[Path]):  # noqa: ANN001, ANN201
    w = MainWindow(cache_dir=tmp_path / "cache", encoder="libx264", open_file=opened.append)
    w.ask_duplicates = lambda outcome: False  # type: ignore[method-assign]
    qtbot.addWidget(w)
    w.resize(1280, 700)
    w.show()
    yield w
    w.dirty = False
    w.shutdown()


def wait_import(qtbot, w: MainWindow, paths: list[Path], timeout: int = 60_000) -> None:  # noqa: ANN001
    task = w.import_paths(paths)
    assert task is not None
    qtbot.waitUntil(lambda: not task.running and not w.import_row.active, timeout=timeout)


def wait_render(qtbot, w: MainWindow, timeout: int = 180_000) -> None:  # noqa: ANN001
    qtbot.waitUntil(lambda: not w.rendering and not w.render_row.active, timeout=timeout)


def copy_media(synthetic: Path, target: Path, *names: str) -> list[Path]:
    out = []
    for n in names:
        shutil.copy2(synthetic / n, target / n)
        out.append(target / n)
    return out


def photos_project(synthetic: Path, folder: Path) -> Project:
    """Fotos aus drei Monaten, Zeiten und Pfade fest vorgegeben."""
    a, b = copy_media(synthetic, folder, "Querformat Wörthersee.jpg", "Hochkant Größe.jpg")
    items = [
        Item("title", duration=2.0, text=TextOverlay("Jahr 2026", position="center")),
        Item("photo", path=str(a), taken="2026-03-01T10:00:00"),
        Item("photo", path=str(b), taken="2026-03-02T10:00:00"),
        Item("photo", path=str(a), taken="2026-05-01T10:00:00"),
        Item("photo", path=str(b), taken="2026-07-01T10:00:00"),
    ]
    return Project(name="Urlaub Wörthersee 2026", items=items, base_dir=folder)


def kinds(w: MainWindow) -> list[str]:
    return [("chapter:" + (it.chapter or "")) if is_chapter_title(it) else
            (it.taken or it.kind) for it in w.project.items]


# --- Import ------------------------------------------------------------------------------


def test_import_sorts_adds_chapters_and_prepares_videos(qtbot, window: MainWindow,  # noqa: ANN001
                                                        synthetic: Path, umlaut_dir: Path) -> None:
    files = copy_media(synthetic, umlaut_dir, "Querformat Wörthersee.jpg", "Hochkant Größe.jpg",
                       "Winter Schnee.heic", "Video mit Ton.mp4")
    wait_import(qtbot, window, [umlaut_dir])
    items = window.project.items
    assert items[0].kind == "title" and not is_chapter_title(items[0])  # Titelkarte vorn
    media = [it for it in items if it.kind != "title"]
    assert {Path(it.path or "").name for it in media} == {f.name for f in files}
    taken = [it.taken for it in media]
    assert taken == sorted(taken)  # nach Aufnahmedatum
    assert any(is_chapter_title(it) for it in items)  # Zwischentitel pro Monat
    assert window.notice.isVisible() and "hinzugefügt" in window.notice.label.text()
    assert window.dirty and window.windowTitle().startswith("*")
    # Video wird im Hintergrund umgewandelt, Fortschritt sichtbar, danach ausgeblendet
    qtbot.waitUntil(lambda: window.video_prep.pending == 0, timeout=120_000)
    qtbot.waitUntil(lambda: not window.video_row.active, timeout=10_000)
    assert not window.activity.isVisible()  # Zeile verschwindet ohne Aktivität
    assert list((window.cache_dir / "normalized").glob("*.mp4"))
    assert not list(window.cache_dir.rglob("*.part*"))


def test_video_progress_is_shown(qtbot, window: MainWindow, synthetic: Path,  # noqa: ANN001
                                 umlaut_dir: Path) -> None:
    seen: list[str] = []
    fractions: list[float] = []
    window.video_prep.status.connect(lambda text, f: (seen.append(text), fractions.append(f)))
    copy_media(synthetic, umlaut_dir, "VFR Handy.mp4")
    wait_import(qtbot, window, [umlaut_dir])
    qtbot.waitUntil(lambda: window.video_prep.pending == 0, timeout=120_000)
    assert any("1 von 1" in s and "„VFR Handy.mp4“" in s for s in seen)
    assert all("–" not in s and "—" not in s for s in seen)
    assert any(f > 0 for f in fractions)  # Balken bekommt echte Werte


def test_duplicates_are_reported_not_silently_removed(qtbot, window: MainWindow,  # noqa: ANN001
                                                      synthetic: Path, umlaut_dir: Path) -> None:
    (a,) = copy_media(synthetic, umlaut_dir, "Querformat Wörthersee.jpg")
    shutil.copy2(a, umlaut_dir / "Kopie.jpg")
    asked: list[ImportOutcome] = []

    def keep(outcome: ImportOutcome) -> bool:
        asked.append(outcome)
        return False  # alle übernehmen

    window.ask_duplicates = keep  # type: ignore[method-assign]
    wait_import(qtbot, window, [umlaut_dir])
    assert len(asked) == 1 and len(asked[0].duplicates) == 1
    media = [it for it in window.project.items if it.kind == "photo"]
    assert len(media) == 2  # nichts stillschweigend entfernt
    assert window.model.duplicates  # in der Zeitleiste als „doppelt“ markiert
    assert "doppelt" in window.notice.label.text()

    # Zweiter Import: identische Datei unter neuem Namen -> wird gemeldet und weggelassen
    shutil.copy2(a, umlaut_dir / "Noch eine Kopie.jpg")
    window.ask_duplicates = lambda outcome: (asked.append(outcome), True)[1]  # type: ignore
    wait_import(qtbot, window, [umlaut_dir / "Noch eine Kopie.jpg", a])
    assert len(asked) == 2
    assert {d.name for d, _ in asked[1].duplicates} == {"Noch eine Kopie.jpg"}
    assert len([it for it in window.project.items if it.kind == "photo"]) == 2
    assert "schon im Projekt" in window.notice.label.text()


# --- Laden und Speichern -----------------------------------------------------------------


def test_save_and_load_roundtrip(qtbot, window: MainWindow, synthetic: Path,  # noqa: ANN001
                                 umlaut_dir: Path, tmp_path: Path) -> None:
    window.set_project(photos_project(synthetic, umlaut_dir), None)
    window.timeline.select_rows([2])
    panel = window.item_panel
    panel.text.setPlainText("Grüße vom Wörthersee – ß")
    panel.duration.setValue(6.5)
    panel.fit.setCurrentIndex(panel.fit.findData("crop"))
    panel.transition.setCurrentIndex(panel.transition.findData("wipeleft"))
    window.music_panel.add_tracks([synthetic / "Musik 1 – Frühling.mp3"])
    window.project_panel.photo_duration.setValue(3.0)
    assert window.dirty

    target = umlaut_dir / "Projekt Wörthersee.json"
    window.save_to(target)
    assert not window.dirty and window.windowTitle().startswith("Projekt Wörthersee.json")
    data = target.read_text("utf-8")
    assert "Grüße vom Wörthersee – ß" in data  # Umlaute lesbar gespeichert

    other = MainWindow(cache_dir=tmp_path / "cache2", encoder="libx264")
    qtbot.addWidget(other)
    try:
        other.load_from(target)
        assert other.project.to_dict() == window.project.to_dict()
        it = other.project.items[2]
        assert (it.text and it.text.text) == "Grüße vom Wörthersee – ß"
        assert (it.duration, it.fit, it.transition) == (6.5, "crop", "wipeleft")
        assert other.project.settings.photo_duration == 3.0
        assert other.music_panel.list.count() == 1
        assert not other.dirty
        other.timeline.select_rows([2])
        assert other.item_panel.text.toPlainText() == "Grüße vom Wörthersee – ß"
        other.item_panel.size.setValue(70)
        assert other.dirty and other.windowTitle().startswith("*")
    finally:
        other.dirty = False
        other.shutdown()


def test_load_reports_missing_files(qtbot, window: MainWindow, synthetic: Path,  # noqa: ANN001
                                    umlaut_dir: Path) -> None:
    p = photos_project(synthetic, umlaut_dir)
    target = umlaut_dir / "p.json"
    p.save(target)
    (umlaut_dir / "Hochkant Größe.jpg").unlink()
    window.load_from(target)
    assert window.notice.isVisible() and "nicht gefunden" in window.notice.label.text()


# --- Reihenfolge und Kapitel --------------------------------------------------------------


def drop_rows(w: MainWindow, rows: list[int], before_row: int) -> None:
    """Wie Ziehen und Loslassen in der Zeitleiste: links auf die Kachel before_row."""
    view = w.timeline
    model = w.model
    mime = model.mimeData([model.index(r) for r in rows])
    if before_row >= model.rowCount():
        last = view.visualRect(model.index(model.rowCount() - 1))
        pos = QPoint(last.right() - 2, last.center().y())
    else:
        rect = view.visualRect(model.index(before_row))
        pos = QPoint(rect.left() + 5, rect.center().y())
    n = len(w.project.items) - sum(1 for it in w.project.items if is_chapter_title(it))
    event = QDropEvent(pos.toPointF(), Qt.DropAction.MoveAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    view.dropEvent(event)
    assert event.isAccepted()
    # Nichts geht verloren oder wird verdoppelt
    assert len(w.project.items) - sum(1 for it in w.project.items if is_chapter_title(it)) == n


def test_reorder_by_drag_and_drop(qtbot, window: MainWindow, synthetic: Path,  # noqa: ANN001
                                  umlaut_dir: Path) -> None:
    window.set_project(photos_project(synthetic, umlaut_dir), None)
    assert kinds(window) == ["title", "chapter:2026-03", "2026-03-01T10:00:00",
                             "2026-03-02T10:00:00", "chapter:2026-05", "2026-05-01T10:00:00",
                             "chapter:2026-07", "2026-07-01T10:00:00"]
    qtbot.wait(50)
    # Juli-Foto ganz nach vorn hinter die Titelkarte ziehen
    drop_rows(window, [7], 1)
    assert kinds(window) == ["title", "chapter:2026-07", "2026-07-01T10:00:00",
                             "chapter:2026-03", "2026-03-01T10:00:00", "2026-03-02T10:00:00",
                             "chapter:2026-05", "2026-05-01T10:00:00"]
    assert window.timeline.selected_rows() == [2]  # verschobenes Element bleibt markiert
    assert window.dirty

    # Zwei markierte Elemente ans Ende ziehen
    drop_rows(window, [4, 5], window.model.rowCount())
    assert kinds(window)[-3:] == ["chapter:2026-03", "2026-03-01T10:00:00",
                                  "2026-03-02T10:00:00"]
    assert len(window.timeline.selected_rows()) == 2


def test_reorder_without_chapters(qtbot, window: MainWindow, synthetic: Path,  # noqa: ANN001
                                  umlaut_dir: Path) -> None:
    window.set_project(photos_project(synthetic, umlaut_dir), None)
    window.project_panel.chapters.setChecked(False)
    assert kinds(window) == ["title", "2026-03-01T10:00:00", "2026-03-02T10:00:00",
                             "2026-05-01T10:00:00", "2026-07-01T10:00:00"]
    drop_rows(window, [1], 4)
    assert kinds(window) == ["title", "2026-03-02T10:00:00", "2026-05-01T10:00:00",
                             "2026-03-01T10:00:00", "2026-07-01T10:00:00"]


def test_chapters_toggle_keeps_edited_titles(qtbot, window: MainWindow,  # noqa: ANN001
                                             synthetic: Path, umlaut_dir: Path) -> None:
    window.set_project(photos_project(synthetic, umlaut_dir), None)
    row = next(i for i, it in enumerate(window.project.items) if it.chapter == "2026-05"
               and is_chapter_title(it))
    window.timeline.select_rows([row])
    assert "Zwischentitel Mai 2026" in window.item_panel.header.text()
    window.item_panel.text.setPlainText("Pfingsten am See")
    window.item_panel.duration.setValue(2.0)

    window.project_panel.chapters.setChecked(False)
    assert not any(is_chapter_title(it) for it in window.project.items)
    assert window.project.settings.chapters is False

    window.project_panel.chapters.setChecked(True)
    may = next(it for it in window.project.items if is_chapter_title(it)
               and it.chapter == "2026-05")
    assert may.text is not None and may.text.text == "Pfingsten am See"
    assert may.duration == 2.0


def test_title_card_and_item_properties(qtbot, window: MainWindow, synthetic: Path,  # noqa: ANN001
                                        umlaut_dir: Path) -> None:
    window.set_project(photos_project(synthetic, umlaut_dir), None)
    panel = window.item_panel

    # Titelkarte: Text auf Farbfläche, dann auf Foto
    window.timeline.select_rows([0])
    assert panel.title_box.isVisibleTo(window) and panel.header.text() == "Titelkarte"
    panel.background.set_color("#336699")
    panel.background.colorChanged.emit("#336699")
    assert window.project.items[0].background == "#336699"
    panel.set_background_photo(str(umlaut_dir / "Querformat Wörthersee.jpg"))
    assert window.project.items[0].path is not None
    assert "Querformat" in panel.bg_name.text()

    # Neue Titelkarte vor dem markierten Element (vor dessen Monats-Zwischentitel)
    photo_a = window.project.items[2]
    window.timeline.select_rows([2])
    window.add_title_card()
    (row,) = window.timeline.selected_rows()
    new_title = window.project.items[row]
    assert new_title.kind == "title" and not is_chapter_title(new_title)
    assert window.project.items.index(photo_a) > row

    # Mehrere Fotos markieren: Änderung gilt für alle
    photos = [i for i, it in enumerate(window.project.items) if it.kind == "photo"]
    window.timeline.select_rows(photos)
    assert "4 Elemente" in panel.header.text()
    panel.duration.setValue(2.5)
    panel.fit.setCurrentIndex(panel.fit.findData("black"))
    panel.position.setCurrentIndex(panel.position.findData("top"))
    panel.bar.setChecked(False)
    for i in photos:
        it = window.project.items[i]
        assert (it.duration, it.fit) == (2.5, "black")
        assert it.text is not None and (it.text.position, it.text.bar) == ("top", False)
    # Kachel zeigt die neue Dauer
    assert "2,5 s" in window.model.index(photos[0]).data(window.model.SubtitleRole)

    # Entfernen
    n = len(window.project.items)
    window.timeline.select_rows([photos[0]])
    window.remove_selected()
    assert len(window.project.items) == n - 1


def test_music_playlist(qtbot, window: MainWindow, synthetic: Path) -> None:  # noqa: ANN001
    panel = window.music_panel
    m1, m2 = synthetic / "Musik 1 – Frühling.mp3", synthetic / "Musik 2 – Sommer.m4a"
    panel.add_tracks([m1, m2, synthetic / "Querformat Wörthersee.jpg"])  # Foto wird ignoriert
    tracks = window.project.music.tracks
    assert [Path(t).name for t in tracks] == [m1.name, m2.name]
    assert "Musik insgesamt" in panel.summary.text()
    panel.list.setCurrentRow(1)
    panel.up_button.click()
    assert [Path(t).name for t in window.project.music.tracks] == [m2.name, m1.name]
    panel.duck.setValue(30)
    panel.fade.setValue(5)
    assert window.project.music.duck_volume == pytest.approx(0.3)
    assert window.project.music.fade_out == 5.0
    panel.list.setCurrentRow(0)
    panel.list.item(0).setSelected(True)
    panel.remove_button.click()
    assert [Path(t).name for t in window.project.music.tracks] == [m1.name]
    assert window.dirty


# --- Export, Vorschau ---------------------------------------------------------------------


def test_export_size_warning_over_4gb(qtbot, window: MainWindow, synthetic: Path,  # noqa: ANN001
                                      umlaut_dir: Path, tmp_path: Path) -> None:
    text, too_big = export_summary(600, "1080p")
    assert not too_big and "MB" in text
    text, too_big = export_summary(5 * 3600, "1080p")
    assert too_big and "4 GB" in text and "FAT32" in text

    p = photos_project(synthetic, umlaut_dir)
    p.items[1].duration = 5 * 3600.0
    window.set_project(p, None)
    asked: list[tuple[str, bool]] = []
    window.confirm_export = lambda t, big: (asked.append((t, big)), False)[1]  # type: ignore
    assert window.export_to(tmp_path / "x.mp4") is None  # abgelehnt: kein Export
    assert asked and asked[0][1] is True
    assert not window.rendering
    # Auch die Statuszeile warnt, sobald die Dauer berechnet ist
    qtbot.waitUntil(lambda: "4 GB" in window.summary_label.text(), timeout=10_000)


@pytest.mark.slow
def test_export_start_and_cancel(qtbot, window: MainWindow, synthetic: Path,  # noqa: ANN001
                                 umlaut_dir: Path) -> None:
    p = photos_project(synthetic, umlaut_dir)
    for it in p.items:
        it.duration = 30.0  # lange genug, um mittendrin abzubrechen
    window.set_project(p, None)
    out = umlaut_dir / "Show Ö.mp4"
    task = window.export_to(out, confirm=False)
    assert task is not None and window.rendering
    assert window.render_row.active and window.activity.isVisible()
    assert not window.act_export.isEnabled() and not window.act_preview.isEnabled()
    qtbot.waitUntil(lambda: window.render_row.bar.value() > 0, timeout=60_000)
    # Oberfläche bleibt bedienbar, während im Hintergrund gerendert wird
    photo = next(i for i, it in enumerate(window.project.items) if it.kind == "photo")
    window.timeline.select_rows([photo])
    assert window.item_panel.header.text().startswith("Foto")
    # Fortschritt ist im Balken sichtbar, Statustext ohne Gedankenstrich
    assert window.activity.bar.value() > 0
    assert window.activity.bar.text().endswith("%")
    assert "–" not in window.activity.summary.text()
    assert window.activity.primary is window.render_row
    qtbot.mouseClick(window.activity.cancel_button, Qt.MouseButton.LeftButton)
    wait_render(qtbot, window, timeout=30_000)
    assert window.last_result is None
    assert not out.exists()
    assert not list(umlaut_dir.glob("*.part*"))
    assert "Abgebrochen" in window.notice.label.text()
    assert window.act_export.isEnabled() and window.act_preview.isEnabled()


@pytest.mark.slow
def test_export_finishes(qtbot, window: MainWindow, synthetic: Path,  # noqa: ANN001
                         umlaut_dir: Path) -> None:
    p = photos_project(synthetic, umlaut_dir)
    for it in p.items:
        it.duration = 1.5
    window.set_project(p, None)
    out = umlaut_dir / "Fertig Ö.mp4"
    assert window.export_to(out, confirm=False) is not None
    wait_render(qtbot, window)
    assert out.is_file() and window.last_result is not None
    assert window.notice.kind == "success" and "Fertig" in window.notice.label.text()


@pytest.mark.slow
def test_preview_shows_notice_when_originals_are_used(qtbot, window: MainWindow,  # noqa: ANN001
                                                      synthetic: Path, umlaut_dir: Path,
                                                      opened: list[Path],
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    (video,) = copy_media(synthetic, umlaut_dir, "HDR HLG.mp4")
    monkeypatch.setattr(window.video_prep, "submit", lambda paths: 0)  # noch nicht umgewandelt
    p = photos_project(synthetic, umlaut_dir)
    p.items.append(Item("video", path=str(video), duration=1.0, taken="2026-08-01T10:00:00"))
    window.set_project(p, None)
    rows = [i for i, it in enumerate(window.project.items) if it.kind == "video"]
    window.timeline.select_rows(rows)
    assert window.preview_range() == (rows[0] + 1, rows[0] + 1)
    assert window.on_preview() is not None
    wait_render(qtbot, window)
    assert opened and opened[-1].is_file()  # Vorschau wird geöffnet
    assert window.notice.isVisible() and window.notice.kind == "warning"
    assert "Original" in window.notice.label.text()
    assert "HDR HLG.mp4" in window.notice.label.text()


@pytest.mark.slow
def test_preview_without_notice_when_converted(qtbot, window: MainWindow,  # noqa: ANN001
                                               synthetic: Path, umlaut_dir: Path,
                                               opened: list[Path]) -> None:
    (video,) = copy_media(synthetic, umlaut_dir, "Video mit Ton.mp4")
    p = Project(items=[Item("video", path=str(video), duration=1.0)], base_dir=umlaut_dir)
    window.set_project(p, None)
    qtbot.waitUntil(lambda: window.video_prep.pending == 0, timeout=120_000)
    window.timeline.select_rows([0])
    window.on_preview()
    wait_render(qtbot, window)
    assert opened
    assert not window.notice.isVisible() or "Original" not in window.notice.label.text()


@pytest.mark.slow
def test_gui_saved_project_renders_identically_via_cli(qtbot, window: MainWindow,  # noqa: ANN001
                                                       synthetic: Path, umlaut_dir: Path,
                                                       tmp_path: Path) -> None:
    from mediacheck import framemd5

    from slideshow.cli import main as cli_main

    copy_media(synthetic, umlaut_dir, "Querformat Wörthersee.jpg", "Hochkant Größe.jpg",
               "Video mit Ton.mp4")
    wait_import(qtbot, window, [umlaut_dir])
    window.project_panel.chapters.setChecked(False)
    window.timeline.select_rows(list(range(len(window.project.items))))
    window.item_panel.duration.setValue(1.5)
    photo = next(i for i, it in enumerate(window.project.items) if it.kind == "photo")
    window.timeline.select_rows([photo])
    window.item_panel.text.setPlainText("Grüße aus Kärnten – ß")
    window.music_panel.add_tracks([synthetic / "Musik 2 – Sommer.m4a"])
    project_file = umlaut_dir / "Mein Projekt Ö.json"
    window.save_to(project_file)

    gui_out = tmp_path / "gui.mp4"
    assert window.export_to(gui_out, confirm=False) is not None
    wait_render(qtbot, window)
    assert gui_out.is_file()

    cli_out = tmp_path / "cli.mp4"
    code = cli_main(["render", str(project_file), "-o", str(cli_out), "--encoder", "libx264",
                     "--cache-dir", str(tmp_path / "cli-cache")])
    assert code == 0
    assert Project.load(project_file).to_dict() == window.project.to_dict()
    assert framemd5(gui_out) == framemd5(cli_out)


# --- Eine Programminstanz ----------------------------------------------------------------


def test_single_instance_lock(qtbot) -> None:  # noqa: ANN001
    key = f"test-{os.getpid()}-{time.monotonic_ns()}"
    first = SingleInstance(key)
    second = SingleInstance(key)
    try:
        assert first.acquire()
        assert not second.acquire()
        first.release()
        assert second.acquire()  # nach dem Beenden der ersten wieder frei
    finally:
        first.release()
        second.release()


def test_single_instance_survives_crashed_first_instance(qtbot) -> None:  # noqa: ANN001
    key = f"crash-{os.getpid()}-{time.monotonic_ns()}"
    code = ("import os;from PySide6.QtCore import QCoreApplication;"
            "from slideshow.app.single import SingleInstance as S;"
            f"app=QCoreApplication([]);s=S({key!r});assert s.acquire();os._exit(0)")
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True, timeout=60)
    inst = SingleInstance(key)
    try:
        assert inst.acquire()  # Sperre des abgestürzten Prozesses wird erkannt
    finally:
        inst.release()


def test_second_start_brings_window_to_front(qtbot, window: MainWindow,  # noqa: ANN001
                                             synthetic: Path, umlaut_dir: Path) -> None:
    key = f"front-{os.getpid()}-{time.monotonic_ns()}"
    inst = SingleInstance(key)
    assert inst.acquire()
    inst.activated.connect(window.bring_to_front)
    project_file = umlaut_dir / "Zweiter Start Ö.json"
    photos_project(synthetic, umlaut_dir).save(project_file)
    window.showMinimized()
    qtbot.waitUntil(window.isMinimized)
    env = {**os.environ, KEY_ENV: key, "QT_QPA_PLATFORM": "offscreen"}
    proc = subprocess.Popen([sys.executable, "-m", "slideshow", "gui", str(project_file)],
                            cwd=ROOT, env=env)
    try:
        # Zweiter Start beendet sich sofort, die laufende Instanz kommt nach vorne und
        # öffnet die mitgegebene Projektdatei
        qtbot.waitUntil(lambda: proc.poll() is not None, timeout=60_000)
        assert proc.returncode == 0
        qtbot.waitUntil(lambda: not window.isMinimized() and window.isVisible(), timeout=10_000)
        qtbot.waitUntil(lambda: window.project_path == project_file.resolve(), timeout=10_000)
        assert window.project.name == "Urlaub Wörthersee 2026"
    finally:
        if proc.poll() is None:
            proc.kill()
        inst.release()


# --- Zeitleiste mit 1000 Elementen ---------------------------------------------------------


def test_timeline_with_1000_items_stays_responsive(qtbot, window: MainWindow,  # noqa: ANN001
                                                   tmp_path: Path) -> None:
    folder = tmp_path / "Fotos 1000 Ä"
    folder.mkdir()
    items = []
    for i in range(1000):
        p = folder / f"Bild {i:04d}.jpg"
        Image.new("RGB", (64, 48), ((i * 7) % 256, (i * 13) % 256, (i * 29) % 256)).save(p)
        month = 1 + i * 12 // 1000
        items.append(Item("photo", path=str(p), taken=f"2026-{month:02d}-01T{i % 24:02d}:00:00"))
    project = Project(items=items, base_dir=folder)

    started = time.perf_counter()
    window.set_project(project, None)
    QApplication.processEvents()
    load_ms = (time.perf_counter() - started) * 1000
    assert window.model.rowCount() == 1000 + 12  # mit Zwischentiteln

    from slideshow.app.timeline import ZOOM_DEFAULT, ZOOM_MIN

    view = window.timeline
    bar = view.verticalScrollBar()
    assert load_ms < 1000
    for zoom in (ZOOM_DEFAULT, ZOOM_MIN):  # kleinste Größe: die meisten Kacheln sichtbar
        window.zoom.setValue(zoom)
        QApplication.processEvents()
        assert bar.maximum() > 0
        timings = []
        positions = [round(bar.maximum() * k / 30) for k in range(31)]
        positions += positions[::-1][::3]
        for pos in positions:
            t0 = time.perf_counter()
            bar.setValue(pos)
            view.viewport().repaint()  # synchron zeichnen, wie beim echten Scrollen
            QApplication.processEvents()
            timings.append((time.perf_counter() - t0) * 1000)
            qtbot.wait(5)  # Vorschaubilder kommen währenddessen im Hintergrund
        worst = max(timings)
        print(f"\nKachelbreite {zoom}: Laden {load_ms:.0f} ms, Scrollen Median "
              f"{statistics.median(timings):.1f} ms, Maximum {worst:.1f} ms")
        assert worst < 100, f"Scroll-Reaktion zu langsam: {worst:.0f} ms (Breite {zoom})"

    # Nach dem Scrollen erscheinen die Vorschaubilder der sichtbaren Kacheln
    visible = [r for r in range(window.model.rowCount())
               if view.viewport().rect().intersects(view.visualRect(window.model.index(r)))
               and window.model.tile(r).thumb_key is not None]
    assert visible
    qtbot.waitUntil(lambda: all(window.model.tile_pixmap(window.model.tile(r)) is not None
                                for r in visible), timeout=60_000)

    # Verschieben in der großen Zeitleiste bleibt flüssig
    t0 = time.perf_counter()
    drop_rows(window, [5], 900)
    QApplication.processEvents()
    move_ms = (time.perf_counter() - t0) * 1000
    assert move_ms < 500, f"Verschieben zu langsam: {move_ms:.0f} ms"


# --- Abnahme-Rückmeldung: Standbild, Fortschritt, Aktivitätszeile, Kacheln, Zoom, Texte -----


def still_image(window: MainWindow) -> QImage:
    assert window.still.pixmap is not None
    return window.still.pixmap.toImage()


def rgb(img: QImage, x: int, y: int) -> tuple[int, int, int]:
    c = img.pixelColor(x, y)
    return c.red(), c.green(), c.blue()


@pytest.mark.slow
def test_still_preview_follows_changes_within_1s(qtbot, window: MainWindow,  # noqa: ANN001
                                                 synthetic: Path, umlaut_dir: Path) -> None:
    (portrait,) = copy_media(synthetic, umlaut_dir, "Hochkant Größe.jpg")
    p = Project(items=[Item("photo", path=str(portrait))], base_dir=umlaut_dir)
    window.set_project(p, None)
    assert window.still.pixmap is None and "Kein Element" in window.still.message
    with qtbot.waitSignal(window.still.updated, timeout=10_000):
        window.timeline.select_rows([0])
    img = still_image(window)
    assert (img.width(), img.height()) == (640, 360)
    assert sum(rgb(img, 4, 180)) > 30  # unscharfer Hintergrund statt schwarzer Rand

    panel = window.item_panel
    timings = []

    def change(action) -> QImage:  # noqa: ANN001
        t0 = time.perf_counter()
        with qtbot.waitSignal(window.still.updated, timeout=1000):  # innerhalb 1 s
            action()
        timings.append(time.perf_counter() - t0)
        return still_image(window)

    img = change(lambda: panel.fit.setCurrentIndex(panel.fit.findData("black")))
    assert max(rgb(img, 4, 180)) < 20  # schwarze Ränder links und rechts
    img = change(lambda: panel.fit.setCurrentIndex(panel.fit.findData("crop")))
    assert sum(rgb(img, 4, 180)) > 150  # Bild füllt bis zum Rand
    assert window.model.tile(0).fit == "crop"  # Kachel zeigt denselben Modus

    def red_text() -> None:
        panel.bar_color.set_color("#FF0000")
        panel.bar_color.colorChanged.emit("#FF0000")
        panel.bar_opacity.setValue(100)
        panel.text.setPlainText("Grüße aus Kärnten")

    img = change(red_text)
    bottom = [rgb(img, x, y) for x in (8, 320, 632) for y in range(270, 356, 4)]
    assert any(r > 200 and g < 60 and b < 60 for r, g, b in bottom)  # roter Textbalken
    print(f"\nStandbild nach Änderung: {', '.join(f'{t * 1000:.0f} ms' for t in timings)}")

    # Erneutes Anwählen zeigt das Bild aus dem Cache sofort wieder
    window.timeline.select_rows([])
    with qtbot.waitSignal(window.still.updated, timeout=1000):
        window.timeline.select_rows([0])


def test_activity_line_combines_tasks(qtbot) -> None:  # noqa: ANN001
    from slideshow.app.activity import ActivityPanel, TaskRow

    panel = ActivityPanel()
    qtbot.addWidget(panel)
    render = panel.add_row(TaskRow("r", "Video erstellen", 3))
    imp = panel.add_row(TaskRow("i", "Einlesen", 2))
    video = panel.add_row(TaskRow("v", "Videos umwandeln", 1))
    assert not panel.isVisible()  # nur bei Aktivität

    cancelled: list[str] = []
    video.start("Videos werden für die Show umgewandelt: 1 von 3", lambda: cancelled.append("v"))
    assert panel.isVisible() and panel.primary is video
    assert not panel.toggle.isVisible()  # eine Aufgabe: nichts aufzuklappen
    video.update_progress(video.text, 0.25)
    assert panel.bar.value() == 250

    render.start("Video wird erstellt", lambda: cancelled.append("r"))
    imp.start("Fotos und Videos werden eingelesen: 3 von 9", None)
    assert panel.primary is render  # wichtigste Aufgabe vorn
    assert "außerdem: Einlesen, Videos umwandeln" in panel.summary.text()
    assert panel.toggle.isVisible() and panel.toggle.text() == "Details (3)"
    assert not any(r.isVisible() for r in (render, imp, video))  # zugeklappt: eine Zeile
    panel.set_expanded(True)
    assert all(r.isVisible() for r in (render, imp, video))
    assert not imp.cancel_button.isVisible()  # ohne Abbruchmöglichkeit kein Knopf

    panel.cancel_button.click()  # bricht die vorne stehende Aufgabe ab
    assert cancelled == ["r"]
    render.finish()
    assert panel.primary is imp and "außerdem: Videos umwandeln" in panel.summary.text()
    imp.finish()
    video.finish()
    assert not panel.isVisible()


def test_tiles_show_fit_mode(qtbot) -> None:  # noqa: ANN001
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QPainter, QPixmap

    from slideshow.app.timeline import paint_fit

    portrait = QPixmap(50, 100)
    portrait.fill(QColor("#E03030"))

    def tile(fit: str) -> QImage:
        img = QImage(160, 90, QImage.Format.Format_RGB32)
        img.fill(QColor("#FFFFFF"))
        p = QPainter(img)
        small = portrait.scaledToWidth(12)
        paint_fit(p, QRect(0, 0, 160, 90), portrait, fit, lambda: small)
        p.end()
        return img

    left = {fit: rgb(tile(fit), 3, 45) for fit in ("black", "crop", "blur")}
    centre = {fit: rgb(tile(fit), 80, 45) for fit in ("black", "crop", "blur")}
    assert left["black"] == (0, 0, 0)  # schwarze Ränder
    assert left["crop"][0] > 200  # füllt den Rahmen
    r, g, _b = left["blur"]
    assert 120 < r < left["crop"][0] and g < 60  # abgedunkelter Hintergrund aus dem Bild
    assert all(c[0] > 200 for c in centre.values())  # Bild selbst immer in der Mitte


def test_tile_follows_fit_change(window: MainWindow, synthetic: Path, umlaut_dir: Path) -> None:
    window.set_project(photos_project(synthetic, umlaut_dir), None)
    row = next(i for i, it in enumerate(window.project.items) if it.kind == "photo")
    assert window.model.tile(row).fit == "blur"
    window.timeline.select_rows([row])
    window.item_panel.fit.setCurrentIndex(window.item_panel.fit.findData("black"))
    assert window.model.tile(row).fit == "black"


def test_zoom_slider_and_ctrl_wheel(qtbot, window: MainWindow, synthetic: Path,  # noqa: ANN001
                                    umlaut_dir: Path) -> None:
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QWheelEvent

    from slideshow.app.timeline import ZOOM_MAX, ZOOM_MIN

    window.set_project(photos_project(synthetic, umlaut_dir), None)
    view = window.timeline
    model = window.model
    before = view.visualRect(model.index(0)).width()
    window.zoom.setValue(ZOOM_MAX)
    assert view.tile_width == ZOOM_MAX
    assert view.visualRect(model.index(0)).width() > before
    window.zoom.setValue(ZOOM_MIN)
    assert view.visualRect(model.index(0)).width() < before

    def wheel(delta: int) -> None:
        pos = QPointF(view.viewport().rect().center())
        ev = QWheelEvent(pos, view.viewport().mapToGlobal(pos), QPoint(0, 0), QPoint(0, delta),
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
                         Qt.ScrollPhase.NoScrollPhase, False)
        view.wheelEvent(ev)

    wheel(120)
    assert view.tile_width == ZOOM_MIN + 16 and window.zoom.value() == ZOOM_MIN + 16
    wheel(-120)
    wheel(-120)
    assert view.tile_width == ZOOM_MIN  # nicht kleiner als das Minimum


def test_status_texts_without_dashes(qtbot, window: MainWindow, synthetic: Path,  # noqa: ANN001
                                     umlaut_dir: Path) -> None:
    from slideshow.core.normalize import NormalizeProgress

    p = photos_project(synthetic, umlaut_dir)
    p.items[1].duration = 5 * 3600.0
    window.set_project(p, None)
    window.music_panel.add_tracks([synthetic / "Musik 1 – Frühling.mp3"])
    qtbot.waitUntil(lambda: "4 GB" in window.summary_label.text(), timeout=10_000)
    photos = [i for i, it in enumerate(window.project.items) if it.kind == "photo"]
    window.timeline.select_rows(photos[:2])
    texts = [window.summary_label.text(), window.music_panel.summary.text(),
             window.item_panel.header.text()]
    seen: list[str] = []
    window.video_prep.status.connect(lambda t, _f: seen.append(t))
    window.video_prep._total = 3
    window.video_prep._emit_status(umlaut_dir / "Clip.mp4", NormalizeProgress(5, 10, 300))
    texts += seen
    assert seen and "noch ca." in seen[0]
    for t in texts:
        # Musiktitel dürfen Gedankenstriche enthalten, die Meldung selbst nicht
        t = t.replace("Musik 1 – Frühling", "")
        assert "–" not in t and "—" not in t, t


# --- Beschädigte Dateien -------------------------------------------------------------


def damaged_files(folder: Path) -> dict[str, Path]:
    from test_damaged import garbage, make_photo, truncated

    good = make_photo(folder / "20260301_100000 gut.jpg")
    return {"good": good,
            "cut": truncated(good, folder / "20260302_100000 abgeschnitten Ö.jpg", 0.95),
            "garbage": garbage(folder / "20260303_100000 Datenmüll.jpg")}


def row_of(w: MainWindow, path: Path) -> int:
    return next(i for i, it in enumerate(w.project.items)
                if it.path and Path(it.path) == path.resolve())


def test_damaged_files_marked_and_named(qtbot, window: MainWindow,  # noqa: ANN001
                                        umlaut_dir: Path) -> None:
    files = damaged_files(umlaut_dir)
    wait_import(qtbot, window, [umlaut_dir])
    text = window.notice.label.text()
    assert window.notice.kind == "warning"
    assert "2 beschädigte Datei(en) sind in der Zeitleiste mit „beschädigt“ markiert" in text
    for key in ("cut", "garbage"):
        p = files[key]
        assert f"„{p.name}“ ({p})" in text  # Hinweis nennt Name und Pfad
    model = window.model
    flags = {key: model.tile(row_of(window, p)).damaged for key, p in files.items()}
    assert flags == {"good": False, "cut": True, "garbage": True}
    cut_row = row_of(window, files["cut"])
    tip = model.data(model.index(cut_row), Qt.ItemDataRole.ToolTipRole)
    assert "Beschädigt: Datei ist abgeschnitten" in tip and "übersprungen" in tip

    # Eigenschaften: Grund mit Pfad und „Trotzdem verwenden“
    panel = window.item_panel
    window.timeline.select_rows([cut_row])
    assert panel.damage_box.isVisibleTo(window)
    assert f"„{files['cut'].name}“ ({files['cut'].resolve()})" in panel.damage_label.text()
    assert panel.use_damaged.isVisibleTo(window) and not panel.use_damaged.isChecked()
    panel.use_damaged.setChecked(True)
    item = window.project.items[cut_row]
    assert item.use_damaged and not item.skip_damaged
    assert "trotzdem verwendet" in model.data(model.index(cut_row),
                                              Qt.ItemDataRole.ToolTipRole)
    assert window.model.tile(cut_row).damaged  # Markierung bleibt

    window.timeline.select_rows([row_of(window, files["garbage"])])
    assert panel.damage_box.isVisibleTo(window)
    assert not panel.use_damaged.isVisibleTo(window)  # nichts Lesbares: keine Option
    window.timeline.select_rows([row_of(window, files["good"])])
    assert not panel.damage_box.isVisibleTo(window)


def test_damaged_tile_shows_warning_badge(qtbot) -> None:  # noqa: ANN001
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QStyleOptionViewItem

    from slideshow.app.timeline import TileDelegate, TimelineModel, TimelineView

    p = Project(items=[Item("photo", path="kaputt.jpg", damage="Bilddaten sind fehlerhaft"),
                       Item("photo", path="gut.jpg")])
    model = TimelineModel(p, None)
    view = TimelineView()
    qtbot.addWidget(view)
    view.setModel(model)
    delegate = view.itemDelegate()
    assert isinstance(delegate, TileDelegate)

    def paint(row: int) -> QImage:
        idx = model.index(row)
        size = delegate.sizeHint(QStyleOptionViewItem(), idx)
        img = QImage(size, QImage.Format.Format_RGB32)
        img.fill(0)
        opt = QStyleOptionViewItem()
        view.initViewItemOption(opt)
        opt.rect = QRect(0, 0, size.width(), size.height())
        from PySide6.QtGui import QPainter

        painter = QPainter(img)
        delegate.paint(painter, opt, idx)
        painter.end()
        return img

    damaged, good = paint(0), paint(1)
    # Abzeichen oben links: dort unterscheiden sich die beiden Kacheln
    diff = sum(1 for x in range(8, 100) for y in range(8, 26)
               if damaged.pixel(x, y) != good.pixel(x, y))
    assert diff > 100


def test_export_skips_damaged_and_summarizes(qtbot, window: MainWindow,  # noqa: ANN001
                                             umlaut_dir: Path, no_dialogs: list[str]) -> None:
    files = damaged_files(umlaut_dir)
    wait_import(qtbot, window, [umlaut_dir])
    for it in window.project.items:
        it.duration = 1.0
    # Bestätigung vor dem Export nennt die Zahl der übersprungenen Dateien
    assert window.export_to(umlaut_dir / "x.mp4") is None
    assert any("2 beschädigte Datei(en) werden übersprungen" in t for t in no_dialogs)

    out = umlaut_dir / "Show Ä.mp4"
    assert window.export_to(out, confirm=False) is not None
    wait_render(qtbot, window)
    assert out.is_file()
    result = window.last_result
    assert result is not None and len(result.skipped) == 2
    text = window.notice.label.text()
    assert window.notice.kind == "warning" and "Fertig" in text
    assert "Übersprungen, weil beschädigt (2)" in text
    for key in ("cut", "garbage"):
        p = files[key].resolve()
        assert f"„{p.name}“ ({p})" in text


def test_export_marks_damage_found_late(qtbot, window: MainWindow,  # noqa: ANN001
                                        umlaut_dir: Path) -> None:
    """Projekt von früher, ohne Prüfung beim Import: Der Export findet den Schaden, die
    Zeitleiste zeigt ihn danach an und bietet „Trotzdem verwenden“ an."""
    files = damaged_files(umlaut_dir)
    p = Project(items=[Item("photo", path=str(files[k]), duration=1.0)
                       for k in ("good", "cut")])
    window.set_project(p, None)
    assert window.export_to(umlaut_dir / "alt.mp4", confirm=False) is not None
    wait_render(qtbot, window)
    item = window.project.items[1]
    assert item.damage is not None and item.damage_usable
    assert window.model.tile(1).damaged
    assert "„Trotzdem verwenden“ möglich" in window.notice.label.text()

