"""Musik-Playlist aus lokalen Dateien: Reihenfolge, Lautstärke, Absenken bei Originalton,
Ausblenden am Ende."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
    QFileDialog,
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

from slideshow.app.properties import form_layout
from slideshow.app.scrubwidgets import AudioScrub
from slideshow.app.trimbar import START, TrimBar
from slideshow.core.ffmpeg import FFmpegError, probe
from slideshow.core.importer import MUSIC_EXTS
from slideshow.core.project import Project


def audio_length(path: Path) -> float | None:
    try:
        return float(probe(path).get("format", {}).get("duration") or 0) or None
    except (FFmpegError, OSError, ValueError):
        return None


MIN_TRIM = 0.5  # kürzester Ausschnitt in Sekunden


def trim_text(start: float, end: float | None) -> str:
    """„0:10 bis 2:30“, „ab 0:10“ oder „bis 2:30“."""
    if end is None:
        return f"ab {format_minutes(start)}"
    if start <= 0:
        return f"bis {format_minutes(end)}"
    return f"{format_minutes(start)} bis {format_minutes(end)}"


def format_minutes(seconds: float) -> str:
    m, s = divmod(round(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class MusicPanel(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None, cache_dir: Path | None = None) -> None:
        super().__init__(parent)
        self.project: Project | None = None
        self._bar_src: Path | None = None
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

        tbox = QGroupBox("Ausschnitt des markierten Stücks")
        tform = form_layout(tbox)
        # Schnitt-Leiste: beim Ziehen hört man die Stelle des Schiebers
        self.trim_bar = TrimBar(MIN_TRIM)
        self.trim_bar.setToolTip("Anfang und Ende des Stücks ziehen. Beim Ziehen hören Sie "
                                 "die Stelle des Schiebers. Pfeiltasten verschieben um 0,5 s "
                                 "(Pfeil hoch/runter wechselt den Schieber).")
        tform.addRow(self.trim_bar)
        self.listen = AudioScrub(cache_dir, self) if cache_dir is not None else None
        self.trim_start = QDoubleSpinBox()
        self.trim_start.setDecimals(1)
        self.trim_start.setSingleStep(1.0)
        self.trim_start.setSuffix(" s")
        self.trim_start.setKeyboardTracking(False)
        self.trim_start.setToolTip("Das Stück beginnt erst an dieser Stelle (in Sekunden)")
        tform.addRow("Start", self.trim_start)
        self.trim_end = QDoubleSpinBox()
        self.trim_end.setDecimals(1)
        self.trim_end.setSingleStep(1.0)
        self.trim_end.setSuffix(" s")
        self.trim_end.setSpecialValueText("Ende der Datei")
        self.trim_end.setKeyboardTracking(False)
        self.trim_end.setToolTip("Das Stück endet an dieser Stelle (in Sekunden), "
                                 "0 = bis zum Ende der Datei")
        tform.addRow("Ende", self.trim_end)
        self.trim_reset = QPushButton("Ganzes Stück")
        tform.addRow(self.trim_reset)
        self.trim_box = tbox
        outer.addWidget(tbox)

        sbox = QGroupBox("Lautstärke")
        form = form_layout(sbox)
        self.volume = QSpinBox()
        self.volume.setRange(0, 200)
        self.volume.setSuffix(" %")
        self.volume.setKeyboardTracking(False)
        form.addRow("Musik", self.volume)
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
        self.fade.valueChanged.connect(lambda v: self._set("fade_out", float(v)))
        self.list.itemSelectionChanged.connect(self._load_trim)
        self.trim_start.valueChanged.connect(self._on_trim)
        self.trim_end.valueChanged.connect(self._on_trim)
        self.trim_bar.changing.connect(self._on_bar_changing)
        self.trim_bar.committed.connect(self._on_bar_committed)
        self.trim_reset.clicked.connect(self._reset_trim)
        self._load_trim()

    def set_project(self, project: Project) -> None:
        self.project = project
        self._loading = True
        try:
            m = project.music
            self.volume.setValue(round(m.volume * 100))
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
            start, end = self.project.music.trim_of(t)
            if (start, end) != (0.0, None):
                item.setText(f"{item.text()}  [{trim_text(start, end)}]")
            self.list.addItem(item)
        self._update_summary()
        self._load_trim()

    def _selected_track(self) -> str | None:
        """Das eine markierte Stück; bei keinem oder mehreren None."""
        rows = {self.list.row(i) for i in self.list.selectedItems()}
        if self.project is None or len(rows) != 1:
            return None
        row = next(iter(rows))
        tracks = self.project.music.tracks
        return tracks[row] if 0 <= row < len(tracks) else None

    def _track_length(self, track: str) -> float | None:
        assert self.project is not None
        p = self.project.resolve(track)
        if p not in self._lengths:
            self._lengths[p] = audio_length(p) if p.is_file() else None
        return self._lengths[p]

    def _load_trim(self) -> None:
        track = self._selected_track()
        self.trim_box.setEnabled(track is not None)
        was = self._loading
        self._loading = True
        try:
            if track is None or self.project is None:
                self.trim_start.setValue(0.0)
                self.trim_end.setValue(0.0)
                self.trim_bar.setVisible(False)
                if self.listen is not None:
                    self.listen.set_source(None)
                    self._bar_src = None
                return
            length = self._track_length(track)
            top = length if length else 36000.0
            self.trim_start.setRange(0.0, top)
            self.trim_end.setRange(0.0, top)
            start, end = self.project.music.trim_of(track)
            self.trim_start.setValue(start)
            self.trim_end.setValue(end or 0.0)
            self.trim_bar.setVisible(bool(length))
            if length:
                self.trim_bar.set_length(length)
                self.trim_bar.set_values(start, end or 0.0)
            if self.listen is not None:
                src = self.project.resolve(track)
                if src != self._bar_src:
                    self._bar_src = src
                    self.listen.set_source(src if length else None)
        finally:
            self._loading = was

    def _on_bar_changing(self, which: str, start: float, end: float) -> None:
        """Schieber wird gezogen: Sekundenfelder mitführen und die Stelle vorhören."""
        was = self._loading
        self._loading = True
        try:
            self.trim_start.setValue(start)
            self.trim_end.setValue(0.0 if end >= self.trim_bar.length - 0.05 else end)
        finally:
            self._loading = was
        if self.listen is not None:
            self.listen.listen_at(start if which == START else max(end - 1.0, 0.0))

    def _on_bar_committed(self, start: float, end: float) -> None:
        if self.listen is not None:
            self.listen.stop()
        self._apply_trim(start, 0.0 if end >= self.trim_bar.length - 0.05 else end)

    def _on_trim(self) -> None:
        if self._loading:
            return
        self._apply_trim(self.trim_start.value(), self.trim_end.value())

    def _apply_trim(self, start: float, end: float) -> None:
        track = self._selected_track()
        if self._loading or track is None or self.project is None:
            return
        length = self._track_length(track)
        if end > 0 and end <= start:
            end = min(start + MIN_TRIM, length) if length else start + MIN_TRIM
        if length and end >= length - 0.05:
            end = 0.0  # bis zum Ende der Datei
        if length and start > length - MIN_TRIM:
            start = max(0.0, length - MIN_TRIM)
        music = self.project.music
        if start <= 0 and end <= 0:
            music.trims.pop(track, None)
        else:
            music.trims[track] = [start, end or None]
        row = self.list.currentRow()
        self._fill()
        self.list.setCurrentRow(row)
        self.changed.emit()

    def _reset_trim(self) -> None:
        track = self._selected_track()
        if track is None or self.project is None:
            return
        self.project.music.trims.pop(track, None)
        row = self.list.currentRow()
        self._fill()
        self.list.setCurrentRow(row)
        self.changed.emit()

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
            track = self.project.music.tracks.pop(r)
            if track not in self.project.music.tracks:
                self.project.music.trims.pop(track, None)
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
            length = self._track_length(t) or 0.0
            start, end = self.project.music.trim_of(t)
            total += max(0.0, min(end if end is not None else length, length) - start)
        text = f"Musik insgesamt {format_minutes(total)}"
        if self._show_seconds:
            text += f", Show {format_minutes(self._show_seconds)}"
            if total and total < self._show_seconds:
                text += ", die Musik wird wiederholt."
            elif total:
                text += ", die Musik wird am Ende ausgeblendet."
        self.summary.setText(text)
