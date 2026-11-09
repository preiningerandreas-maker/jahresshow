"""Bildausschnitt eines Fotos festlegen: Rechteck über dem Foto, mit der Maus verschieben
und an Rändern und Ecken ziehen. Das Ergebnis ist ein fixer Ausschnitt (kein Zoom, kein
Schwenk): [links, oben, Breite, Höhe] in Anteilen des Bildes."""

from __future__ import annotations

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from slideshow.app.theme import color

HANDLE = 10  # Griffbreite in Pixeln (auch Trefferfläche)
MIN_FRACTION = 0.02  # kleinster Ausschnitt, Anteil der Bildseite
FULL = (0.0, 0.0, 1.0, 1.0)
EDGE_EPS = 0.001  # ab hier gilt der Ausschnitt als „ganzes Bild“

# Welche Kanten ein Griff bewegt: (links, oben, rechts, unten)
_GRIPS = {
    "tl": (1, 1, 0, 0), "t": (0, 1, 0, 0), "tr": (0, 1, 1, 0),
    "l": (1, 0, 0, 0), "r": (0, 0, 1, 0),
    "bl": (1, 0, 0, 1), "b": (0, 0, 0, 1), "br": (0, 0, 1, 1),
}
_CURSORS = {
    "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
    "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
    "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
    "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
}


def to_qimage(img: Image.Image) -> QImage:
    rgb = img.convert("RGB")
    data = rgb.tobytes()
    return QImage(data, rgb.width, rgb.height, rgb.width * 3,
                  QImage.Format.Format_RGB888).copy()


def clamp_rect(l: float, t: float, r: float, b: float,  # noqa: E741
               min_w: float = MIN_FRACTION, min_h: float = MIN_FRACTION
               ) -> tuple[float, float, float, float]:
    """Kanten ins Bild legen; Breite und Höhe nicht kleiner als das Minimum."""
    l, r = max(0.0, min(l, 1.0)), max(0.0, min(r, 1.0))  # noqa: E741
    t, b = max(0.0, min(t, 1.0)), max(0.0, min(b, 1.0))
    if r - l < min_w:
        if l + min_w <= 1.0:
            r = l + min_w
        else:
            l, r = 1.0 - min_w, 1.0  # noqa: E741
    if b - t < min_h:
        if t + min_h <= 1.0:
            b = t + min_h
        else:
            t, b = 1.0 - min_h, 1.0
    return l, t, r, b


