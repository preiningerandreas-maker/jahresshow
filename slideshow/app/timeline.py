"""Zeitleiste: Vorschaubilder in Reihen, Reihenfolge per Drag & Drop.

Für 1000 Elemente und mehr: Alle Kacheln sind gleich groß (die Ansicht muss nichts messen),
gezeichnet werden nur sichtbare Kacheln, und Vorschaubilder lädt ein Hintergrund-Thread erst,
wenn eine Kachel sichtbar wird. Zuletzt angefragte Bilder kommen zuerst dran, damit beim
schnellen Scrollen die gerade sichtbaren Kacheln nicht warten müssen."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QItemSelection,
    QItemSelectionModel,
    QMimeData,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDrag,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QImage,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from slideshow.app.icons import icon
from slideshow.app.theme import RADIUS, color
from slideshow.core.chapters import is_chapter_title
from slideshow.core.edit import move_items
from slideshow.core.importer import media_kind
from slideshow.core.project import Item, Project, readable_text
from slideshow.core.thumbs import ThumbnailCache, ThumbProcessPool

MIME_ROWS = "application/x-flashback-rows"
# Kachelgröße (Breite des Vorschaubilds in logischen Pixeln), per Zoom-Regler einstellbar
ZOOM_MIN, ZOOM_DEFAULT, ZOOM_MAX = 96, 160, 288
LOAD_BOX = 256  # Vorschaubilder im Speicher: längere Seite in Pixeln
PIXMAP_CACHE = 300  # Vorschaubilder im Speicher (je ca. 200 KB)
BLUR_W = 12  # Breite des Unschärfe-Hintergrunds; hochskaliert wirkt er weich wie im Export
MAX_QUEUE = 300
SCROLL_PAUSE = 0.15  # Sekunden ohne Nachladen nach jeder Scrollbewegung

KIND_LABELS = {"photo": "Foto", "video": "Video", "title": "Titel"}
DAMAGED_TEXT = "beschädigt"
MINOR_DAMAGE_TIP = "Datei leicht beschädigt, vollständig lesbar"
# Bildinhalt wie im Export (keine Theme-Farben): schwarze Ränder, Abdunkeln bei blur,
# weißer Titeltext auf der Titelfarbe des Nutzers
BLACK = QColor("#000000")
DARKEN = QColor(0, 0, 0, 38)  # wie eq=brightness=-0.15 im Export
TITLE_TEXT = QColor("#FFFFFF")


def item_label(item: Item) -> str:
    """Erste Zeile der Kachel."""
    if item.kind == "title":
        raw = item.text.text.strip() if item.text else ""
        text = raw.splitlines()[0] if raw else ""
        if is_chapter_title(item):
            return text or "Zwischentitel"
        return text or "Titelkarte"
    return Path(item.path or "").name


def format_seconds(seconds: float) -> str:
    return f"{seconds:.1f}".replace(".", ",") + " s"


@dataclass(slots=True)
class Tile:
    """Alles, was eine Kachel zum Zeichnen braucht (einmal berechnet, dann gecacht)."""

    label: str
    subtitle: str
    kind: str
    chapter: bool
    duplicate: bool
    damaged: bool  # Bilddaten beschädigt
    slightly_damaged: bool  # leicht beschädigt, vollständig lesbar (wird verwendet)
    background: str
    thumb_key: str | None  # Pfad des Vorschaubilds, None bei Titeln ohne Foto
    fit: str  # Formatmodus blur | black | crop


class ThumbLoader(QObject):
    """Lädt Vorschaubilder im Hintergrund (Datei-Cache über ThumbnailCache) und liefert sie
    als QImage in Kachelgröße. QPixmap entsteht erst im Hauptthread."""

    loaded = Signal(str, QImage)
    failed = Signal(str)

    def __init__(self, cache: ThumbnailCache | ThumbProcessPool, workers: int = 2,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cache = cache
        self._stack: deque[Path] = deque()
        self._queued: set[Path] = set()
        self._cond = threading.Condition()
        self._stop = False
        self._busy = 0
        self._resume_at = 0.0
        self._threads = [threading.Thread(target=self._work, name=f"thumbs-{i}", daemon=True)
                         for i in range(workers)]
        for t in self._threads:
            t.start()

    def request(self, src: Path) -> None:
        with self._cond:
            if src in self._queued:
                # Schon eingereiht: nach oben holen, die Kachel ist gerade sichtbar
                try:
                    self._stack.remove(src)
                except ValueError:
                    return  # wird gerade bearbeitet
                self._stack.append(src)
                return
            self._queued.add(src)
            self._stack.append(src)
            while len(self._stack) > MAX_QUEUE:  # längst weggescrollt: verwerfen
                self._queued.discard(self._stack.popleft())
            self._cond.notify()

    def pause(self, seconds: float = SCROLL_PAUSE) -> None:
        """Nachladen kurz anhalten (beim Scrollen). Laufende Bilder werden fertig."""
        with self._cond:
            self._resume_at = time.monotonic() + seconds
            self._cond.notify_all()

    def idle(self) -> bool:
        with self._cond:
            return not self._stack and self._busy == 0

    def _work(self) -> None:
        while True:
            with self._cond:
                while not self._stop:
                    # Während gescrollt wird, nichts Neues anfangen: die Oberfläche hat Vorrang
                    wait = self._resume_at - time.monotonic()
                    if wait > 0:
                        self._cond.wait(wait)
                    elif not self._stack:
                        self._cond.wait()
                    else:
                        break
                if self._stop:
                    return
                src = self._stack.pop()  # zuletzt angefragt zuerst
                self._busy += 1
            try:
                thumb = self.cache.create(src)
                img = QImage(str(thumb))
                if img.isNull():
                    raise OSError("Vorschaubild unlesbar")
                img = img.scaled(LOAD_BOX, LOAD_BOX, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
            except Exception:  # noqa: BLE001 - kaputte Datei: Platzhalter zeigen
                self.failed.emit(str(src))
            else:
                self.loaded.emit(str(src), img)
            finally:
                with self._cond:
                    self._busy -= 1
                    self._queued.discard(src)

    def shutdown(self) -> None:
        with self._cond:
            self._stop = True
            self._stack.clear()
            self._cond.notify_all()
        for t in self._threads:
            t.join(timeout=5)
        self.cache.shutdown(wait=False)


class TimelineModel(QAbstractListModel):
    """Elemente des Projekts als Liste. Das Modell arbeitet direkt auf project.items."""

    PathRole = Qt.ItemDataRole.UserRole + 1
    KindRole = Qt.ItemDataRole.UserRole + 2
    SubtitleRole = Qt.ItemDataRole.UserRole + 3
    ChapterRole = Qt.ItemDataRole.UserRole + 4
    DuplicateRole = Qt.ItemDataRole.UserRole + 5
    BackgroundRole = Qt.ItemDataRole.UserRole + 6

    rowsMovedByUser = Signal(list)  # neue Zeilen der verschobenen Elemente

    def __init__(self, project: Project, loader: ThumbLoader | None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self.loader = loader
        self.clip_seconds: dict[Path, float] = {}  # bekannte Videolängen
        self.duplicates: set[Path] = set()
        self.reorder: Callable[[list[Item]], list[Item]] = lambda items: items
        self._pixmaps: OrderedDict[str, QPixmap] = OrderedDict()
        self._broken: set[str] = set()
        self._rows_by_path: dict[str, list[int]] | None = None
        self._tiles: dict[int, Tile] = {}
        self._resolved: dict[str, Path] = {}
        self._blurs: OrderedDict[str, QPixmap] = OrderedDict()
        if loader is not None:
            loader.loaded.connect(self._on_loaded)
            loader.failed.connect(self._on_failed)

    # --- Daten ------------------------------------------------------------------------

    def set_project(self, project: Project) -> None:
        self.beginResetModel()
        self.project = project
        self._resolved.clear()
        self._invalidate()
        self.endResetModel()

    def _invalidate(self) -> None:
        self._rows_by_path = None
        self._tiles.clear()

    def items(self) -> list[Item]:
        return self.project.items

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.project.items)

    def thumb_source(self, item: Item) -> Path | None:
        if not item.path:
            return None
        src = self._resolved.get(item.path)
        if src is None:
            src = self._resolved[item.path] = self.project.resolve(item.path)
        return src

    def tile(self, row: int) -> Tile:
        """Kacheldaten der Zeile; schneller Weg für den Delegate (ohne Qt-Rollen)."""
        t = self._tiles.get(row)
        if t is None:
            item = self.project.items[row]
            src = self.thumb_source(item)
            key = str(src) if src is not None and media_kind(src) is not None else None
            t = Tile(item_label(item), self.subtitle(item), item.kind, is_chapter_title(item),
                     src is not None and src in self.duplicates,
                     item.damage is not None and not item.damage_minor, item.damage_minor,
                     item.background, key, item.fit)
            self._tiles[row] = t
        return t

    def tile_pixmap(self, tile: Tile) -> QPixmap | None:
        """Vorschaubild aus dem Speicher; fehlt es, wird es im Hintergrund angefordert."""
        key = tile.thumb_key
        if key is None:
            return None
        pm = self._pixmaps.get(key)
        if pm is not None:
            self._pixmaps.move_to_end(key)
            return pm
        if key not in self._broken and self.loader is not None:
            self.loader.request(Path(key))
        return None

    def subtitle(self, item: Item) -> str:
        kind = KIND_LABELS[item.kind]
        if is_chapter_title(item):
            kind = "Zwischentitel"
        if item.duration is not None:
            return f"{kind} · {format_seconds(item.duration)}"
        if item.kind == "video":
            assert item.path is not None
            secs = self.clip_seconds.get(self.thumb_source(item))  # type: ignore[arg-type]
            if secs is not None:
                return f"{kind} · {format_seconds(max(0.0, secs - item.start))}"
            return kind
        return f"{kind} · {format_seconds(self.project.settings.photo_duration)}"

    def data(self, index: QModelIndex | QPersistentModelIndex,
             role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or not 0 <= index.row() < len(self.project.items):
            return None
        item = self.project.items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return item_label(item)
        if role == self.SubtitleRole:
            return self.subtitle(item)
        if role == Qt.ItemDataRole.DecorationRole:
            return self.tile_pixmap(self.tile(index.row()))
        if role == Qt.ItemDataRole.ToolTipRole:
            return self.tooltip(item)
        if role == self.KindRole:
            return item.kind
        if role == self.ChapterRole:
            return is_chapter_title(item)
        if role == self.PathRole:
            return item.path
        if role == self.DuplicateRole:
            src = self.thumb_source(item)
            return src is not None and src in self.duplicates
        if role == self.BackgroundRole:
            return item.background
        return None

    def tooltip(self, item: Item) -> str:
        lines = [item_label(item), self.subtitle(item)]
        if item.path:
            lines.append(str(self.project.resolve(item.path)))
        if item.taken:
            lines.append(f"Aufgenommen: {item.taken.replace('T', ' ')}")
        if item.text and item.text.text.strip() and item.kind != "title":
            lines.append(f"Text: {item.text.text.strip()}")
        src = self.thumb_source(item)
        if src is not None and src in self.duplicates:
            lines.append("Doppelt vorhanden (gleicher Inhalt wie eine andere Datei)")
        if item.damage_minor:
            lines.append(MINOR_DAMAGE_TIP)
        elif item.damage is not None:
            lines.append(f"Beschädigt: {item.damage}, {readable_text(item.damage_readable)}")
            lines.append("Wird trotzdem verwendet (lesbarer Teil)" if not item.skip_damaged
                         else "Wird beim Export übersprungen")
        return "\n".join(lines)


    def blur_pixmap(self, tile: Tile, pm: QPixmap) -> QPixmap:
        """Stark verkleinertes Vorschaubild; groß gezeichnet ergibt es den unscharfen
        Hintergrund des Formatmodus „blur“."""
        key = tile.thumb_key or ""
        small = self._blurs.get(key)
        if small is None:
            small = pm.scaledToWidth(BLUR_W, Qt.TransformationMode.SmoothTransformation)
            self._blurs[key] = small
            while len(self._blurs) > PIXMAP_CACHE:
                self._blurs.popitem(last=False)
        return small

    def _rows_for(self, key: str) -> list[int]:
        if self._rows_by_path is None:
            rows: dict[str, list[int]] = {}
            for i, it in enumerate(self.project.items):
                src = self.thumb_source(it)
                if src is not None:
                    rows.setdefault(str(src), []).append(i)
            self._rows_by_path = rows
        return self._rows_by_path.get(key, [])

    def _on_loaded(self, key: str, img: QImage) -> None:
        self._blurs.pop(key, None)
        self._pixmaps[key] = QPixmap.fromImage(img)
        self._pixmaps.move_to_end(key)
        while len(self._pixmaps) > PIXMAP_CACHE:
            self._pixmaps.popitem(last=False)
        self._refresh(key)

    def _on_failed(self, key: str) -> None:
        self._broken.add(key)
        self._refresh(key)

    def _refresh(self, key: str) -> None:
        for r in self._rows_for(key):
            idx = self.index(r)
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])

    def refresh_rows(self, rows: list[int] | None = None) -> None:
        """Nach Änderungen im Eigenschaftenbereich neu zeichnen."""
        if not self.project.items:
            return
        if rows is None:
            self._tiles.clear()
            self.dataChanged.emit(self.index(0), self.index(len(self.project.items) - 1))
            return
        for r in rows:
            self._tiles.pop(r, None)
            idx = self.index(r)
            self.dataChanged.emit(idx, idx)

    def replace_items(self, items: list[Item]) -> None:
        self.beginResetModel()
        self.project.items = items
        self._invalidate()
        self.endResetModel()

    # --- Drag & Drop --------------------------------------------------------------

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.isValid():
            return base | Qt.ItemFlag.ItemIsDragEnabled
        return Qt.ItemFlag.ItemIsDropEnabled

    def supportedDropActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction | Qt.DropAction.CopyAction

    def mimeTypes(self) -> list[str]:
        return [MIME_ROWS, "text/uri-list"]

    def mimeData(self, indexes: list[QModelIndex]) -> QMimeData:  # type: ignore[override]
        rows = sorted({i.row() for i in indexes if i.isValid()})
        mime = QMimeData()
        mime.setData(MIME_ROWS, QByteArray(",".join(map(str, rows)).encode("ascii")))
        return mime

    @staticmethod
    def rows_from_mime(mime: QMimeData) -> list[int]:
        raw = bytes(mime.data(MIME_ROWS).data()).decode("ascii", "ignore")
        return [int(r) for r in raw.split(",") if r.strip().isdigit()]

    def move_rows(self, rows: list[int], target: int) -> list[int]:
        """Verschiebt Elemente vor die Zeile target. Liefert die neuen Zeilen der verschobenen
        Elemente (nach dem Neusetzen der Kapitel)."""
        items = self.project.items
        moved = [items[r] for r in sorted(set(rows)) if 0 <= r < len(items)]
        if not moved:
            return []
        new = self.reorder(move_items(items, rows, target))
        self.replace_items(new)
        ids = {id(it) for it in moved}
        new_rows = [i for i, it in enumerate(new) if id(it) in ids]
        self.rowsMovedByUser.emit(new_rows)
        return new_rows


def fit_rect(src_w: float, src_h: float, frame: QRectF, cover: bool) -> QRectF:
    """Rechteck für ein Bild im Rahmen: ganz sichtbar (cover=False) oder füllend."""
    scale = (max if cover else min)(frame.width() / src_w, frame.height() / src_h)
    w, h = src_w * scale, src_h * scale
    return QRectF(frame.center().x() - w / 2, frame.center().y() - h / 2, w, h)


def rounded(frame: QRect) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(QRectF(frame), RADIUS, RADIUS)
    return path


def paint_fit(painter: QPainter, frame: QRect, pm: QPixmap, fit: str,
              blur: Callable[[], QPixmap]) -> None:
    """Vorschaubild im 16:9-Rahmen wie im Export: blur, black oder crop."""
    size = pm.deviceIndependentSize()
    f = QRectF(frame)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setClipPath(rounded(frame))
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    if fit == "crop":
        painter.drawPixmap(fit_rect(size.width(), size.height(), f, True), pm,
                           QRectF(pm.rect()))
    else:
        if fit == "blur":
            small = blur()
            painter.drawPixmap(fit_rect(size.width(), size.height(), f, True), small,
                               QRectF(small.rect()))
            painter.fillRect(frame, DARKEN)
        else:
            painter.fillRect(frame, BLACK)
        painter.drawPixmap(fit_rect(size.width(), size.height(), f, False), pm,
                           QRectF(pm.rect()))
    painter.restore()


class TileDelegate(QStyledItemDelegate):
    """Kachel: Vorschaubild (16:9, im Formatmodus des Elements) oben, zwei Textzeilen
    darunter. Zwischentitel und Titelkarten werden als Farbfläche mit Text gezeichnet."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.thumb_w = ZOOM_DEFAULT

    @property
    def thumb_h(self) -> int:
        return round(self.thumb_w * 9 / 16)

    def tile_size(self, line_height: int) -> QSize:
        return QSize(self.thumb_w + 16, self.thumb_h + 2 * line_height + 20)

    def sizeHint(self, option: QStyleOptionViewItem,
                 index: QModelIndex | QPersistentModelIndex) -> QSize:
        return self.tile_size(option.fontMetrics.height())  # type: ignore[attr-defined]

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex | QPersistentModelIndex) -> None:
        model = index.model()
        if not isinstance(model, TimelineModel):
            return
        # Ein Zugriff pro Kachel: beim schnellen Scrollen zählt jede Python-Zeile
        tile = model.tile(index.row())
        pm = model.tile_pixmap(tile)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r: QRect = option.rect.adjusted(4, 4, -4, -4)  # type: ignore[attr-defined]
        pal: QPalette = option.palette  # type: ignore[attr-defined]

        tw, th = self.thumb_w, self.thumb_h
        thumb = QRect(r.x() + (r.width() - tw) // 2, r.y() + 4, tw, th)
        if pm is not None:
            paint_fit(painter, thumb, pm, tile.fit, lambda: model.blur_pixmap(tile, pm))
        elif tile.kind == "title" and tile.thumb_key is None:
            painter.fillPath(rounded(thumb), QColor(tile.background))
            painter.setPen(TITLE_TEXT)
            f = QFont(option.font)  # type: ignore[attr-defined]
            f.setBold(True)
            painter.setFont(f)
            painter.drawText(thumb.adjusted(6, 6, -6, -6),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, tile.label)
            painter.setFont(option.font)  # type: ignore[attr-defined]
        else:
            painter.fillPath(rounded(thumb), color("input"))
            painter.setPen(color("text"))
            painter.drawText(thumb, Qt.AlignmentFlag.AlignCenter,
                             "Video" if tile.kind == "video" else "…")
        if tile.kind == "video":
            # Kleines Abspielzeichen als Kennzeichen für Videos
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color("bg", 220))
            badge = QRect(thumb.x() + 4, thumb.bottom() - 22, 26, 18)
            painter.drawRoundedRect(badge, 3, 3)
            painter.setBrush(color("text"))
            cx, cy = badge.center().x(), badge.center().y()
            painter.drawPolygon(QPolygonF([QPointF(cx - 4, cy - 5), QPointF(cx - 4, cy + 5),
                                           QPointF(cx + 5, cy)]))
        if tile.damaged:
            # Warnsymbol und Text oben links (Farbe neutral, wie die übrigen Hinweise)
            fm = option.fontMetrics  # type: ignore[attr-defined]
            painter.setPen(color("text"))
            painter.setBrush(color("bg"))
            badge = QRect(thumb.x() + 4, thumb.y() + 4,
                          fm.horizontalAdvance(DAMAGED_TEXT) + 26, 18)
            painter.drawRoundedRect(badge, 3, 3)
            painter.drawPixmap(badge.x() + 4, badge.y() + 2, icon("warning").pixmap(14, 14))
            painter.drawText(badge.adjusted(20, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter,
                             DAMAGED_TEXT)
        elif tile.slightly_damaged:
            # Dezentes Hinweissymbol ohne Text, gedämpft; Erklärung im Tooltip
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color("bg", 160))
            badge = QRect(thumb.x() + 4, thumb.y() + 4, 18, 18)
            painter.drawRoundedRect(badge, 3, 3)
            painter.drawPixmap(badge.x() + 2, badge.y() + 2,
                               icon("info", "muted").pixmap(14, 14))
        if tile.duplicate:
            painter.setPen(color("text"))
            painter.setBrush(color("bg"))
            badge = QRect(thumb.right() - 60, thumb.y() + 4, 56, 18)
            painter.drawRoundedRect(badge, 3, 3)
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, "doppelt")
        if option.state & QStyle.StateFlag.State_Selected:  # type: ignore[attr-defined]
            # Auswahl als blauer Rand um die ganze Kachel, ohne Füllung
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(color("accent"), 2))
            painter.drawRoundedRect(QRectF(r).adjusted(1, 1, -1, -1), RADIUS, RADIUS)

        painter.setPen(pal.color(QPalette.ColorRole.Text))
        fm = option.fontMetrics  # type: ignore[attr-defined]
        text_r = QRect(r.x() + 4, thumb.bottom() + 4, r.width() - 8, fm.height())
        painter.drawText(text_r, Qt.AlignmentFlag.AlignLeft,
                         fm.elidedText(tile.label, Qt.TextElideMode.ElideMiddle, text_r.width()))
        sub_r = text_r.translated(0, fm.height())
        painter.setPen(pal.color(QPalette.ColorRole.PlaceholderText))
        painter.drawText(sub_r, Qt.AlignmentFlag.AlignLeft,
                         fm.elidedText(tile.subtitle, Qt.TextElideMode.ElideRight, sub_r.width()))
        painter.restore()


