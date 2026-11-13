"""Live-Vorschau für die Schnitt-Leiste: Bild beim Ziehen (Video) und Vorhören (Musik).

Die Daten (Bilderreihe, Klangbild) entstehen einmal im Hintergrund und liegen im Cache; beim
Ziehen wird nur noch gelesen. Hintergrundarbeit läuft wie bei den Zeitleisten-Vorschaubildern
in einfachen Threads, die ein Signal senden."""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from pathlib import Path

# Nur die schlanke Windows-Anbindung für Ton verwenden. Die FFmpeg-Anbindung von Qt würde
# zweite FFmpeg-Bibliotheken mitbringen, die mit denen des Programms kollidieren.
os.environ.setdefault("QT_MEDIA_BACKEND", "windows")

from PySide6.QtCore import (  # noqa: E402
    QBuffer,
    QByteArray,
    QIODevice,
    QObject,
    Qt,
    QTimer,
    Signal,
    SignalInstance,
)
from PySide6.QtGui import QImage, QPixmap  # noqa: E402
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices  # noqa: E402
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget  # noqa: E402

from slideshow.core.scrub import (  # noqa: E402
    PCM_RATE,
    SNIPPET_SECONDS,
    cached_frames,
    cached_pcm,
    exact_frame,
    frame_index,
    make_frames,
    make_pcm,
    pcm_slice,
)

log = logging.getLogger(__name__)

PREVIEW_W, PREVIEW_H = 256, 144
LISTEN_DELAY_MS = 70  # Ziehen: höchstens alle paar Millisekunden neu vorhören


def _emit(signal: SignalInstance, *args: object) -> None:
    """Aus einem Hintergrund-Thread senden; ist das Fenster schon geschlossen, ist es egal."""
    with contextlib.suppress(RuntimeError):
        signal.emit(*args)


class FramePreview(QLabel):
    """Zeigt das Bild an der Schieberposition. Während des Ziehens aus der Bilderreihe
    (sofort), nach dem Loslassen das genaue Bild."""

    framesReady = Signal(str)  # Pfad des Videos, für das die Bilderreihe fertig ist
    exactReady = Signal(str, float, QImage)

    def __init__(self, cache_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cache_dir = cache_dir
        self.setFixedSize(PREVIEW_W, PREVIEW_H)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setObjectName("scrubPreview")
        self.setStyleSheet("background:#111; color:#ccc;")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._src: Path | None = None
        self._frames: list[Path] = []
        self._images: dict[Path, QImage] = {}
        self._exact_token = 0
        self.framesReady.connect(self._on_frames)
        self.exactReady.connect(self._on_exact)
        self.set_source(None)

    def set_source(self, src: Path | None) -> None:
        """Video wählen; die Bilderreihe wird bei Bedarf im Hintergrund erzeugt."""
        self._src = src
        self._frames = []
        self._images.clear()
        self._exact_token += 1
        if src is None:
            self.clear()
            return
        try:
            done = cached_frames(src, self.cache_dir)
        except OSError:
            done = None
        if done is not None:
            self._frames = done
            self.setText("")
            return
        self.setText("Vorschau wird vorbereitet …")
        threading.Thread(target=self._build_frames, args=(src,), daemon=True,
                         name="scrub-frames").start()

    def _build_frames(self, src: Path) -> None:
        try:
            make_frames(src, self.cache_dir)
        except Exception:  # noqa: BLE001 - die Leiste funktioniert auch ohne Vorschau
            log.warning("Bilderreihe für %s nicht erzeugt", src, exc_info=True)
            return
        _emit(self.framesReady, str(src))

    def _on_frames(self, src: str) -> None:
        if self._src is None or str(self._src) != src:
            return
        done = cached_frames(self._src, self.cache_dir)
        if done:
            self._frames = done
            self.setText("")

    def show_at(self, t: float) -> None:
        """Bild an Sekunde t aus der Bilderreihe zeigen (sofort, aus dem Speicher)."""
        if not self._frames:
            return
        path = self._frames[frame_index(t, len(self._frames))]
        img = self._images.get(path)
        if img is None:
            img = QImage(str(path))
            if len(self._images) > 200:
                self._images.clear()
            self._images[path] = img
        self._show(img)

    def show_exact(self, t: float) -> None:
        """Nach dem Loslassen: das genaue Bild nachladen (ein einzelner Aufruf)."""
        if self._src is None:
            return
        self._exact_token += 1
        threading.Thread(target=self._build_exact, args=(self._src, t, self._exact_token),
                         daemon=True, name="scrub-exact").start()

    def _build_exact(self, src: Path, t: float, token: int) -> None:
        import tempfile

        try:
            with tempfile.TemporaryDirectory(prefix="flashback-scrub-",
                                             ignore_cleanup_errors=True) as tmp:
                out = Path(tmp) / "bild.jpg"
                exact_frame(src, t, out, self.cache_dir)
                img = QImage(str(out))
        except Exception:  # noqa: BLE001
            log.warning("Genaues Bild bei %.2f s nicht erzeugt", t, exc_info=True)
            return
        _emit(self.exactReady, f"{src}|{token}", t, img)

    def _on_exact(self, tag: str, _t: float, img: QImage) -> None:
        src, _, token = tag.rpartition("|")
        if self._src is None or str(self._src) != src or int(token) != self._exact_token:
            return  # inzwischen anderes Video oder neuer Zug am Schieber
        self._show(img)

    def _show(self, img: QImage) -> None:
        if img.isNull():
            return
        self.setPixmap(QPixmap.fromImage(img).scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation))


