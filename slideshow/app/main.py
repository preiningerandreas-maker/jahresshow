"""Programmstart der Oberfläche: nur eine Instanz, deutsche Standarddialoge, Fenstergröße
passend zum Bildschirm (auch 1366×768 oder 150 % Skalierung)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
from PySide6.QtWidgets import QApplication, QMessageBox

from slideshow import __version__
from slideshow.app.mainwindow import APP_NAME, MainWindow
from slideshow.app.single import SingleInstance

SMALL_SCREEN = (1500, 900)  # darunter startet das Fenster maximiert


def _translate(app: QApplication) -> None:
    QLocale.setDefault(QLocale(QLocale.Language.German, QLocale.Country.Germany))
    translator = QTranslator(app)
    path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if translator.load("qtbase_de", path):
        app.installTranslator(translator)


def place_window(win: MainWindow) -> None:
    screen = win.screen().availableGeometry()
    if screen.width() < SMALL_SCREEN[0] or screen.height() < SMALL_SCREEN[1]:
        win.showMaximized()
    else:
        win.resize(min(1600, screen.width() - 80), min(1000, screen.height() - 80))
        win.show()


def run_gui(files: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = QApplication.instance() or QApplication(sys.argv[:1])
    assert isinstance(app, QApplication)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    _translate(app)

    instance = SingleInstance()
    if not instance.acquire():
        if instance.notify_running(files or []):
            return 0  # die laufende Instanz holt ihr Fenster nach vorne
        QMessageBox.warning(None, APP_NAME, "Jahresshow läuft bereits, reagiert aber nicht. "
                            "Bitte warten oder das andere Fenster schließen.")
        return 1

    win = MainWindow()
    instance.activated.connect(win.bring_to_front)
    place_window(win)
    if files:
        win.on_files_dropped([str(Path(f)) for f in files])
    try:
        return app.exec()
    finally:
        instance.release()