class TimelineView(QListView):
    """Kacheln in Reihen; Drag & Drop verschiebt, Dateien aus dem Explorer werden importiert."""

    filesDropped = Signal(list)  # list[str]
    deleteRequested = Signal()
    zoomChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(True)
        self.setLayoutMode(QListView.LayoutMode.SinglePass)
        self.setSpacing(2)
        self.setItemDelegate(TileDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(ZOOM_DEFAULT // 3)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.setMovement(QListView.Movement.Snap)

    @property
    def tile_width(self) -> int:
        delegate = self.itemDelegate()
        assert isinstance(delegate, TileDelegate)
        return delegate.thumb_w

    def set_tile_width(self, width: int) -> None:
        """Kachelgröße ändern (Zoom). Die markierte Kachel bleibt im Blick."""
        width = max(ZOOM_MIN, min(ZOOM_MAX, int(width)))
        delegate = self.itemDelegate()
        assert isinstance(delegate, TileDelegate)
        if width == delegate.thumb_w:
            return
        current = self.currentIndex()
        delegate.thumb_w = width
        # Gleich große Kacheln merkt sich die Ansicht; neu messen lassen
        self.setUniformItemSizes(False)
        self.setUniformItemSizes(True)
        self.doItemsLayout()
        self.verticalScrollBar().setSingleStep(max(16, width // 3))
        if current.isValid():
            self.scrollTo(current, QAbstractItemView.ScrollHint.PositionAtCenter)
        self.zoomChanged.emit(width)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Strg + Mausrad: Kacheln größer oder kleiner
            step = 16 if event.angleDelta().y() > 0 else -16
            self.set_tile_width(self.tile_width + step)
            event.accept()
            return
        super().wheelEvent(event)

    def _on_scroll(self, _value: int) -> None:
        model = self.model()
        if isinstance(model, TimelineModel) and model.loader is not None:
            model.loader.pause()

    def timeline_model(self) -> TimelineModel:
        model = self.model()
        assert isinstance(model, TimelineModel)
        return model

    def selected_rows(self) -> list[int]:
        return sorted(i.row() for i in self.selectionModel().selectedIndexes())

    def select_rows(self, rows: list[int]) -> None:
        sel = QItemSelection()
        model = self.model()
        for r in rows:
            idx = model.index(r, 0)
            sel.select(idx, idx)
        flags = QItemSelectionModel.SelectionFlag.ClearAndSelect
        self.selectionModel().select(sel, flags)
        if rows:
            self.selectionModel().setCurrentIndex(model.index(rows[0], 0),
                                                  QItemSelectionModel.SelectionFlag.NoUpdate)
            self.scrollTo(model.index(rows[0], 0))

    def drop_target_row(self, pos: QPoint) -> int:
        """Zeile, vor die beim Loslassen an pos eingefügt wird."""
        idx = self.indexAt(pos)
        n = self.model().rowCount()
        if not idx.isValid():
            return n
        rect = self.visualRect(idx)
        return idx.row() + (1 if pos.x() > rect.center().x() else 0)

    def startDrag(self, supportedActions: Qt.DropAction) -> None:
        """Eigenes Ziehen: das Verschieben erledigt dropEvent über das Modell. Die
        Standardumsetzung würde nach einem Verschieben die Quellzeilen zusätzlich löschen."""
        indexes = self.selectionModel().selectedIndexes()
        if not indexes:
            return
        drag = QDrag(self)
        drag.setMimeData(self.model().mimeData(indexes))
        pm = indexes[0].data(Qt.ItemDataRole.DecorationRole)
        if isinstance(pm, QPixmap) and not pm.isNull():
            drag.setPixmap(pm.scaledToWidth(80, Qt.TransformationMode.SmoothTransformation))
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime = event.mimeData()
        if mime.hasFormat(MIME_ROWS) or mime.hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        mime = event.mimeData()
        if mime.hasFormat(MIME_ROWS):
            super().dragMoveEvent(event)
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        elif mime.hasUrls():
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        mime = event.mimeData()
        pos = event.position().toPoint()
        if mime.hasFormat(MIME_ROWS):
            rows = TimelineModel.rows_from_mime(mime)
            new_rows = self.timeline_model().move_rows(rows, self.drop_target_row(pos))
            self.select_rows(new_rows)
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            self.setState(QAbstractItemView.State.NoState)
            self.viewport().update()
            return
        if mime.hasUrls():
            files = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
            if files:
                self.filesDropped.emit(files)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return
        event.ignore()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Delete:
            self.deleteRequested.emit()
            return
        super().keyPressEvent(event)
