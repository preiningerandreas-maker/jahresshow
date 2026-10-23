"""Prüft ein Fenster auf abgeschnittene oder unerreichbare Bedienelemente (für die
Screenshot-Tests bei kleinen Bildschirmen und 150 % Skalierung).

Als Fehler zählt:
- ein sichtbares Bedienelement ragt aus dem Fenster oder aus seinem Elternelement,
- ein Element ist kleiner als sein Mindestplatz (Text würde abgeschnitten),
- Knöpfe der Werkzeugleiste landen im Überlaufmenü, Menüs oder Reiter passen nicht.
Elemente in einem Rollbereich dürfen unten außerhalb liegen (man erreicht sie per
Scrollen), aber nicht seitlich, denn waagerechtes Scrollen ist abgeschaltet."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QMenuBar,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QSlider,
    QTabBar,
    QToolBar,
    QToolButton,
    QWidget,
)

CONTROLS = (QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit, QPlainTextEdit, QSlider,
            QProgressBar, QAbstractItemView, QLabel, QTabBar)


def describe(w: QWidget) -> str:
    name = w.objectName() or ""
    text = ""
    for attr in ("text", "placeholderText", "currentText"):
        fn = getattr(w, attr, None)
        if callable(fn):
            try:
                value = fn()
            except TypeError:
                continue
            if isinstance(value, str) and value:
                text = value.replace("\n", " ")[:40]
                break
    parts = [type(w).__name__]
    if name and not name.startswith("qt_"):
        parts.append(f"#{name}")
    if text:
        parts.append(f"„{text}“")
    return " ".join(parts)


def _rect_in(w: QWidget, ancestor: QWidget) -> QRect:
    return QRect(w.mapTo(ancestor, QPoint(0, 0)), w.size())


def _scroll_area_of(w: QWidget) -> QScrollArea | None:
    p = w.parentWidget()
    while p is not None:
        if isinstance(p, QScrollArea):
            return p
        p = p.parentWidget()
    return None


def _is_internal(w: QWidget) -> bool:
    """Hilfselemente von Qt (Rollbalken-Knöpfe, Zeilen im Spinner usw.) nicht prüfen."""
    if w.objectName().startswith("qt_"):
        return True
    parent = w.parentWidget()
    return isinstance(parent, (QAbstractSpinBox, QComboBox, QTabBar, QAbstractItemView))


def clipped_widgets(window: QWidget) -> list[str]:
    problems: list[str] = []
    win_rect = window.rect()
    for w in window.findChildren(QWidget):
        if not isinstance(w, CONTROLS) or not w.isVisibleTo(window) or _is_internal(w):
            continue
        if isinstance(w, QLabel) and (w.wordWrap() or not w.text()):
            continue  # umbrechende Texte passen sich an
        rect = _rect_in(w, window)
        area = _scroll_area_of(w)
        if area is None:
            if not win_rect.contains(rect):
                problems.append(f"{describe(w)} ragt aus dem Fenster ({rect.getRect()})")
                continue
        else:
            content = area.widget()
            viewport = area.viewport()
            r = _rect_in(w, content)
            if r.left() < 0 or r.right() >= viewport.width() + 1:
                problems.append(f"{describe(w)} ist seitlich abgeschnitten im Rollbereich")
                continue
            if not content.rect().contains(r):
                problems.append(f"{describe(w)} liegt außerhalb des Rollbereichs")
                continue
        # Jedes Elternelement bis zum Fenster (bzw. bis zum Inhalt des Rollbereichs, der oben
        # schon geprüft ist) muss das Element ganz enthalten
        stop = area.widget() if area is not None else window
        parent = w.parentWidget()
        while parent is not None and parent is not stop:
            if not parent.rect().contains(_rect_in(w, parent)):
                problems.append(f"{describe(w)} wird von {describe(parent)} abgeschnitten")
                break
            parent = parent.parentWidget()
        hint = w.minimumSizeHint()
        if isinstance(w, QAbstractItemView | QPlainTextEdit):
            continue  # Listen und Textfelder rollen selbst
        if isinstance(w, QProgressBar) and not w.isTextVisible():
            continue  # schmaler Balken ohne Text: nichts, was abgeschnitten werden könnte
        if hint.isValid() and (w.width() < hint.width() or w.height() < hint.height()):
            problems.append(f"{describe(w)} ist zu klein: {w.width()}×{w.height()}, "
                            f"braucht {hint.width()}×{hint.height()}")
    problems += _toolbar_problems(window)
    problems += _menubar_problems(window)
    problems += _tabbar_problems(window)
    return problems


def _toolbar_problems(window: QWidget) -> list[str]:
    out = []
    for tb in window.findChildren(QToolBar):
        if not tb.isVisibleTo(window):
            continue
        for act in tb.actions():
            if act.isSeparator() or not act.isVisible():
                continue
            btn = tb.widgetForAction(act)
            if btn is None or not btn.isVisibleTo(window):
                out.append(f"Werkzeugleiste: „{act.text()}“ passt nicht (im Überlaufmenü)")
            elif not tb.rect().contains(_rect_in(btn, tb)):
                out.append(f"Werkzeugleiste: „{act.text()}“ ist abgeschnitten")
    return out


def toolbar_button_state(window: QWidget, button: QWidget) -> dict[str, bool]:
    """Liegt ein Knopf der Werkzeugleiste sichtbar im Fenster oder steckt er im Überlaufmenü
    (Pfeil am Ende der Leiste, dort landen Knöpfe, die nicht mehr in die Zeile passen)?"""
    tb = button.parentWidget()
    assert isinstance(tb, QToolBar), "Knopf liegt nicht direkt in einer Werkzeugleiste"
    ext = tb.findChild(QToolButton, "qt_toolbar_ext_button")
    # Qt blendet Knöpfe, die nicht in die Zeile passen, aus und zeigt sie erst nach einem
    # Klick auf den Pfeil
    visible = button.isVisible()
    return {"visible": visible,
            "in_window": visible and window.rect().contains(_rect_in(button, window)),
            "in_toolbar": visible and tb.rect().contains(_rect_in(button, tb)),
            "in_overflow": not visible and ext is not None and ext.isVisibleTo(window)}


def _menubar_problems(window: QWidget) -> list[str]:
    out = []
    bars = window.findChildren(QMenuBar)
    for bar in bars:
        for act in bar.actions():
            if not act.isVisible():
                continue
            geo = bar.actionGeometry(act)
            if geo.isEmpty() or not bar.rect().contains(geo):
                out.append(f"Menü „{act.text()}“ passt nicht in die Menüleiste")
    return out


def _tabbar_problems(window: QWidget) -> list[str]:
    out = []
    for bar in window.findChildren(QTabBar):
        if not bar.isVisibleTo(window):
            continue
        for i in range(bar.count()):
            r = bar.tabRect(i)
            if not bar.rect().contains(r):
                out.append(f"Reiter „{bar.tabText(i)}“ ist abgeschnitten")
            elif r.width() < bar.fontMetrics().horizontalAdvance(bar.tabText(i)):
                out.append(f"Reiter „{bar.tabText(i)}“ ist zu schmal")
    return out
