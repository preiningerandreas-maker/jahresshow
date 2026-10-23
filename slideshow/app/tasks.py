"""Hintergrundarbeit für die Oberfläche: eine Aufgabe läuft in einem eigenen Thread und meldet
Fortschritt und Ergebnis per Qt-Signal. Die Signale kommen im Hauptthread an, die Oberfläche
bleibt dabei bedienbar."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from slideshow.core.ffmpeg import Cancelled, FFmpegError
from slideshow.core.importer import describe_file
from slideshow.core.normalize import BackgroundNormalizer, NormalizeProgress, cached_normalized
from slideshow.render.encoder import remember_failure

log = logging.getLogger(__name__)


class Task(QObject):
    """fn(task) läuft im Hintergrund; über task.report() meldet sie Fortschritt, über
    task.cancel_event erfährt sie vom Abbrechen (dann Cancelled auslösen)."""

    progress = Signal(str, float)
    done = Signal(object)
    failed = Signal(object)  # Exception
    cancelled = Signal()
    finished = Signal()  # immer zuletzt, egal wie die Aufgabe endet

    def __init__(self, fn: Callable[[Task], Any], name: str = "task",
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._fn = fn
        self._name = name
        self.cancel_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> Task:
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()
        return self

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def cancel(self) -> None:
        self.cancel_event.set()

    def report(self, message: str, fraction: float) -> None:
        self.progress.emit(message, fraction)

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        try:
            result = self._fn(self)
        except Cancelled:
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001 - jeder Fehler wird der Oberfläche gemeldet
            log.exception("Hintergrundaufgabe %s fehlgeschlagen", self._name)
            self.failed.emit(e)
        else:
            if self.cancel_event.is_set():
                self.cancelled.emit()
            else:
                self.done.emit(result)
        finally:
            self.finished.emit()


class VideoPrep(QObject):
    """Wandelt importierte Videos im Hintergrund ins Arbeitsformat um und meldet den
    Fortschritt in einfacher Sprache. Fertige Dateien bleiben dauerhaft im Cache."""

    status = Signal(str, float)  # Text, Fortschritt 0..1
    idle = Signal()  # alles umgewandelt
    converted = Signal(object)  # Path des Originals
    error = Signal(object, str)  # Path, Meldung

    def __init__(self, cache_dir: Path, encoder: Callable[[], str],
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cache_dir = cache_dir
        self._encoder = encoder
        self._normalizer: BackgroundNormalizer | None = None
        self._lock = threading.Lock()
        self._pending: list[Path] = []
        self._known: set[Path] = set()
        self._done = 0
        self._total = 0
        self._waiting: list[Path] = []
        self._starting = False
        self._closed = False

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    def submit(self, paths: list[Path]) -> int:
        """Videos einreihen, die noch nicht umgewandelt sind. Liefert die Anzahl neuer."""
        fresh = []
        with self._lock:
            if not self._pending:  # neue Runde: Zähler zurücksetzen
                self._done = 0
                self._total = 0
            for p in paths:
                if p in self._known or not p.is_file():
                    continue
                if cached_normalized(p, self.cache_dir) is not None:
                    continue
                self._known.add(p)
                self._pending.append(p)
                fresh.append(p)
            if not fresh:
                return 0
            self._total += len(fresh)
            normalizer = self._normalizer
            if normalizer is None:
                self._waiting.extend(fresh)
                start = not self._starting
                self._starting = True
        if normalizer is not None:
            normalizer.submit(fresh)
        elif start:
            # Kodierer im Hintergrund ermitteln (Probe-Encode dauert, Ergebnis ist gecacht)
            threading.Thread(target=self._start_normalizer, name="video-prep",
                             daemon=True).start()
        self._emit_status(None, None)
        return len(fresh)

    def _start_normalizer(self) -> None:
        try:
            encoder = self._encoder()
        except Exception:  # noqa: BLE001 - Prozessor funktioniert immer
            encoder = "libx264"
        normalizer = BackgroundNormalizer(
            self.cache_dir, encoder=encoder, on_progress=self._on_progress,
            on_done=self._on_done, on_error=self._on_error,
            on_encoder_failure=self._on_encoder_failure)
        with self._lock:
            if self._closed:
                waiting: list[Path] = []
            else:
                self._normalizer = normalizer
                waiting, self._waiting = self._waiting, []
        if self._closed:
            normalizer.close()
            return
        normalizer.submit(waiting)

    def _label(self, src: Path | None) -> str:
        with self._lock:
            n = min(self._done + 1, self._total)
            total = self._total
        name = f" („{src.name}“)" if src is not None else ""
        return f"Videos werden für die Show umgewandelt: {n} von {total}{name}"

    def _emit_status(self, src: Path | None, p: NormalizeProgress | None) -> None:
        text = self._label(src)
        frac = 0.0
        if p is not None:
            if p.eta is not None:
                minutes = max(1, round(p.eta / 60))
                text += f", dieses Video noch ca. {minutes} min"
            frac = p.fraction
        with self._lock:
            overall = (self._done + frac) / self._total if self._total else 1.0
        self.status.emit(text, overall)

    def _on_progress(self, src: Path, p: NormalizeProgress) -> None:
        self._emit_status(src, p)

    def _finish(self, src: Path) -> None:
        with self._lock:
            if src in self._pending:
                self._pending.remove(src)
            self._done += 1
            empty = not self._pending
        if empty:
            self.idle.emit()

    def _on_done(self, src: Path, _out: Path) -> None:
        self._finish(src)
        self.converted.emit(src)

    def _on_error(self, src: Path, e: Exception) -> None:
        with self._lock:
            self._known.discard(src)  # später erneut versuchen (z. B. beim Export)
        self.error.emit(src, f"{describe_file(src)} konnte nicht umgewandelt werden: {e}")
        self._finish(src)

    def _on_encoder_failure(self, encoder: str, src: Path, err: FFmpegError) -> None:
        """Grafikkarte hat versagt, der Prozessor hat das Video umgewandelt und macht mit den
        übrigen weiter. Für dieses Gerät merken, damit auch der Export gleich den Prozessor
        nimmt."""
        log.warning("%s hat beim Umwandeln von %s versagt, weiter mit dem Prozessor: %s",
                    encoder, describe_file(src), err)
        try:
            remember_failure(self.cache_dir, encoder)
        except OSError as e:
            log.warning("Panne der Grafikkarte konnte nicht gespeichert werden: %s", e)

    def shutdown(self) -> None:
        """Laufende Umwandlung abbrechen; fertige Dateien bleiben im Cache."""
        with self._lock:
            self._closed = True
            normalizer, self._normalizer = self._normalizer, None
        if normalizer is not None:
            normalizer.cancel()
            normalizer.close()
