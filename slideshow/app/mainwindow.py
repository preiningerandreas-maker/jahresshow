"""Hauptfenster: Zeitleiste links, Eigenschaften/Projekt/Musik rechts, laufende Aufgaben
(Einlesen, Videos umwandeln, Video erstellen) unten mit Fortschritt und Abbrechen."""

from __future__ import annotations

import copy
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from PySide6.QtCore import QSize, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QDesktopServices,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from slideshow import APP_NAME, COPYRIGHT, __version__
from slideshow.app.activity import ActivityPanel, TaskRow
from slideshow.app.icons import icon
from slideshow.app.music import MusicPanel, format_minutes
from slideshow.app.properties import ItemPanel, ProjectPanel
from slideshow.app.renderdialog import RenderDialog
from slideshow.app.reveal import reveal_in_explorer
from slideshow.app.still import StillPreview
from slideshow.app.tasks import Task, VideoPrep
from slideshow.app.theme import apply_theme, dark_title_bar
from slideshow.app.timeline import (
    NO_TRACK_TEXT,
    SOUND_ON,
    ZOOM_MAX,
    ZOOM_MIN,
    ThumbLoader,
    TimelineModel,
    TimelineView,
    item_label,
)
from slideshow.core.chapters import apply_chapters, is_chapter_title, media_items
from slideshow.core.edit import merge_by_date, sort_by_date
from slideshow.core.importer import (
    MUSIC_EXTS,
    PHOTO_EXTS,
    VIDEO_EXTS,
    ImportResult,
    clip_length,
    damage_message,
    describe_file,
    find_existing_duplicates,
    import_media,
    media_kind,
    video_info,
)
from slideshow.core.project import FPS, RESOLUTIONS, Item, Project, TextOverlay
from slideshow.core.thumbs import ThumbProcessPool
from slideshow.render.assemble import (
    FAT32_LIMIT,
    RenderOptions,
    RenderResult,
    estimate_size,
)
from slideshow.render.assemble import render_project as _render_project
from slideshow.render.cache import cleanup_tmp, default_cache_dir, sweep_stale_parts
from slideshow.render.encoder import (
    GPU_MODES,
    GPU_NEVER,
    choose_encoder,
    failed_text,
    forget_failures,
    load_prefs,
    set_gpu_mode,
)

log = logging.getLogger(__name__)

PROJECT_FILTER = f"{APP_NAME}-Projekt (*.json)"


def render_project(*args: object, **kwargs: object) -> RenderResult:
    """Eigene Stelle zum Ersetzen in Tests."""
    return _render_project(*args, **kwargs)  # type: ignore[arg-type]


def format_size(size: float) -> str:
    if size >= 1024**3:
        return f"{size / 1024**3:.1f} GB".replace(".", ",")
    return f"{max(1, round(size / 1024**2))} MB"


def export_summary(seconds: float, resolution: str) -> tuple[str, bool]:
    """Text vor dem Export und ob die Datei für FAT32-USB-Sticks zu groß wird."""
    height = RESOLUTIONS[resolution][1]
    size = estimate_size(seconds, height)
    text = (f"Die Show dauert {format_minutes(seconds)} Minuten.\n"
            f"Die Videodatei wird voraussichtlich höchstens {format_size(size)} groß.")
    too_big = size > FAT32_LIMIT
    if too_big:
        text += ("\n\nAchtung: Das ist mehr als 4 GB. Auf viele USB-Sticks (Format FAT32) passt "
                 "die Datei dann nicht. Abhilfe: den Stick als exFAT formatieren oder die "
                 "Show kürzen bzw. in Full HD statt 4K erstellen.")
    return text, too_big


def new_project() -> Project:
    year = date.today().year
    title = Item("title", duration=5.0,
                 text=TextOverlay(f"Jahresrückblick {year}", position="center", size=110,
                                  bold=True, bar=False))
    return Project(name=f"Jahresrückblick {year}", items=[title])


@dataclass
class ImportOutcome:
    result: ImportResult
    existing_duplicates: list[tuple[Path, Path]] = field(default_factory=list)
    already_in_project: list[Path] = field(default_factory=list)

    @property
    def duplicates(self) -> list[tuple[Path, Path]]:
        return self.result.duplicates + self.existing_duplicates


class MainToolBar(QToolBar):
    """Werkzeugleiste, die nie ins Überlaufmenü umbricht: Ihre Mindestbreite ist die ganze
    Zeile. Damit kann das Hauptfenster nicht schmaler werden, als „Video erstellen“ braucht."""

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        hint = super().minimumSizeHint()
        return QSize(max(hint.width(), self.sizeHint().width()), hint.height())


