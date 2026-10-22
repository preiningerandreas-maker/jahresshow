"""Standbild-Vorschau des markierten Elements, über dieselben Filter wie der Export
(Formatmodus, Text, Farben) in kleiner Auflösung. Nach jeder Änderung wird kurz gewartet,
ob noch mehr kommt (z. B. beim Tippen), dann im Hintergrund neu berechnet. Fertige Bilder
liegen im Cache, ein erneutes Anwählen zeigt sie sofort."""

from __future__ import annotations

import contextlib
import copy
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from slideshow.app.tasks import Task
from slideshow.core.normalize import cached_normalized
from slideshow.core.project import Item, Project
from slideshow.render.segment import render_still, still_key

STILL_W, STILL_H = 640, 360
DEBOUNCE_MS = 150
MAX_FILES = 300  # Standbilder im Cache, ältere werden entfernt


class StillPreview(QWidget):
    """Zeigt das Standbild im Seitenverhältnis 16:9."""

    updated = Signal()  # neues Bild wird angezeigt

    def __init__(self, cache_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cache_dir = cache_dir
        self.setObjectName("stillPreview")
        sp = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.setMinimumSize(160, 90)
        self.pixmap: QPixmap | None = None
        self.message = "Kein Element markiert"
        self.note = ""
        self.busy = False
        self.last_seconds: float | None = None  # Dauer der letzten Berechnung
        self._project: Project | None = None
        self._item: Item | None = None
        self._task: Task | None = None
        self._pending = False
        self._running = False
        self._gen = 0
        self._renders = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self._start)

    # --- Größe: immer 16:9 ---------------------------------------------------------

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return round(width * 9 / 16)

    def sizeHint(self) -> QSize:
        return QSize(320, 180)

    def resizeEvent(self, event: object) -> None:  # noqa: N802
        target = self.heightForWidth(self.width())
        if self.height() != target:
            self.setFixedHeight(target)

    # --- Inhalt -------------------------------------------------------------------

    def show_item(self, project: Project, item: Item | None) -> None:
        """Element anzeigen bzw. nach einer Änderung neu berechnen."""
        self._project = project
        self._item = item
        self._gen += 1
        if item is None:
            self._timer.stop()
            self.pixmap = None
            self.message = "Kein Element markiert"
            self.note = ""
            self.busy = False
            self.update()
            return
        self.busy = True
        self.update()
        self._timer.start()

    def refresh(self) -> None:
        """Erneut berechnen (z. B. nachdem ein Video umgewandelt wurde)."""
        if self._project is not None and self._item is not None:
            self.show_item(self._project, self._item)

    @property
    def pending(self) -> bool:
        return self._timer.isActive() or self._pending or self._running

    def _start(self) -> None:
        if self._running:
            self._pending = True  # danach mit dem neuesten Stand
            return
        self._pending = False
        project, item = self._project, self._item
        if project is None or item is None:
            return
        gen = self._gen
        # Eigene Kopie: der Nutzer kann weiter ändern, während gerechnet wird
        item = copy.deepcopy(item)
        snap = replace(project, items=[])
        out_dir = self.cache_dir / "stills"
        started = time.perf_counter()

        def run(task: Task) -> tuple[int, Path, str, float]:
            key = still_key(snap, item, STILL_W, STILL_H, self.cache_dir)
            out = out_dir / f"{key}.png"
            if not out.is_file():
                render_still(snap, item, STILL_W, STILL_H, out, self.cache_dir,
                             cancel=task.cancel_event)
                self._renders += 1
                if self._renders % 50 == 0:
                    _trim(out_dir)
            note = ""
            if item.kind == "video" and item.path and cached_normalized(
                    snap.resolve(item.path), self.cache_dir) is None:
                note = "Video noch nicht umgewandelt: Farben können abweichen"
            return gen, out, note, time.perf_counter() - started

        task = Task(run, "still", self)
        task.done.connect(self._on_done)
        task.failed.connect(lambda e, g=gen: self._on_failed(g, e))
        task.finished.connect(self._on_finished)
        self._running = True
        self._task = task.start()

    def _on_done(self, result: tuple[int, Path, str, float]) -> None:
        gen, out, note, seconds = result
        if gen != self._gen:
            return  # inzwischen geändert, das nächste Bild kommt gleich
        pm = QPixmap(str(out))
        if pm.isNull():
            self._on_failed(gen, OSError("Bild unlesbar"))
            return
        self.pixmap = pm
        self.message = ""
        self.note = note
        self.busy = False
        self.last_seconds = seconds
        self.update()
        self.updated.emit()

    def _on_failed(self, gen: int, e: object) -> None:
        if gen != self._gen:
            return
        self.pixmap = None
        self.busy = False
        self.message = "Vorschau nicht möglich"
        if isinstance(e, FileNotFoundError):
            self.message += ": Datei fehlt"
        self.note = ""
        self.update()

    def _on_finished(self) -> None:
        self._running = False
        if self._pending:  # während der Berechnung geändert
            self._start()

    def shutdown(self) -> None:
        self._timer.stop()
        if self._task is not None and self._task.running:
            self._task.cancel()
            self._task.wait(5)

    # --- Zeichnen -----------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, QColor("#202020"))
        if self.pixmap is not None:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawPixmap(r, self.pixmap)
        p.setPen(QColor("#FFFFFF"))
        if self.message:
            p.drawText(r, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
        info = "Wird aktualisiert …" if self.busy else self.note
        if info:
            fm = self.fontMetrics()
            band = QRect(0, r.height() - fm.height() - 6, r.width(), fm.height() + 6)
            p.fillRect(band, QColor(0, 0, 0, 160))
            p.drawText(band.adjusted(6, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter, info)
        p.end()


def _trim(folder: Path) -> None:
    """Nur die neuesten Standbilder behalten."""
    with contextlib.suppress(OSError):
        files = sorted(folder.glob("*.png"), key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files[MAX_FILES:]:
            f.unlink(missing_ok=True)
