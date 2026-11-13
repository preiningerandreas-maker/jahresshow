"""Schnitt-Leiste: ein Balken mit zwei Schiebern (Anfang und Ende), für Video und Musik.

Die Leiste kennt nur Sekunden. Beim Ziehen meldet sie laufend `changing` (Live-Bild bzw.
Vorhören, Sekundenfelder mitführen), beim Loslassen einmal `committed` (ins Projekt schreiben).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

START, END = "start", "end"
HANDLE_W = 12.0
MARGIN = HANDLE_W / 2 + 2
KEY_STEP = 0.5  # Sekunden pro Pfeiltaste


class TrimBar(QWidget):
    changing = Signal(str, float, float)  # welcher Schieber ("start"/"end"), Anfang, Ende
    committed = Signal(float, float)  # Anfang, Ende (nach dem Loslassen oder Pfeiltaste)

    def __init__(self, min_gap: float = 0.5, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.min_gap = min_gap
        self._length = 0.0
        self._start = 0.0
        self._end = 0.0
        self._drag: str | None = None
        self._focus_handle = START
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(False)
        self.setAccessibleName("Schnitt-Leiste")

    # --- Werte -----------------------------------------------------------------------

    @property
    def length(self) -> float:
        return self._length

    @property
    def start(self) -> float:
        return self._start

    @property
    def end(self) -> float:
        return self._end

    def set_length(self, length: float) -> None:
        self._length = max(length, 0.0)
        self._start = min(self._start, self._length)
        self._end = self._length if self._end <= 0 else min(self._end, self._length)
        self.update()

    def set_values(self, start: float, end: float) -> None:
        """Von außen setzen (Sekundenfelder, Laden); meldet nichts."""
        if self._drag is not None:
            return  # während des Ziehens bestimmt die Maus
        self._start = min(max(start, 0.0), self._length)
        self._end = self._length if end <= 0 else min(max(end, self._start), self._length)
        self.update()

    # --- Umrechnung ------------------------------------------------------------------

    def _span(self) -> float:
        return max(self.width() - 2 * MARGIN, 1.0)

    def _x(self, t: float) -> float:
        if self._length <= 0:
            return MARGIN
        return MARGIN + t / self._length * self._span()

    def _t(self, x: float) -> float:
        if self._length <= 0:
            return 0.0
        return min(max((x - MARGIN) / self._span() * self._length, 0.0), self._length)

    def clamp(self, which: str, t: float) -> float:
        """Schieberposition innerhalb der Grenzen (Mindestabstand, Länge)."""
        gap = min(self.min_gap, self._length)
        if which == START:
            return min(max(t, 0.0), max(self._end - gap, 0.0))
        return max(min(t, self._length), min(self._start + gap, self._length))

    # --- Zeichnen --------------------------------------------------------------------

    def paintEvent(self, _event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        mid = self.height() / 2
        groove = QRectF(MARGIN, mid - 4, self._span(), 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(pal.mid())
        p.drawRoundedRect(groove, 4, 4)
        if self._length > 0:
            x1, x2 = self._x(self._start), self._x(self._end)
            p.setBrush(pal.highlight())
            p.drawRoundedRect(QRectF(x1, mid - 4, max(x2 - x1, 0.0), 8), 4, 4)
            for which, t in ((START, self._start), (END, self._end)):
                self._draw_handle(p, self._x(t), mid, which)
        p.end()

    def _draw_handle(self, p: QPainter, x: float, mid: float, which: str) -> None:
        rect = QRectF(x - HANDLE_W / 2, mid - 14, HANDLE_W, 28)
        active = which == self._drag or (self.hasFocus() and which == self._focus_handle)
        p.setPen(QPen(QColor("#202020"), 1.5 if active else 1.0))
        p.setBrush(QColor("#FFD24A") if active else QColor("#FFFFFF"))
        p.drawRoundedRect(rect, 3, 3)
        c = rect.center()
        p.setPen(QPen(QColor("#202020"), 1))
        p.drawLine(QPointF(c.x(), c.y() - 6), QPointF(c.x(), c.y() + 6))

    # --- Maus und Tastatur -----------------------------------------------------------

    def _nearest(self, x: float) -> str:
        d_start, d_end = abs(x - self._x(self._start)), abs(x - self._x(self._end))
        if d_start == d_end:  # beide übereinander: je nach Richtung der Maus
            return START if x < self._x(self._start) else END
        return START if d_start < d_end else END

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._length <= 0:
            return
        self._drag = self._nearest(event.position().x())
        self._focus_handle = self._drag
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self._move_to(event.position().x())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag is not None:
            self._move_to(event.position().x())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._drag is None:
            return
        self._move_to(event.position().x())
        self._drag = None
        self.update()
        self.committed.emit(self._start, self._end)

    def _move_to(self, x: float) -> None:
        which = self._drag
        if which is None:
            return
        t = self.clamp(which, self._t(x))
        if which == START:
            self._start = t
        else:
            self._end = t
        self.update()
        self.changing.emit(which, self._start, self._end)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Tab or key == Qt.Key.Key_Backtab:
            event.ignore()
            return
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._focus_handle = START if key == Qt.Key.Key_Up else END
            self.update()
            return
        if key not in (Qt.Key.Key_Left, Qt.Key.Key_Right) or self._length <= 0:
            super().keyPressEvent(event)
            return
        step = KEY_STEP if key == Qt.Key.Key_Right else -KEY_STEP
        which = self._focus_handle
        t = self.clamp(which, (self._start if which == START else self._end) + step)
        if which == START:
            self._start = t
        else:
            self._end = t
        self.update()
        self.changing.emit(which, self._start, self._end)
        self.committed.emit(self._start, self._end)

    def focusInEvent(self, event) -> None:  # noqa: ANN001
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:  # noqa: ANN001
        super().focusOutEvent(event)
        self.update()