class NoticeBar(QFrame):
    """Hinweis über den Bereichen (bleibt, bis er geschlossen wird). Die Art erkennt man am
    Symbol, nicht an einer Farbe: Blau ist der Auswahl vorbehalten."""

    ICONS = {"info": "info", "warning": "warning", "error": "error_circle",
             "success": "checkmark_circle"}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("noticeBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        self.icon = QLabel()
        self.icon.setFixedSize(20, 20)
        lay.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        self.label = QLabel("")
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.action_button = QPushButton("")
        self.close_button = QPushButton("Schließen")
        lay.addWidget(self.label, 1)
        lay.addWidget(self.action_button)
        lay.addWidget(self.close_button)
        self.close_button.clicked.connect(self.hide)
        self._action: Callable[[], None] | None = None
        self.action_button.clicked.connect(self._run_action)
        self.kind = "info"
        self.hide()

    def show_message(self, text: str, kind: str = "info",
                     action: tuple[str, Callable[[], None]] | None = None) -> None:
        self.kind = kind
        self.icon.setPixmap(icon(self.ICONS[kind]).pixmap(20, 20))
        self.icon.setToolTip({"info": "Hinweis", "warning": "Achtung", "error": "Fehler",
                              "success": "Erledigt"}[kind])
        self.label.setText(text)
        if action is None:
            self._action = None
            self.action_button.hide()
        else:
            self.action_button.setText(action[0])
            self._action = action[1]
            self.action_button.show()
        self.show()

    def _run_action(self) -> None:
        if self._action is not None:
            self._action()


def _scroll(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidget(widget)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    widget.setContentsMargins(6, 6, 10, 6)
    return area


class MainWindow(QMainWindow):
    def __init__(self, *, cache_dir: Path | None = None, encoder: str | None = None,
                 open_file: Callable[[Path], None] | None = None,
                 reveal_file: Callable[[Path], None] | None = None) -> None:
        """open_file: Datei öffnen (Vorschau im Player). reveal_file: Datei im Explorer
        zeigen (fertiges Video)."""
        super().__init__()
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_theme(app)
        self.cache_dir = cache_dir or default_cache_dir()
        self.forced_encoder = encoder
        self.reveal_file = reveal_file or reveal_in_explorer
        self.open_file = open_file or (
            lambda p: QDesktopServices.openUrl(QUrl.fromLocalFile(str(p))))
        self.project = new_project()
        self.project_path: Path | None = None
        self.dirty = False
        self.total_seconds: float | None = None
        self._chapter_stash: dict[str, Item] = {}
        self._import_task: Task | None = None
        self._render_task: Task | None = None
        self._duration_task: Task | None = None
        self._duration_gen = 0
        self.last_result: RenderResult | None = None
        self._encoder_lock = threading.Lock()

        threading.Thread(target=sweep_stale_parts, args=(self.cache_dir,), daemon=True,
                         name="sweep").start()

        thumb_pool = ThumbProcessPool(self.cache_dir)
        threading.Thread(target=thumb_pool.start, daemon=True, name="thumb-start").start()
        self.thumbs = ThumbLoader(thumb_pool, parent=self)
        self.model = TimelineModel(self.project, self.thumbs, self)
        self.model.reorder = self._apply_chapters
        self.video_prep = VideoPrep(self.cache_dir, self.encoder_for_jobs, self)

        self._build_ui()
        self._build_actions()
        self._connect()
        self.set_project(self.project, None)

        self._duration_timer = QTimer(self)
        self._duration_timer.setSingleShot(True)
        self._duration_timer.setInterval(300)
        self._duration_timer.timeout.connect(self._start_duration)
        self._start_duration()
        dark_title_bar(self)

    # --- Aufbau ----------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.timeline = TimelineView()
        self.timeline.setObjectName("timeline")
        self.timeline.setModel(self.model)

        self.item_panel = ItemPanel()
        self.item_panel.has_track = self.model.has_track
        self.project_panel = ProjectPanel()
        self.music_panel = MusicPanel()
        self.tabs = QTabWidget()
        self.tabs.addTab(_scroll(self.item_panel), "Element")
        self.tabs.addTab(_scroll(self.project_panel), "Show")
        self.tabs.addTab(_scroll(self.music_panel), "Musik")
        self.tabs.tabBar().setExpanding(False)
        self.tabs.setDocumentMode(True)
        right = QWidget()
        right.setObjectName("propertiesPane")
        right.setMinimumWidth(300)
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.addWidget(self.tabs, 1)

        # Monitor in der Mitte: Standbild des markierten Elements, nimmt den meisten Platz ein
        monitor = QWidget()
        monitor.setObjectName("monitorPane")
        monitor.setMinimumWidth(240)
        mon_lay = QVBoxLayout(monitor)
        mon_lay.setContentsMargins(0, 0, 0, 0)
        mon_lay.setSpacing(0)
        self.monitor_title = QLabel("Vorschau")
        self.monitor_title.setObjectName("monitorTitle")
        self.still = StillPreview(self.cache_dir)
        mon_lay.addWidget(self.monitor_title)
        mon_lay.addWidget(self.still, 1)

        self.notice = NoticeBar()
        left = QWidget()
        left.setObjectName("rasterPane")
        left.setMinimumWidth(260)
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        self.empty_hint = QLabel(
            "Fügen Sie Fotos und Videos hinzu: „Ordner hinzufügen“ oben oder Dateien aus dem "
            "Explorer hierher ziehen. Die Reihenfolge ändern Sie, indem Sie Bilder in der "
            "Zeitleiste verschieben.")
        self.empty_hint.setObjectName("emptyHint")
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setContentsMargins(6, 6, 6, 6)
        left_lay.addWidget(self.empty_hint)
        left_lay.addWidget(self.timeline, 1)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(left)
        self.splitter.addWidget(monitor)
        self.splitter.addWidget(right)
        for i, stretch in enumerate((0, 1, 0)):  # zusätzlicher Platz geht an den Monitor
            self.splitter.setStretchFactor(i, stretch)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(5)  # dünne Linie, aber gut zu greifen
        self.splitter.setSizes([400, 600, 320])

        self.activity = ActivityPanel()
        self.import_row = self.activity.add_row(TaskRow("importRow", "Einlesen", 2))
        self.video_row = self.activity.add_row(TaskRow("videoRow", "Videos umwandeln", 1))
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        notice_box = QHBoxLayout()
        notice_box.setContentsMargins(6, 6, 6, 6)
        notice_box.addWidget(self.notice)
        self.notice_box = notice_box
        lay.addLayout(notice_box)
        lay.addWidget(self.splitter, 1)
        lay.addWidget(self.activity)
        self.setCentralWidget(central)
        # Export und Vorschau: eigenes Fenster mittig auf dem Bildschirm (sperrt das Hauptfenster)
        self.render_dialog = RenderDialog(self)

        self.summary_label = QLabel("")
        self.statusBar().addWidget(self.summary_label, 1)
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setObjectName("zoomSlider")
        self.zoom.setRange(ZOOM_MIN, ZOOM_MAX)
        self.zoom.setSingleStep(16)
        self.zoom.setPageStep(32)
        self.zoom.setValue(self.timeline.tile_width)
        self.zoom.setFixedWidth(140)
        tip = "Größe der Bilder in der Zeitleiste (auch mit Strg + Mausrad)"
        self.zoom.setToolTip(tip)
        zoom_label = QLabel("Bildgröße")
        zoom_label.setToolTip(tip)
        self.statusBar().addPermanentWidget(zoom_label)
        self.statusBar().addPermanentWidget(self.zoom)
        self.copyright_label = QLabel(COPYRIGHT)
        self.copyright_label.setObjectName("copyrightLabel")
        self.copyright_label.setToolTip(f"{APP_NAME} {__version__} – Hilfe → Über {APP_NAME}")
        self.statusBar().addPermanentWidget(self.copyright_label)
        self.zoom.valueChanged.connect(self.timeline.set_tile_width)
        self.timeline.zoomChanged.connect(self._on_zoom)

    def _on_zoom(self, width: int) -> None:
        self.zoom.blockSignals(True)
        self.zoom.setValue(width)
        self.zoom.blockSignals(False)

    def _action(self, text: str, slot: Callable[[], object], shortcut: str | None = None,
                icon_name: str | None = None, tip: str | None = None,
                short: str | None = None) -> QAction:
        """short: kurzer Text in der Werkzeugleiste (im Menü steht der volle Text)."""
        act = QAction(text, self)
        if icon_name is not None:
            act.setIcon(icon(icon_name))
        if short:
            act.setIconText(short)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        if tip:
            act.setToolTip(tip)
            act.setStatusTip(tip)
        act.triggered.connect(lambda _checked=False: slot())
        return act

    def _build_actions(self) -> None:
        self.act_new = self._action("Neues Projekt", self.on_new, "Ctrl+N", "document_add")
        self.act_open = self._action("Öffnen …", self.on_open, "Ctrl+O", "folder_open",
                                     "Gespeichertes Projekt öffnen", "Öffnen")
        self.act_save = self._action("Speichern", self.on_save, "Ctrl+S", "save",
                                     "Projekt speichern, um später weiterzuarbeiten")
        self.act_save_as = self._action("Speichern unter …", self.on_save_as, "Ctrl+Shift+S")
        self.act_quit = self._action("Beenden", self.close, "Alt+F4")
        self.act_add_folder = self._action(
            "Ordner hinzufügen", self.on_add_folder, None, "folder_add",
            "Alle Fotos und Videos eines Ordners (mit Unterordnern) hinzufügen", "Ordner")
        self.act_add_files = self._action(
            "Dateien hinzufügen", self.on_add_files, "Ctrl+I", "image_add",
            "Einzelne Fotos und Videos hinzufügen", "Dateien")
        self.act_add_title = self._action(
            "Titelkarte", self.add_title_card, None, "text_t",
            "Titel mit Text auf Farbfläche oder Foto einfügen", "Titel")
        self.act_add_music = self._action(
            "Musik", self.on_add_music, None, "music_note_2", "Musikstücke hinzufügen")
        self.act_remove = self._action("Markierte entfernen", self.remove_selected, None,
                                       "delete")
        self.act_sort = self._action("Nach Aufnahmedatum sortieren", self.sort_by_date, None,
                                     "arrow_sort")
        self.act_preview = self._action(
            "Vorschau", self.on_preview, "F5", "play",
            "Markierte Elemente schnell in kleiner Größe (480p) als Video ansehen")
        self.act_export = self._action(
            "Video erstellen", self.on_export, "Ctrl+E", "arrow_export_ltr",
            "Die fertige Show als MP4-Video speichern")

        menu = self.menuBar()
        m = menu.addMenu("&Datei")
        for a in (self.act_new, self.act_open, self.act_save, self.act_save_as):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.act_quit)
        m = menu.addMenu("&Bearbeiten")
        for a in (self.act_add_folder, self.act_add_files, self.act_add_title,
                  self.act_add_music):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.act_remove)
        m.addAction(self.act_sort)
        m = menu.addMenu("&Show")
        m.addAction(self.act_preview)
        m.addAction(self.act_export)
        m = menu.addMenu("E&xtras")
        self.gpu_menu = m.addMenu("Grafikkarte verwenden")
        self.gpu_menu.setToolTipsVisible(True)
        group = QActionGroup(self)
        self.gpu_actions: dict[str, QAction] = {}
        tips = {
            "auto": "Die Grafikkarte rechnet das Video, wenn sie es kann. Das ist schneller.",
            GPU_NEVER: "Immer der Prozessor. Langsamer, funktioniert aber auf jedem Gerät.",
        }
        for mode, text in GPU_MODES.items():
            act = QAction(text, self)
            act.setCheckable(True)
            act.setToolTip(tips[mode])
            act.setStatusTip(tips[mode])
            act.triggered.connect(lambda _checked=False, mode=mode: self.set_gpu_mode(mode))
            group.addAction(act)
            self.gpu_menu.addAction(act)
            self.gpu_actions[mode] = act
        self.gpu_menu.addSeparator()
        self.act_gpu_retry = self._action(
            "Grafikkarte wieder erlauben", self.retry_gpu, None, None,
            "Die Grafikkarte hat auf diesem Gerät schon einmal versagt und wird deshalb "
            "nicht verwendet. Beim nächsten Export wieder versuchen.")
        self.gpu_menu.addAction(self.act_gpu_retry)
        self.gpu_menu.aboutToShow.connect(self.update_gpu_menu)
        self.update_gpu_menu()
        m = menu.addMenu("&Hilfe")
        self.act_about = self._action(f"Über {APP_NAME} …", self.show_about)
        m.addAction(self.act_about)
        self.act_about_qt = self._action("Über Qt …", lambda: QMessageBox.aboutQt(self))
        m.addAction(self.act_about_qt)

        tb = MainToolBar("Werkzeuge")
        tb.setObjectName("mainToolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        for a in (self.act_open, self.act_save):
            tb.addAction(a)
        tb.addSeparator()
        for a in (self.act_add_folder, self.act_add_files, self.act_add_title,
                  self.act_add_music):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.act_preview)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        # Einziger hervorgehobener Knopf, rechts: weiße Schrift auf Blau (Kontrast 5.1:1)
        primary = QToolButton()
        primary.setObjectName("primaryButton")
        primary.setText("Video erstellen")
        primary.setIcon(icon("arrow_export_ltr", "on_accent"))
        primary.setIconSize(QSize(18, 18))
        primary.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        primary.setToolTip(self.act_export.toolTip())
        primary.clicked.connect(self.act_export.trigger)
        self.act_export.changed.connect(lambda: primary.setEnabled(self.act_export.isEnabled()))
        tb.addWidget(primary)
        self.export_button = primary
        self.addToolBar(tb)
        self.toolbar = tb

    def _connect(self) -> None:
        self.timeline.selectionModel().selectionChanged.connect(self._on_selection)
        self.timeline.filesDropped.connect(self.on_files_dropped)
        self.timeline.deleteRequested.connect(self.remove_selected)
        self.timeline.soundToggled.connect(self.toggle_sound)
        self.model.rowsMovedByUser.connect(lambda _rows: self.mark_dirty())
        self.item_panel.changed.connect(self._on_item_changed)
        self.project_panel.changed.connect(self._on_project_changed)
        self.project_panel.chaptersToggled.connect(self.set_chapters)
        self.music_panel.changed.connect(self.mark_dirty)
        self._connect_video_prep()

    def _connect_video_prep(self) -> None:
        prep = self.video_prep
        prep.status.connect(self._on_video_status)
        prep.idle.connect(self._on_video_idle)
        prep.converted.connect(self._on_video_converted)
        prep.error.connect(lambda _p, msg: self.notice.show_message(msg, "error"))

    # --- Info ------------------------------------------------------------------------

    def about_text(self) -> str:
        return (f"<h3>{APP_NAME}</h3>"
                f"<p>Version {__version__}</p>"
                "<p>Macht aus Fotos und Videos eine Slideshow mit Musik und Texten und "
                "speichert sie als MP4-Video.</p>"
                f"<p>{COPYRIGHT}</p>"
                f"<p>{APP_NAME} verwendet freie Software, unter anderem FFmpeg, Qt und Python. "
                "Die Lizenzen stehen im Ordner „Lizenzen“ neben dem Programm.</p>")

    def show_about(self) -> None:
        QMessageBox.about(self, f"Über {APP_NAME}", self.about_text())

    # --- Kodierer --------------------------------------------------------------------

    def encoder_for_jobs(self) -> str:
        """Kodierer für die Umwandlung: nach Einstellung und gemerkten Pannen (Probe-Encode
        einmalig, Ergebnis im Cache)."""
        with self._encoder_lock:
            return choose_encoder(self.cache_dir, self.forced_encoder).name

    def update_gpu_menu(self) -> None:
        prefs = load_prefs(self.cache_dir)
        self.gpu_actions[prefs.mode].setChecked(True)
        self.act_gpu_retry.setVisible(bool(prefs.failed))
        if prefs.failed:
            self.act_gpu_retry.setText(f"Grafikkarte wieder erlauben (versagt: "
                                       f"{failed_text(prefs)})")

    def set_gpu_mode(self, mode: str) -> None:
        try:
            set_gpu_mode(self.cache_dir, mode)
        except OSError as e:
            self.notice.show_message(f"Die Einstellung konnte nicht gespeichert werden: {e}",
                                     "error")
        self.update_gpu_menu()

    def retry_gpu(self) -> None:
        try:
            forget_failures(self.cache_dir)
        except OSError as e:
            self.notice.show_message(f"Die Einstellung konnte nicht gespeichert werden: {e}",
                                     "error")
        else:
            self.notice.show_message(
                "Die Grafikkarte wird beim nächsten Export wieder verwendet. Versagt sie "
                "erneut, rechnet automatisch der Prozessor weiter.", "info")
        self.update_gpu_menu()

    # --- Projekt ---------------------------------------------------------------------

    def set_project(self, project: Project, path: Path | None) -> None:
        self.project = project
        self.project_path = path
        self._chapter_stash = {}
        project.items = apply_chapters(project.items, project.settings.chapters)
        self.model.duplicates = set()
        self.model.set_project(project)
        self.project_panel.set_project(project)
        self.music_panel.set_project(project)
        self.item_panel.set_items(project, [])
        self._show_still([])
        self.dirty = False
        self.total_seconds = None
        self._update_title()
        self._update_empty_hint()
        self._submit_videos(project.items)
        self._start_duration_later()

    def _update_title(self) -> None:
        name = self.project_path.name if self.project_path else "Neues Projekt"
        self.setWindowTitle(f"{'*' if self.dirty else ''}{name} – {APP_NAME}")

    def _update_empty_hint(self) -> None:
        has_media = any(it.kind != "title" for it in self.project.items)
        self.empty_hint.setVisible(not has_media)

    def mark_dirty(self) -> None:
        self.dirty = True
        self._update_title()
        self._update_empty_hint()
        self._start_duration_later()

    def save_to(self, path: Path) -> None:
        self.project.save(path)
        self.project_path = path
        self.dirty = False
        self._update_title()

    def load_from(self, path: Path) -> None:
        project = Project.load(path)
        self.set_project(project, path.resolve())
        missing = [project.resolve(it.path) for it in project.items
                   if it.path and not project.resolve(it.path).is_file()]
        missing += [project.resolve(t) for t in project.music.tracks
                    if not project.resolve(t).is_file()]
        if missing:
            names = "\n".join(f"• {describe_file(p)}" for p in missing[:10])
            more = f"\n… und {len(missing) - 10} weitere" if len(missing) > 10 else ""
            self.notice.show_message(
                f"{len(missing)} Datei(en) des Projekts wurden nicht gefunden "
                f"(verschoben oder gelöscht):\n{names}{more}", "warning")

    def maybe_save(self) -> bool:
        """Vor Neu/Öffnen/Beenden: ungespeicherte Änderungen sichern? False = abbrechen."""
        if not self.dirty:
            return True
        box = QMessageBox(QMessageBox.Icon.Question, APP_NAME,
                          "Das Projekt wurde geändert. Änderungen speichern?",
                          parent=self)
        save = box.addButton("Speichern", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Nicht speichern", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return self.on_save()
        return clicked is discard

    def on_new(self) -> None:
        if self.maybe_save():
            self.set_project(new_project(), None)

    def on_open(self) -> None:
        if not self.maybe_save():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Projekt öffnen", self._documents(),
                                              PROJECT_FILTER)
        if path:
            self.open_project(Path(path))

    def open_project(self, path: Path) -> None:
        try:
            self.load_from(path)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, APP_NAME, "Das Projekt konnte nicht geöffnet werden:\n"
                                f"{describe_file(path)}\n{e}")

    def on_save(self) -> bool:
        if self.project_path is None:
            return self.on_save_as()
        return self._save_checked(self.project_path)

    def on_save_as(self) -> bool:
        start = str(self.project_path or Path(self._documents()) / f"{self.project.name}.json")
        path, _ = QFileDialog.getSaveFileName(self, "Projekt speichern", start, PROJECT_FILTER)
        if not path:
            return False
        p = Path(path)
        if p.suffix.lower() != ".json":
            p = p.with_suffix(p.suffix + ".json") if p.suffix else p.with_suffix(".json")
        return self._save_checked(p)

    def _save_checked(self, path: Path) -> bool:
        try:
            self.save_to(path)
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, "Speichern ist fehlgeschlagen:\n"
                                f"{describe_file(path)}\n{e}")
            return False
        self.statusBar().showMessage(f"Gespeichert: {path}", 5000)
        return True

    @staticmethod
    def _documents() -> str:
        return QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)

    # --- Zeitleiste ------------------------------------------------------------------

    def _apply_chapters(self, items: list[Item]) -> list[Item]:
        on = self.project.settings.chapters
        if on:
            # Früher ausgeblendete (evtl. bearbeitete) Zwischentitel wieder verwenden
            items = list(self._chapter_stash.values()) + items
        return apply_chapters(items, on)

    def set_chapters(self, on: bool) -> None:
        rows = self.timeline.selected_rows()
        chosen = {id(self.project.items[r]) for r in rows}
        self.project.settings.chapters = on
        if not on:
            for it in self.project.items:
                if is_chapter_title(it):
                    assert it.chapter is not None
                    self._chapter_stash[it.chapter] = it
        self.model.replace_items(self._apply_chapters(self.project.items))
        self.project_panel.set_chapters_checked(on)
        self.timeline.select_rows([i for i, it in enumerate(self.project.items)
                                   if id(it) in chosen])
        self.mark_dirty()

    def selected_items(self) -> list[Item]:
        return [self.project.items[r] for r in self.timeline.selected_rows()]

    def _on_selection(self) -> None:
        items = self.selected_items()
        self.item_panel.set_items(self.project, items)
        self._show_still(items)
        if items:
            self.tabs.setCurrentIndex(0)

    def _show_still(self, items: list[Item] | None = None) -> None:
        """Standbild des (ersten) markierten Elements."""
        items = self.selected_items() if items is None else items
        self.still.show_item(self.project, items[0] if items else None)
        self.monitor_title.setText(
            f"Vorschau: {self.item_panel.header.text()}" if items else "Vorschau")

    def _on_item_changed(self) -> None:
        self.model.refresh_rows(self.timeline.selected_rows())
        self._show_still()
        self.mark_dirty()

    def toggle_sound(self, row: int) -> None:
        """Lautsprecher-Symbol in der Zeitleiste: Originalton nur dieses Clips umschalten."""
        item = self.project.items[row]
        if item.kind != "video":
            return
        if not self.model.has_track(item):
            self.statusBar().showMessage(f"{item_label(item)}: {NO_TRACK_TEXT}.", 5000)
            return
        if self.model.sound(item) == SOUND_ON:
            item.audio = False
        else:
            item.audio = True
            if item.volume <= 0:  # sonst bliebe der Clip trotz Haken stumm
                item.volume = 1.0
        self.model.refresh_rows([row])
        if row in self.timeline.selected_rows():
            self.item_panel.set_items(self.project, self.selected_items())
        self.mark_dirty()

    def _learn_silent(self, silent: set[Path], checked: set[Path]) -> None:
        """Ergebnis der Tonspur-Prüfung übernehmen; Kacheln und Eigenschaften neu zeichnen."""
        new = (self.model.silent - checked) | silent
        if new == self.model.silent:
            return
        self.model.silent = new
        self.model.refresh_rows(None)
        self.item_panel.set_items(self.project, self.selected_items())

    def _on_project_changed(self) -> None:
        self.model.refresh_rows(None)
        self.mark_dirty()

    def add_title_card(self) -> None:
        rows = self.timeline.selected_rows()
        pos = rows[0] if rows else 0
        title = Item("title", duration=5.0, text=TextOverlay(
            self.project.name or "Titel", position="center", size=110, bold=True, bar=False))
        items = list(self.project.items)
        items.insert(pos, title)
        self.model.replace_items(self._apply_chapters(items))
        self.timeline.select_rows([self.project.items.index(title)])
        self.mark_dirty()

    def remove_selected(self) -> None:
        rows = set(self.timeline.selected_rows())
        if not rows:
            return
        if self.project.settings.chapters and all(
                is_chapter_title(self.project.items[r]) for r in rows):
            self.notice.show_message(
                "Zwischentitel entstehen automatisch für jeden Monat. Abschalten können Sie "
                "sie im Bereich „Show“ unter „Zwischentitel für jeden Monat“.", "info")
            return
        items = [it for i, it in enumerate(self.project.items) if i not in rows]
        self.model.replace_items(self._apply_chapters(items))
        self.item_panel.set_items(self.project, [])
        self._show_still([])
        self.mark_dirty()

    def sort_by_date(self) -> None:
        self.model.replace_items(self._apply_chapters(sort_by_date(self.project.items)))
        self.mark_dirty()

    # --- Import ----------------------------------------------------------------------

    def on_add_folder(self) -> None:
        start = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
        folder = QFileDialog.getExistingDirectory(self, "Ordner mit Fotos und Videos", start)
        if folder:
            self.import_paths([Path(folder)])

    def on_add_files(self) -> None:
        start = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
        exts = " ".join(f"*{e}" for e in sorted(PHOTO_EXTS | VIDEO_EXTS))
        files, _ = QFileDialog.getOpenFileNames(self, "Fotos und Videos hinzufügen", start,
                                                f"Fotos und Videos ({exts})")
        if files:
            self.import_paths([Path(f) for f in files])

    def on_add_music(self) -> None:
        self.tabs.setCurrentWidget(self.tabs.widget(2))
        self.music_panel._choose()

    def on_files_dropped(self, files: list[str]) -> None:
        paths = [Path(f) for f in files]
        music = [p for p in paths if p.suffix.lower() in MUSIC_EXTS]
        projects = [p for p in paths if p.suffix.lower() == ".json"]
        media = [p for p in paths if p.is_dir() or media_kind(p) is not None]
        if projects and self.maybe_save():
            self.open_project(projects[0])
            return
        if music:
            self.music_panel.add_tracks(music)
        if media:
            self.import_paths(media)

    def import_paths(self, paths: list[Path]) -> Task | None:
        if self._import_task is not None and self._import_task.running:
            self.notice.show_message("Es werden gerade noch Dateien eingelesen. Bitte warten "
                                     "Sie, bis das abgeschlossen ist.", "info")
            return None
        existing = [self.project.resolve(it.path) for it in self.project.items
                    if it.path and it.kind != "title"]

        def run(task: Task) -> ImportOutcome:
            def progress(i: int, n: int) -> None:
                task.report(f"Fotos und Videos werden eingelesen: {i} von {n}", i / max(1, n))

            result = import_media(paths, progress=progress, cancel=task.cancel_event)
            task.report("Doppelte Dateien werden gesucht …", 1.0)
            dups, already = find_existing_duplicates(result.media, existing, task.cancel_event)
            # Paare innerhalb des Imports, an denen eine schon vorhandene Datei beteiligt ist,
            # zählen als Duplikat zur Projektdatei (die selbst nicht neu hinzukommt)
            known = {p.resolve() for p in already}
            batch = []
            for dup, orig in result.duplicates:
                if dup.resolve() in known:
                    continue
                if orig.resolve() in known:
                    if all(d != dup for d, _ in dups):
                        dups.append((dup, orig))
                else:
                    batch.append((dup, orig))
            result.duplicates = batch
            return ImportOutcome(result, dups, already)

        task = Task(run, "import", self)
        self._import_task = task
        self.import_row.start("Fotos und Videos werden eingelesen …", task.cancel)
        task.progress.connect(self.import_row.update_progress)
        task.done.connect(self._finish_import)
        task.failed.connect(lambda e: self.notice.show_message(
            f"Beim Einlesen ist ein Fehler aufgetreten: {e}", "error"))
        task.cancelled.connect(lambda: self.notice.show_message(
            "Einlesen abgebrochen. Es wurde nichts hinzugefügt.", "info"))
        task.finished.connect(self.import_row.finish)
        return task.start()

    def ask_duplicates(self, outcome: ImportOutcome) -> bool:
        """Doppelte Dateien melden. True: Doppelte weglassen, False: trotzdem übernehmen."""
        lines = [f"• „{d.name}“ ist identisch mit „{o.name}“" for d, o in outcome.duplicates]
        shown = "\n".join(lines[:15])
        if len(lines) > 15:
            shown += f"\n… und {len(lines) - 15} weitere"
        box = QMessageBox(QMessageBox.Icon.Question, "Doppelte Dateien gefunden",
                          f"{len(lines)} Datei(en) sind doppelt vorhanden (gleicher Inhalt):"
                          f"\n\n{shown}\n\nSollen die doppelten weggelassen werden?",
                          parent=self)
        skip = box.addButton("Doppelte weglassen", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Alle übernehmen", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(skip)
        box.exec()
        return box.clickedButton() is skip

    def _finish_import(self, outcome: ImportOutcome) -> None:
        res = outcome.result
        already = {p.resolve() for p in outcome.already_in_project}
        media = [m for m in res.media if m.path.resolve() not in already]
        dup_paths = {d.resolve() for d, _ in outcome.duplicates}
        messages: list[str] = []
        if outcome.duplicates:
            if self.ask_duplicates(outcome):
                media = [m for m in media if m.path.resolve() not in dup_paths]
                messages.append(f"{len(dup_paths)} doppelte Datei(en) wurden weggelassen.")
            else:
                self.model.duplicates |= dup_paths
                messages.append(f"{len(dup_paths)} doppelte Datei(en) sind in der Zeitleiste "
                                "mit „doppelt“ markiert.")
        if already:
            messages.append(f"{len(already)} Datei(en) waren schon im Projekt und wurden nicht "
                            "noch einmal hinzugefügt.")
        damaged = [m for m in media if m.damage is not None and not m.damage_minor]
        minor = [m for m in media if m.damage_minor]
        messages += [damage_message(m) for m in damaged + minor]
        for p, reason in res.skipped:
            messages.append(f"{describe_file(p)} wurde übersprungen: {reason}")
        if res.live_videos:
            messages.append(f"{len(res.live_videos)} Live-Photo-Video(s) vom iPhone: "
                            "verwendet wird nur das Foto.")
        motion = sum(1 for m in media if m.motion_photo)
        if motion:
            messages.append(f"{motion} Bewegtes Foto (Motion Photo): verwendet wird nur das "
                            "Standbild.")

        for m in media:
            if m.kind == "video" and m.duration is not None:
                self.model.clip_seconds[m.path.resolve()] = m.duration
        videos = {m.path.resolve() for m in media if m.kind == "video"}
        self._learn_silent({m.path.resolve() for m in media
                            if m.kind == "video" and not m.has_audio}, videos)
        new_items = media_items(media)
        chosen = {id(it) for it in new_items}
        items = merge_by_date(self.project.items, new_items)
        self.model.replace_items(self._apply_chapters(items))
        self.timeline.select_rows([i for i, it in enumerate(self.project.items)
                                   if id(it) in chosen][:1])
        self._submit_videos(new_items)
        text = (f"{len(new_items)} Foto(s) und Video(s) hinzugefügt, nach Aufnahmedatum "
                "einsortiert.")
        if messages:
            text += "\n" + "\n".join(messages[:12])
            if len(messages) > 12:
                text += f"\n… und {len(messages) - 12} weitere Hinweise"
        if damaged:
            text += (f"\n{len(damaged)} beschädigte Datei(en) sind in der Zeitleiste mit "
                     "„beschädigt“ markiert.")
        if minor:
            text += (f"\n{len(minor)} leicht beschädigte Datei(en) sind vollständig lesbar "
                     "und werden verwendet.")
        kind = "warning" if res.skipped or outcome.duplicates or damaged else "success"
        self.notice.show_message(text, kind)
        if new_items:
            self.mark_dirty()

    # --- Videos umwandeln -----------------------------------------------------------

    def _submit_videos(self, items: list[Item]) -> None:
        videos = list(dict.fromkeys(self.project.resolve(it.path) for it in items
                                    if it.kind == "video" and it.path))
        if videos:
            self.video_prep.submit(videos)

    def _on_video_status(self, text: str, fraction: float) -> None:
        if not self.video_row.active:
            self.video_row.start(text, self.cancel_video_prep)
        self.video_row.update_progress(text, fraction)

    def _on_video_converted(self, src: Path) -> None:
        self._start_duration_later()
        items = self.selected_items()
        if items and items[0].path and self.project.resolve(items[0].path) == src:
            self.still.refresh()  # jetzt mit den Farben der umgewandelten Fassung

    def _on_video_idle(self) -> None:
        self.video_row.finish()
        self.statusBar().showMessage("Alle Videos sind für die Show vorbereitet.", 5000)

    def cancel_video_prep(self) -> None:
        self.video_prep.shutdown()
        self.video_row.finish()
        self.video_prep = VideoPrep(self.cache_dir, self.encoder_for_jobs, self)
        self._connect_video_prep()
        self.notice.show_message(
            "Umwandeln der Videos angehalten. Fertige Videos bleiben erhalten; der Rest wird "
            "beim Erstellen des Videos nachgeholt.", "info")

    # --- Dauer ------------------------------------------------------------------------

    def _start_duration_later(self) -> None:
        self._duration_gen += 1  # laufende Berechnung ist damit veraltet
        if hasattr(self, "_duration_timer"):
            self._duration_timer.start()

    @property
    def duration_pending(self) -> bool:
        """Wird die Show-Dauer gerade (neu) berechnet?"""
        return self._duration_timer.isActive() or (
            self._duration_task is not None and self._duration_task.running)

    def _snapshot(self) -> Project:
        p = self.project
        return replace(p, items=[copy.copy(it) for it in p.items],
                       music=copy.deepcopy(p.music), settings=copy.copy(p.settings))

    def _start_duration(self) -> None:
        self._duration_gen += 1
        gen = self._duration_gen
        snap = self._snapshot()

        def run(_task: Task) -> tuple[int, float, dict[Path, float], set[Path]]:
            clips: dict[Path, float] = {}
            silent: set[Path] = set()
            for it in snap.items:
                if it.kind == "video" and it.path:
                    src = snap.resolve(it.path)
                    clips[src] = clip_length(src)
                    if not video_info(src).has_audio:
                        silent.add(src)
            return gen, snap.total_frames(clip_length) / FPS, clips, silent

        task = Task(run, "duration", self)
        task.done.connect(self._on_duration)
        task.failed.connect(lambda e: self._on_duration_failed(gen, e))
        self._duration_task = task.start()

    def _on_duration(self, data: tuple[int, float, dict[Path, float], set[Path]]) -> None:
        gen, seconds, clips, silent = data
        if gen != self._duration_gen:
            return
        self._learn_silent(silent, set(clips))
        self.total_seconds = seconds
        new = {k: v for k, v in clips.items() if self.model.clip_seconds.get(k) != v}
        if new:
            self.model.clip_seconds.update(new)
            self.model.refresh_rows(None)
        n = sum(1 for it in self.project.items if it.kind != "title")
        size = estimate_size(seconds, RESOLUTIONS[self.project.settings.resolution][1])
        text = (f"{n} Fotos/Videos · Show-Dauer {format_minutes(seconds)} · "
                f"Videodatei höchstens ca. {format_size(size)}")
        if size > FAT32_LIMIT:
            text += ". Achtung: größer als 4 GB, passt nicht auf FAT32-USB-Sticks!"
        self.summary_label.setText(text)
        self.music_panel.set_show_length(seconds)

    def _on_duration_failed(self, gen: int, e: object) -> None:
        if gen != self._duration_gen:
            return
        self.total_seconds = None
        if isinstance(e, FileNotFoundError):
            self.summary_label.setText("Dauer unbekannt: eine Datei fehlt.")
        else:
            self.summary_label.setText(f"Dauer unbekannt: {e}")

    # --- Vorschau und Export --------------------------------------------------------

    @property
    def rendering(self) -> bool:
        return self._render_task is not None and self._render_task.running

    def _set_render_actions(self, enabled: bool) -> None:
        self.act_preview.setEnabled(enabled)
        self.act_export.setEnabled(enabled)

    def preview_range(self) -> tuple[int, int]:
        """Vorschau-Abschnitt (1-basiert, inklusive): markierte Elemente, sonst die ersten 10."""
        rows = self.timeline.selected_rows()
        if rows:
            return rows[0] + 1, rows[-1] + 1
        return 1, min(10, len(self.project.items))

    def on_preview(self) -> Task | None:
        if not self.project.items or self.rendering:
            return None
        first, last = self.preview_range()
        out_dir = self.cache_dir / "preview"
        out_dir.mkdir(parents=True, exist_ok=True)
        for old in out_dir.glob("vorschau-*.mp4"):
            cleanup_tmp(old)  # vorherige Vorschau (geöffnete Dateien bleiben einfach liegen)
        out = out_dir / f"vorschau-{uuid.uuid4().hex[:8]}.mp4"
        opts = RenderOptions(output=out, encoder=self.forced_encoder, preview=True,
                             first=first, last=last, cache_dir=self.cache_dir)
        return self._start_render(opts, f"Vorschau von Element {first} bis {last} wird erstellt")

    def on_export(self) -> Task | None:
        if not any(it for it in self.project.items):
            QMessageBox.information(self, APP_NAME, "Das Projekt ist noch leer.")
            return None
        if self.rendering:
            return None
        videos = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation)
        base = self.project_path.parent if self.project_path else Path(videos)
        start = str(base / f"{self.project.name or APP_NAME}.mp4")
        path, _ = QFileDialog.getSaveFileName(self, "Show als Video speichern", start,
                                              "MP4-Video (*.mp4)")
        if not path:
            return None
        out = Path(path)
        if out.suffix.lower() != ".mp4":
            out = out.with_suffix(".mp4")
        return self.export_to(out)

    def confirm_export(self, text: str, too_big: bool) -> bool:
        icon = QMessageBox.Icon.Warning if too_big else QMessageBox.Icon.Question
        box = QMessageBox(icon, "Video erstellen", text, parent=self)
        go = box.addButton("Video erstellen", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is go

    def export_to(self, output: Path, *, confirm: bool = True) -> Task | None:
        try:
            seconds = self.project.total_frames(clip_length) / FPS
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, APP_NAME, f"Das Video kann nicht erstellt werden:\n{e}")
            return None
        text, too_big = export_summary(seconds, self.project.settings.resolution)
        damaged = {it.path for it in self.project.items if it.path and it.skip_damaged}
        if damaged:
            text += (f"\n\n{len(damaged)} beschädigte Datei(en) werden übersprungen, die "
                     "Show wird entsprechend kürzer.")
        pending = self.video_prep.pending
        if pending:
            text += (f"\n\n{pending} Video(s) werden noch vorbereitet. Das Erstellen wartet "
                     "darauf und dauert entsprechend länger.")
        if confirm and not self.confirm_export(text, too_big):
            return None
        opts = RenderOptions(output=output, encoder=self.forced_encoder,
                             cache_dir=self.cache_dir)
        return self._start_render(opts, "Video wird erstellt")

    def _start_render(self, opts: RenderOptions, label: str) -> Task:
        snapshot = copy.deepcopy(self.project)

        def run(task: Task) -> RenderResult:
            return render_project(snapshot, opts, status=task.report, cancel=task.cancel_event)

        task = Task(run, "render", self)
        self._render_task = task
        self._set_render_actions(False)
        self.render_dialog.start(label, task.cancel)
        task.progress.connect(self.render_dialog.update_progress)
        task.done.connect(lambda r: self._render_done(r, opts))
        task.failed.connect(self._render_failed)
        task.cancelled.connect(lambda: self.notice.show_message(
            "Abgebrochen. Bereits fertige Teile bleiben gespeichert, beim nächsten Mal geht "
            "es schneller.", "info"))
        task.finished.connect(self._render_finished)
        return task.start()

    def _render_finished(self) -> None:
        self.render_dialog.finish()
        self._set_render_actions(True)

    def _render_failed(self, e: object) -> None:
        self.notice.show_message(f"Das Video konnte nicht erstellt werden:\n{e}", "error")

    def _skipped_summary(self, result: RenderResult) -> str:
        if not result.skipped:
            return ""
        lines = [f"• {s.message()}" for s in result.skipped[:10]]
        if len(result.skipped) > 10:
            lines.append(f"… und {len(result.skipped) - 10} weitere")
        text = (f"\n\nÜbersprungen, weil beschädigt ({len(result.skipped)}):\n"
                + "\n".join(lines))
        if result.dropped_titles:
            titles = ", ".join(f"„{t}“" for t in result.dropped_titles)
            text += ("\n\nAlle Elemente eines Kapitels sind weggefallen, deshalb entfällt "
                     f"auch der Zwischentitel: {titles}")
        return text

    def _encoder_summary(self, result: RenderResult) -> str:
        return f"\n\nHinweis: {result.encoder_note}" if result.encoder_note else ""

    def _mark_skipped(self, result: RenderResult) -> None:
        """Erst beim Export gefundene Schäden auch in der Zeitleiste markieren."""
        found = {s.path.resolve(): s for s in result.skipped + result.minor}
        changed = False
        for it in self.project.items:
            if it.path and it.damage is None:
                s = found.get(self.project.resolve(it.path).resolve())
                if s is not None:
                    it.damage, it.damage_readable = s.reason, s.readable
                    changed = True
        if changed:
            self.model.refresh_rows(None)
            self._on_selection()
            self.mark_dirty()

    def _render_done(self, result: RenderResult, opts: RenderOptions) -> None:
        self.last_result = result
        self._mark_skipped(result)
        if opts.preview:
            if result.notes:
                n = len(result.notes)
                self.notice.show_message(
                    f"Hinweis zur Vorschau: {n} Video(s) sind noch nicht für die Show "
                    "umgewandelt, die Vorschau zeigt deshalb die Originale. Farben, Helligkeit "
                    "und Bildrate können im fertigen Video anders aussehen.\n"
                    + "\n".join(f"• {note}" for note in result.notes[:5])
                    + self._skipped_summary(result), "warning")
            elif result.skipped or result.encoder_note:
                self.notice.show_message(
                    "Vorschau erstellt." + self._encoder_summary(result)
                    + self._skipped_summary(result), "warning")
            self.open_file(result.output)
            return
        size = result.output.stat().st_size if result.output.is_file() else 0
        text = (f"Fertig! Die Show wurde gespeichert:\n{result.output}\n"
                f"Dauer {format_minutes(result.duration)}, Größe {format_size(size)}.")
        for w in result.warnings:
            text += f"\nAchtung: {w}"
        text += self._encoder_summary(result)
        text += self._skipped_summary(result)
        output = result.output
        problems = result.warnings or result.skipped or result.fallback_element is not None
        self.notice.show_message(text, "warning" if problems else "success",
                                 ("Ordner öffnen", lambda: self.reveal_file(output)))
        QApplication.alert(self)
        # Explorer mit dem fertigen Video öffnen, die Datei ist dort markiert
        if output.is_file():
            self.reveal_file(output)

    # --- Zweiter Programmstart ------------------------------------------------------

    def bring_to_front(self, files: list[str] | None = None) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        QApplication.alert(self)
        if files:
            self.on_files_dropped(files)

    # --- Beenden ----------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.rendering:
            answer = QMessageBox.question(
                self, APP_NAME, "Das Video wird gerade erstellt. Abbrechen und beenden?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        if not self.maybe_save():
            event.ignore()
            return
        self.shutdown()
        event.accept()

    def shutdown(self) -> None:
        self.still.shutdown()
        for task in (self._render_task, self._import_task):
            if task is not None and task.running:
                task.cancel()
                task.wait(10)
        self.video_prep.shutdown()
        self.thumbs.shutdown()