class CropView(QWidget):
    """Foto mit abgedunkeltem Rand und verschiebbarem Auswahlrechteck."""

    changed = Signal()

    def __init__(self, image: QImage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.image = image
        self.setObjectName("cropView")
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(360, 240)
        self.rect_n: tuple[float, float, float, float] = FULL  # links, oben, rechts, unten
        self._drag: str | None = None
        self._start = QPointF()
        self._start_rect = FULL
        self._new = False  # gerade ein neues Rechteck aufziehen
        self._before_new = FULL

    # --- Ausschnitt -----------------------------------------------------------------

    def set_crop(self, crop: list[float] | None) -> None:
        if crop is None:
            self.rect_n = FULL
        else:
            x, y, w, h = crop
            self.rect_n = clamp_rect(x, y, x + w, y + h)
        self.update()

    def crop(self) -> list[float] | None:
        """[links, oben, Breite, Höhe]; None, wenn das ganze Bild gewählt ist."""
        l, t, r, b = self.rect_n  # noqa: E741
        if l <= EDGE_EPS and t <= EDGE_EPS and r >= 1 - EDGE_EPS and b >= 1 - EDGE_EPS:
            return None
        return [l, t, r - l, b - t]

    # --- Geometrie ------------------------------------------------------------------

    def image_rect(self) -> QRectF:
        """Wo das Foto im Widget liegt (so groß wie möglich, Seitenverhältnis bleibt)."""
        avail = QRectF(self.rect()).adjusted(HANDLE, HANDLE, -HANDLE, -HANDLE)
        iw, ih = max(1, self.image.width()), max(1, self.image.height())
        scale = min(avail.width() / iw, avail.height() / ih)
        w, h = iw * scale, ih * scale
        return QRectF(avail.x() + (avail.width() - w) / 2,
                      avail.y() + (avail.height() - h) / 2, w, h)

    def crop_rect(self) -> QRectF:
        """Auswahlrechteck in Widget-Koordinaten."""
        ir = self.image_rect()
        l, t, r, b = self.rect_n  # noqa: E741
        return QRectF(ir.x() + l * ir.width(), ir.y() + t * ir.height(),
                      (r - l) * ir.width(), (b - t) * ir.height())

    def _to_norm(self, p: QPointF) -> tuple[float, float]:
        ir = self.image_rect()
        return ((p.x() - ir.x()) / ir.width(), (p.y() - ir.y()) / ir.height())

    def grip_at(self, p: QPointF) -> str | None:
        """Griff unter dem Mauszeiger: Ecke/Rand, „move“ innen, sonst None."""
        r = self.crop_rect()
        h = min(HANDLE, r.width() / 3, r.height() / 3)
        near_l, near_r = abs(p.x() - r.left()) <= h, abs(p.x() - r.right()) <= h
        near_t, near_b = abs(p.y() - r.top()) <= h, abs(p.y() - r.bottom()) <= h
        in_x = r.left() - h <= p.x() <= r.right() + h
        in_y = r.top() - h <= p.y() <= r.bottom() + h
        if not (in_x and in_y):
            return None
        vertical = "t" if near_t else "b" if near_b else ""
        horizontal = "l" if near_l else "r" if near_r else ""
        key = vertical + horizontal
        if key in _GRIPS:
            return key
        return "move" if r.contains(p) else None

    # --- Maus -----------------------------------------------------------------------

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        p = e.position()
        grip = self.grip_at(p)
        if grip is None:
            # Neues Rechteck aufziehen, ab dem Klickpunkt
            x, y = self._to_norm(p)
            x, y = max(0.0, min(x, 1.0)), max(0.0, min(y, 1.0))
            self._before_new = self.rect_n
            self.rect_n = (x, y, x, y)
            grip = "br"
            self._start_rect = self.rect_n
            self._new = True
        else:
            self._new = False
            self._start_rect = self.rect_n
        self._drag = grip
        self._start = p

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        p = e.position()
        if self._drag is None:
            grip = self.grip_at(p)
            if grip == "move":
                self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            elif grip is not None:
                self.setCursor(QCursor(_CURSORS[grip]))
            else:
                self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
            return
        ir = self.image_rect()
        dx = (p.x() - self._start.x()) / ir.width()
        dy = (p.y() - self._start.y()) / ir.height()
        l, t, r, b = self._start_rect  # noqa: E741
        if self._drag == "move":
            w, h = r - l, b - t
            nl = max(0.0, min(l + dx, 1.0 - w))
            nt = max(0.0, min(t + dy, 1.0 - h))
            self.rect_n = (nl, nt, nl + w, nt + h)
        elif self._new:
            x, y = self._to_norm(p)
            x, y = max(0.0, min(x, 1.0)), max(0.0, min(y, 1.0))
            sx, sy = l, t
            # Erst beim Loslassen auf die Mindestgröße bringen: ein bloßer Klick soll den
            # bisherigen Ausschnitt nicht verändern
            self.rect_n = (min(sx, x), min(sy, y), max(sx, x), max(sy, y))
        else:
            gl, gt, gr, gb = _GRIPS[self._drag]
            nl, nt, nr, nb = l + gl * dx, t + gt * dy, r + gr * dx, b + gb * dy
            # Beim Ziehen darf eine Kante die gegenüberliegende nicht überholen
            if gl:
                nl = min(nl, r - MIN_FRACTION)
            if gr:
                nr = max(nr, l + MIN_FRACTION)
            if gt:
                nt = min(nt, b - MIN_FRACTION)
            if gb:
                nb = max(nb, t + MIN_FRACTION)
            self.rect_n = clamp_rect(nl, nt, nr, nb)
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self._drag is not None:
            self._drag = None
            l, t, r, b = self.rect_n  # noqa: E741
            if self._new and (r - l < MIN_FRACTION or b - t < MIN_FRACTION):
                self.rect_n = self._before_new  # nur geklickt, nicht gezogen: nichts ändern
            else:
                self.rect_n = clamp_rect(l, t, r, b)
            self._new = False
            self.update()
            self.changed.emit()

    # --- Zeichnen -------------------------------------------------------------------

    def paintEvent(self, _e: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), color("bg"))
        ir = self.image_rect()
        p.drawImage(ir, self.image)
        cr = self.crop_rect()
        # Außerhalb des Ausschnitts abdunkeln
        shade = QColor(0, 0, 0, 150)
        p.fillRect(QRectF(ir.left(), ir.top(), ir.width(), cr.top() - ir.top()), shade)
        p.fillRect(QRectF(ir.left(), cr.bottom(), ir.width(), ir.bottom() - cr.bottom()), shade)
        p.fillRect(QRectF(ir.left(), cr.top(), cr.left() - ir.left(), cr.height()), shade)
        p.fillRect(QRectF(cr.right(), cr.top(), ir.right() - cr.right(), cr.height()), shade)
        p.setPen(QPen(QColor("#FFFFFF"), 2))
        p.drawRect(cr)
        # Drittel-Raster als Anhaltspunkt
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        for i in (1, 2):
            x = cr.left() + cr.width() * i / 3
            y = cr.top() + cr.height() * i / 3
            p.drawLine(QPointF(x, cr.top()), QPointF(x, cr.bottom()))
            p.drawLine(QPointF(cr.left(), y), QPointF(cr.right(), y))
        # Griffe in den Ecken und Mitten der Kanten
        p.setPen(QPen(QColor("#1A1A1A"), 1))
        p.setBrush(QColor("#FFFFFF"))
        s = 8
        for x in (cr.left(), cr.center().x(), cr.right()):
            for y in (cr.top(), cr.center().y(), cr.bottom()):
                if x == cr.center().x() and y == cr.center().y():
                    continue
                p.drawRect(QRectF(x - s / 2, y - s / 2, s, s))
        p.end()


