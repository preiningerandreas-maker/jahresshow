"""Fortschritt von Export und Vorschau als eigenes gelbes Fenster mittig auf dem Bildschirm.
Es sperrt das Hauptfenster, bis das Video fertig ist oder abgebrochen wird. Gerendert wird
weiter im Hintergrund-Thread; das Hauptfenster zeichnet sich also weiter, nimmt aber keine
Eingaben an."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCloseEvent, QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from slideshow.app.activity import GAP, ProgressBar

WIDTH = 460  # logische Pixel; passt auch bei 1280×720 (150 %) bequem auf den Bildschirm


class RenderDialog(QDialog):
    def __init__(self, parent: QWidget) -> None:
        # Ohne Fenstertitel: Die gelbe Fläche ist das ganze Fenster
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("renderDialog")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFixedWidth(WIDTH)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        self.title = QLabel("")
        self.title.setObjectName("renderTitle")
        self.title.setWordWrap(True)
        self.status = QLabel("")
        self.status.setObjectName("renderStatus")
        self.status.setWordWrap(True)
        # Zwei Zeilen Platz, damit das Fenster bei längeren Texten nicht springt
        self.status.setMinimumHeight(self.status.fontMetrics().lineSpacing() * 2)
        self.status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.bar = ProgressBar()
        self.bar.setObjectName("renderBar")
        self.bar.setFixedWidth(WIDTH - 40 - GAP - self.bar.percent.width())
        self.bar.percent.setObjectName("renderPercent")
        row = QHBoxLayout()
        row.setSpacing(GAP)
        row.addWidget(self.bar)
        row.addWidget(self.bar.percent)
        self.cancel_button = QPushButton("Abbrechen")
        self.cancel_button.setObjectName("renderCancel")
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        lay.addWidget(self.title)
        lay.addWidget(self.status)
        lay.addLayout(row)
        lay.addSpacing(4)
        lay.addLayout(buttons)
        self.active = False
        self.fraction = 0.0
        self._cancel: Callable[[], None] | None = None
        self.cancel_button.clicked.connect(self.cancel)

    def start(self, title: str, cancel: Callable[[], None]) -> None:
        self._cancel = cancel
        self.active = True
        self.title.setText(title)
        self.cancel_button.setEnabled(True)
        self.update_progress("Wird vorbereitet …", 0.0)
        self.adjustSize()
        self.center_on_screen()
        self.show()
        self.raise_()
        self.activateWindow()

    def center_on_screen(self) -> None:
        parent = self.parentWidget()
        screen = (parent.screen() if parent is not None else self.screen()).availableGeometry()
        self.move(screen.center() - QPoint(self.width() // 2, self.height() // 2))

    def update_progress(self, text: str, fraction: float) -> None:
        if not self.active:
            return
        if self.cancel_button.isEnabled():  # nach „Abbrechen“ bleibt dieser Hinweis stehen
            self.status.setText(text)
        self.fraction = max(0.0, min(1.0, fraction))
        self.bar.set_fraction(self.fraction)

    def cancel(self) -> None:
        if self._cancel is not None and self.cancel_button.isEnabled():
            self.cancel_button.setEnabled(False)
            self.status.setText("Wird abgebrochen …")
            self._cancel()

    def finish(self) -> None:
        self._cancel = None
        self.active = False
        self.hide()

    # Escape soll einen langen Export nicht versehentlich beenden; Alt+F4 bricht ab wie
    # der Knopf
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            return
        super().keyPressEvent(event)

    def reject(self) -> None:
        if not self.active:
            super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.active:
            self.cancel()
            event.ignore()
            return
        super().closeEvent(event)
