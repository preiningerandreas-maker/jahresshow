"""Musik-Playlist aus lokalen Dateien: Reihenfolge, Lautstärke, Absenken bei Originalton,
Ausblenden am Ende."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from slideshow.core.ffmpeg import FFmpegError, probe
from slideshow.core.importer import MUSIC_EXTS
from slideshow.core.project import Project


def audio_length(path: Path) -> float | None:
    try:
        return float(probe(path).get("format", {}).get("duration") or 0) or None
    except (FFmpegError, OSError, ValueError):
        return None


def format_minutes(seconds: float) -> str:
    m, s = divmod(round(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class MusicPanel(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project: Project | None = None
        self._lengths: dict[Path, float | None] = {}
        self._show_seconds: float | None = None
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QGroupBox("Musik (wird nacheinander abgespielt)")
        lay = QVBoxLayout(box)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setMinimumHeight(90)
        self.list.model().rowsMoved.connect(self._sync_from_list)
        lay.addWidget(self.list, 1)
        buttons = QGridLayout()
        self.add_button = QPushButton("Musik hinzufügen …")
        self.remove_button = QPushButton("Entfernen")
        self.up_button = QPushButton("Nach oben")
        self.down_button = QPushButton("Nach unten")
        buttons.addWidget(self.add_button, 0, 0)
        buttons.addWidget(self.remove_button, 0, 1)
        buttons.addWidget(self.up_button, 1, 0)
        buttons.addWidget(self.down_button, 1, 1)
        lay.addLayout(buttons)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        outer.addWidget(box, 1)

        sbox = QGroupBox("Lautstärke")
        form = QFormLayout(sbox)
        self.volume = QSpinBox()
        self.volume.setRange(0, 200)
        self.volume.setSuffix(" %")
        self.volume.setKeyboardTracking(False)
        form.addRow("Musik", self.volume)
        self.duck = QSpinBox()
        self.duck.setRange(0, 100)
        self.duck.setSuffix(" %")
        self.duck.setKeyboardTracking(False)
        self.duck.setToolTip("Lautstärke der Musik, solange ein Video mit Originalton läuft")
        form.addRow("Bei Videos mit Ton", self.duck)
        self.fade = QSpinBox()
        self.fade.setRange(0, 30)
        self.fade.setSuffix(" s")
        self.fade.setKeyboardTracking(False)
        self.fade.setToolTip("Die Musik wird am Ende der Show langsam leiser")
        form.addRow("Ausblenden am Ende", self.fade)
        outer.addWidget(sbox)

        self.add_button.clicked.connect(self._choose)
        self.remove_button.clicked.connect(self.remove_selected)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        self.volume.valueChanged.connect(lambda v: self._set("volume", v / 100))
        self.duck.valueChanged.connect(lambda v: self._set("duck_volume", v / 100))
        self.fade.valueChanged.connect(lambda v: self._set("fade_out", float(v)))

    def set_project(self, project: Project) -> None:
        self.project = project
        self._loading = True
        try:
            m = project.music
            self.volume.setValue(round(m.volume * 100))
            self.duck.setValue(round(m.duck_volume * 100))
            self.fade.setValue(round(m.fade_out))
            self._fill()
        finally:
            self._loading = False

    def _fill(self) -> None:
        assert self.project is not None
        self.list.clear()
        for t in self.project.music.tracks:
            path = self.project.resolve(t)
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, t)
            item.setToolTip(str(path) if path.is_file() else f"{path}\n(Datei fehlt!)")
            if not path.is_file():
                item.setText(f"{path.name} (fehlt)")
            self.list.addItem(item)
        self._update_summary()

    def _set(self, field: str, value: float) -> None:
        if self._loading or self.project is None:
            return
        setattr(self.project.music, field, value)
        self.changed.emit()

    def _choose(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(MUSIC_EXTS))
        files, _ = QFileDialog.getOpenFileNames(self, "Musik hinzufügen", "",
                                                f"Musik ({exts})")
        if files:
            self.add_tracks([Path(f) for f in files])

    def add_tracks(self, files: list[Path]) -> None:
        if self.project is None:
            return
        for f in files:
            if f.suffix.lower() in MUSIC_EXTS:
                self.project.music.tracks.append(str(f.resolve()))
        self._fill()
        self.changed.emit()

    def remove_selected(self) -> None:
        if self.project is None:
            return
        rows = sorted({self.list.row(i) for i in self.list.selectedItems()}, reverse=True)
        for r in rows:
            del self.project.music.tracks[r]
        if rows:
            self._fill()
            self.changed.emit()

    def _move(self, delta: int) -> None:
        if self.project is None:
            return
        row = self.list.currentRow()
        new = row + delta
        tracks = self.project.music.tracks
        if row < 0 or not 0 <= new < len(tracks):
            return
        tracks[row], tracks[new] = tracks[new], tracks[row]
        self._fill()
        self.list.setCurrentRow(new)
        self.changed.emit()

    def _sync_from_list(self) -> None:
        if self.project is None:
            return
        self.project.music.tracks = [str(self.list.item(i).data(Qt.ItemDataRole.UserRole))
                                     for i in range(self.list.count())]
        self._update_summary()
        self.changed.emit()

    def set_show_length(self, seconds: float | None) -> None:
        self._show_seconds = seconds
        self._update_summary()

    def _update_summary(self) -> None:
        if self.project is None or not self.project.music.tracks:
            self.summary.setText("Noch keine Musik gewählt. Die Show ist dann still, nur "
                                 "Videos mit Ton sind zu hören.")
            return
        total = 0.0
        for t in self.project.music.tracks:
            p = self.project.resolve(t)
            if p not in self._lengths:
                self._lengths[p] = audio_length(p) if p.is_file() else None
            total += self._lengths[p] or 0.0
        text = f"Musik insgesamt {format_minutes(total)}"
        if self._show_seconds:
            text += f", Show {format_minutes(self._show_seconds)}"
            if total and total < self._show_seconds:
                text += " – die Musik wird wiederholt."
            elif total:
                text += " – die Musik wird am Ende ausgeblendet."
        self.summary.setText(text)