class AudioScrub(QObject):
    """Beim Ziehen ein kurzes Stück der Musik an der Schieberposition abspielen."""

    pcmReady = Signal(str)

    def __init__(self, cache_dir: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cache_dir = cache_dir
        self._src: Path | None = None
        self._pcm: Path | None = None
        self._sink: QAudioSink | None = None
        self._buffer: QBuffer | None = None
        self._pending: float | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(LISTEN_DELAY_MS)
        self._timer.timeout.connect(self._play_pending)
        self.pcmReady.connect(self._on_pcm)

    @staticmethod
    def available() -> bool:
        try:
            return not QMediaDevices.defaultAudioOutput().isNull()
        except Exception:  # noqa: BLE001
            return False

    @property
    def ready(self) -> bool:
        return self._pcm is not None

    def set_source(self, src: Path | None) -> None:
        self.stop()
        self._src = src
        self._pcm = None
        if src is None:
            return
        try:
            done = cached_pcm(src, self.cache_dir)
        except OSError:
            done = None
        if done is not None:
            self._pcm = done
            return
        threading.Thread(target=self._build_pcm, args=(src,), daemon=True,
                         name="scrub-audio").start()

    def _build_pcm(self, src: Path) -> None:
        try:
            make_pcm(src, self.cache_dir)
        except Exception:  # noqa: BLE001
            log.warning("Klangbild für %s nicht erzeugt", src, exc_info=True)
            return
        _emit(self.pcmReady, str(src))

    def _on_pcm(self, src: str) -> None:
        if self._src is not None and str(self._src) == src:
            self._pcm = cached_pcm(self._src, self.cache_dir)

    def listen_at(self, t: float) -> None:
        """Vorhören an Sekunde t. Schnelle Folgen werden zusammengefasst."""
        self._pending = t
        if not self._timer.isActive():
            self._timer.start()

    def _play_pending(self) -> None:
        t, self._pending = self._pending, None
        if t is None or self._pcm is None:
            return
        try:
            data = pcm_slice(self._pcm, t, SNIPPET_SECONDS)
        except OSError:
            return
        if data:
            self._play(data)

    def _play(self, data: bytes) -> None:
        if not self.available():
            return
        self.stop()
        fmt = QAudioFormat()
        fmt.setSampleRate(PCM_RATE)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        device = QMediaDevices.defaultAudioOutput()
        if not device.isFormatSupported(fmt):
            log.warning("Audiogerät nimmt 22,05 kHz mono nicht an")
            return
        buf = QBuffer(self)
        buf.setData(QByteArray(data))
        buf.open(QIODevice.OpenModeFlag.ReadOnly)
        sink = QAudioSink(device, fmt, self)
        sink.setVolume(0.8)
        sink.start(buf)
        self._sink, self._buffer = sink, buf

    def stop(self) -> None:
        self._timer.stop()
        self._pending = None
        if self._sink is not None:
            self._sink.stop()
            self._sink.deleteLater()
            self._sink = None
        if self._buffer is not None:
            self._buffer.close()
            self._buffer.deleteLater()
            self._buffer = None