class CropDialog(QDialog):
    """Dialog „Bildausschnitt wählen“."""

    def __init__(self, image: Image.Image, crop: list[float] | None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bildausschnitt wählen")
        self.setObjectName("cropDialog")
        self.resize(900, 640)
        lay = QVBoxLayout(self)
        hint = QLabel("Ziehen Sie das Rechteck über den Teil des Fotos, der zu sehen sein soll. "
                      "Verschieben: in das Rechteck klicken und ziehen. Größe ändern: an den "
                      "Rändern und Ecken ziehen. Der Ausschnitt bleibt während der ganzen "
                      "Anzeige gleich.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.view = CropView(to_qimage(image))
        self.view.set_crop(crop)
        lay.addWidget(self.view, 1)
        self.info = QLabel("")
        self.info.setObjectName("cropInfo")
        lay.addWidget(self.info)
        buttons = QDialogButtonBox()
        self.reset_button = QPushButton("Ganzes Bild")
        self.reset_button.setToolTip("Den Ausschnitt entfernen: das ganze Foto zeigen")
        buttons.addButton(self.reset_button, QDialogButtonBox.ButtonRole.ResetRole)
        self.ok_button = buttons.addButton("Übernehmen", QDialogButtonBox.ButtonRole.AcceptRole)
        self.ok_button.setObjectName("primary")
        self.ok_button.setDefault(True)
        self.cancel_button = buttons.addButton("Abbrechen", QDialogButtonBox.ButtonRole.RejectRole)
        lay.addWidget(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.reset_button.clicked.connect(self._reset)
        self.view.changed.connect(self._update_info)
        self._update_info()

    def _reset(self) -> None:
        self.view.set_crop(None)
        self._update_info()

    def _update_info(self) -> None:
        crop = self.view.crop()
        if crop is None:
            self.info.setText("Ganzes Foto")
            return
        iw, ih = self.view.image.width(), self.view.image.height()
        self.info.setText(f"Ausschnitt: {round(crop[2] * 100)} % der Breite, "
                          f"{round(crop[3] * 100)} % der Höhe "
                          f"(Seitenverhältnis {crop[2] * iw / (crop[3] * ih):.2f} : 1)")

    def result_crop(self) -> list[float] | None:
        return self.view.crop()
