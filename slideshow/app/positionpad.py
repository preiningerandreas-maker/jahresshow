"""Kleines Feld im Seitenverhältnis des Bildes mit einem Kreuz: Klicken oder Ziehen setzt die
Textposition auf jeden beliebigen Punkt, auch in die Ecken. Die Pfeiltasten verschieben das
Kreuz in kleinen Schritten."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from slideshow.app.theme import color

STEP = 0.01  # Pfeiltaste
BIG_STEP = 0.05  # mit Umschalt
CROSS = 9  # halbe Länge der Kreuzarme in Pixeln
HEIGHT = 130  # Höhe des Widgets in Pixeln


class PositionPad(QWidget):
    """Punkt (x, y) in Anteilen des Bildes, 0..1. moved wird nur bei Änderung durch den
    Nutzer gesendet, nicht bei set_point."""

    moved = Signal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("positionPad")
        self.point: tuple[float, float] = (0.5, 0.5)
        self.free = False  # False: Voreinstellung (oben/Mitte/unten), Kreuz nur als Hinweis
        self._dragging = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Feste Höhe: das 16:9-Feld wird mittig hineingezeichnet, so bleibt der Platz im
        # Eigenschaftenbereich bei jeder Breite gleich
        self.setFixedHeight(HEIGHT)
        self.setMinimumWidth(HEIGHT * 16 // 9)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip("Position des Textes: ins Feld klicken oder das Kreuz ziehen. "
                        "Das Feld steht für das ganze Bild, der Text sitzt mit seiner Mitte "
                        "auf dem Kreuz und bleibt dabei im Bild.")

    def sizeHint(self) -> QSize:
        return QSize(HEIGHT * 16 // 9, HEIGHT)

    def set_point(self, x: float, y: float, free: bool) -> None:
        self.point = (min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0))
        self.free = free
        self.update()

    def _frame(self) -> QRectF:
        """Das Bildfeld: größtes 16:9-Rechteck in der Mitte des Widgets."""
        avail = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        w = min(avail.width(), avail.height() * 16 / 9)
        h = w * 9 / 16
        return QRectF(avail.x() + (avail.width() - w) / 2,
                      avail.y() + (avail.height() - h) / 2, w, h)

    def _set_from_pos(self, p: QPointF) -> None:
        f = self._frame()
        x = min(max((p.x() - f.x()) / f.width(), 0.0), 1.0)
        y = min(max((p.y() - f.y()) / f.height(), 0.0), 1.0)
        self._emit(x, y)

    def _emit(self, x: float, y: float) -> None:
        self.point = (x, y)
        self.free = True
        self.update()
        self.moved.emit(x, y)

    # --- Eingabe ----------------------------------------------------------------------

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.setFocus()
            self._set_from_pos(e.position())

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._dragging:
            self._set_from_pos(e.position())

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = False

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        step = BIG_STEP if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else STEP
        dx, dy = {Qt.Key.Key_Left: (-step, 0.0), Qt.Key.Key_Right: (step, 0.0),
                  Qt.Key.Key_Up: (0.0, -step), Qt.Key.Key_Down: (0.0, step)}.get(
            Qt.Key(e.key()), (None, None))
        if dx is None or dy is None:
            super().keyPressEvent(e)
            return
        x, y = self.point
        self._emit(min(max(x + dx, 0.0), 1.0), min(max(y + dy, 0.0), 1.0))

    # --- Zeichnen ---------------------------------------------------------------------

    def paintEvent(self, _e: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        f = self._frame()
        p.fillRect(f, color("input"))
        p.setPen(QPen(color("line"), 1))
        for i in (1, 2):  # Drittel als Anhaltspunkt
            p.drawLine(QPointF(f.x() + f.width() * i / 3, f.top()),
                       QPointF(f.x() + f.width() * i / 3, f.bottom()))
            p.drawLine(QPointF(f.left(), f.y() + f.height() * i / 3),
                       QPointF(f.right(), f.y() + f.height() * i / 3))
        p.setPen(QPen(color("muted"), 1))
        p.drawRect(f)
        cx = f.x() + self.point[0] * f.width()
        cy = f.y() + self.point[1] * f.height()
        # Voreinstellung: gedämpft (Hinweis), frei: kräftig
        c = QColor("#FFFFFF") if self.free else color("muted")
        p.setPen(QPen(c, 2))
        p.drawLine(QPointF(cx - CROSS, cy), QPointF(cx + CROSS, cy))
        p.drawLine(QPointF(cx, cy - CROSS), QPointF(cx, cy + CROSS))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), CROSS / 2, CROSS / 2)
        if self.hasFocus():
            p.setPen(QPen(color("accent"), 2))
            p.drawRect(f)
        p.end()
