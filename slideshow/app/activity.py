"""Laufende Aufgaben (Einlesen, Videos umwandeln, Video erstellen) in einer einzigen Zeile,
die nur bei Aktivität erscheint. Aufgeklappt zeigt sie jede Aufgabe mit eigenem Fortschritt
und Abbrechen."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Eigenes Aussehen: Der Windows-11-Stil zeichnet Fortschritt nur als haarfeine Linie, die
# leicht als „leer“ übersehen wird. So ist der Balken in jedem Stil gut sichtbar.
BAR_STYLE = (
    "QProgressBar { border: 1px solid #8A8A8A; border-radius: 3px; background: #E6E6E6;"
    " color: #000000; text-align: center; min-height: 16px; }"
    "QProgressBar::chunk { background-color: #66BB6A; border-radius: 2px; }"
)


GAP = 6  # Abstand zwischen Text, Balken und Knöpfen


class ProgressBar(QProgressBar):
    """Fortschritt 0..1 mit Prozentzahl im Balken."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRange(0, 1000)
        self.setFormat("%p %")
        self.setTextVisible(True)
        self.setStyleSheet(BAR_STYLE)
        self.setFixedWidth(170)

    def set_fraction(self, fraction: float) -> None:
        self.setValue(round(max(0.0, min(1.0, fraction)) * 1000))


class TaskRow(QFrame):
    """Eine Aufgabe: Text, Fortschritt, Abbrechen. Sichtbar im aufgeklappten Bereich."""

    changed = Signal()

    def __init__(self, name: str, title: str, priority: int,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(name)
        self.title = title  # kurz, für die Zusammenfassung
        self.priority = priority  # höher = wichtiger für die Zusammenfassung
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 1, 6, 1)
        lay.setSpacing(GAP)
        self.label = QLabel("")
        self.label.setMinimumWidth(100)
        self.bar = ProgressBar()
        self.cancel_button = QPushButton("Abbrechen")
        lay.addWidget(self.label, 1)
        lay.addWidget(self.bar)
        lay.addWidget(self.cancel_button)
        # Platz unter dem Details-Knopf der Kopfzeile, damit alles bündig untereinander steht
        self.pad = QWidget()
        self.pad.setFixedWidth(0)
        lay.addWidget(self.pad)
        self.active = False
        self.fraction = 0.0
        self._cancel: Callable[[], None] | None = None
        self.cancel_button.clicked.connect(self.cancel)
        self.hide()

    @property
    def text(self) -> str:
        return self.label.text()

    def start(self, text: str, cancel: Callable[[], None] | None) -> None:
        self._cancel = cancel
        self.active = True
        self.cancel_button.setVisible(cancel is not None)
        self.cancel_button.setEnabled(True)
        self.update_progress(text, 0.0)

    def update_progress(self, text: str, fraction: float) -> None:
        if not self.active:
            return
        self.label.setText(text)
        self.label.setToolTip(text)
        self.fraction = max(0.0, min(1.0, fraction))
        self.bar.set_fraction(self.fraction)
        self.changed.emit()

    @property
    def can_cancel(self) -> bool:
        return self._cancel is not None and self.cancel_button.isEnabled()

    def cancel(self) -> None:
        if self._cancel is not None:
            self.cancel_button.setEnabled(False)
            self.label.setText("Wird abgebrochen …")
            self._cancel()
            self.changed.emit()

    def finish(self) -> None:
        self._cancel = None
        self.active = False
        self.hide()
        self.changed.emit()


class ActivityPanel(QFrame):
    """Eine Zeile für alle laufenden Aufgaben. Die wichtigste steht vorn, die übrigen
    werden genannt; über „Details“ lassen sich alle einzeln ansehen und abbrechen."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("activityPanel")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(6, 2, 6, 2)
        head.setSpacing(GAP)
        self.summary = QLabel("")
        self.summary.setMinimumWidth(100)
        self.bar = ProgressBar()
        self.cancel_button = QPushButton("Abbrechen")
        self.toggle = QPushButton("Details (3)")
        self.toggle.setCheckable(True)
        self.toggle.setFixedWidth(self.toggle.sizeHint().width())
        self.toggle.setToolTip("Alle laufenden Aufgaben einzeln anzeigen")
        head.addWidget(self.summary, 1)
        head.addWidget(self.bar)
        head.addWidget(self.cancel_button)
        head.addWidget(self.toggle)
        outer.addLayout(head)
        self.details = QWidget()
        self.details_layout = QVBoxLayout(self.details)
        self.details_layout.setContentsMargins(0, 0, 0, 2)
        self.details_layout.setSpacing(0)
        self.details.hide()
        outer.addWidget(self.details)
        self.rows: list[TaskRow] = []
        self.toggle.toggled.connect(self._refresh)
        self.cancel_button.clicked.connect(self._cancel_primary)
        self.hide()

    def add_row(self, row: TaskRow) -> TaskRow:
        row.pad.setFixedWidth(self.toggle.width())  # Abstand davor setzt das Layout
        self.rows.append(row)
        self.details_layout.addWidget(row)
        row.changed.connect(self._refresh)
        return row

    def active_rows(self) -> list[TaskRow]:
        return sorted((r for r in self.rows if r.active), key=lambda r: -r.priority)

    @property
    def primary(self) -> TaskRow | None:
        rows = self.active_rows()
        return rows[0] if rows else None

    def expanded(self) -> bool:
        return self.toggle.isChecked()

    def set_expanded(self, on: bool) -> None:
        self.toggle.setChecked(on)

    def _cancel_primary(self) -> None:
        row = self.primary
        if row is not None:
            row.cancel()

    def _refresh(self) -> None:
        rows = self.active_rows()
        if not rows:
            self.hide()
            self.toggle.setChecked(False)
            return
        first = rows[0]
        text = first.text
        if len(rows) > 1:
            others = ", ".join(r.title for r in rows[1:])
            text += f" (außerdem: {others})"
        self.summary.setText(text)
        self.summary.setToolTip(text)
        self.bar.set_fraction(first.fraction)
        self.cancel_button.setVisible(first.can_cancel)
        self.toggle.setVisible(len(rows) > 1)
        self.toggle.setText(f"Details ({len(rows)})")
        expanded = self.toggle.isChecked() and len(rows) > 1
        self.details.setVisible(expanded)
        for r in self.rows:
            r.setVisible(expanded and r.active)
        self.show()
