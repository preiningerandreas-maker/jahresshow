"""Datei im Explorer zeigen: Ordner öffnen und die Datei darin markieren."""

from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

log = logging.getLogger(__name__)


def _shell32() -> ctypes.WinDLL:
    shell32 = ctypes.WinDLL("shell32")
    shell32.ILCreateFromPathW.restype = ctypes.c_void_p
    shell32.ILCreateFromPathW.argtypes = [ctypes.c_wchar_p]
    shell32.ILFree.argtypes = [ctypes.c_void_p]
    shell32.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                                   ctypes.c_void_p, ctypes.c_ulong]
    return shell32


def item_id(path: Path) -> int | None:
    """Windows-Kennung (PIDL) einer Datei; None, wenn Windows sie nicht findet.
    Freigeben mit _shell32().ILFree."""
    return _shell32().ILCreateFromPathW(str(path.resolve())) or None


def reveal_in_explorer(path: Path) -> None:
    """Explorer mit dem Ordner der Datei öffnen und die Datei markieren. Geht das nicht
    (kein Windows, Datei fehlt), wird nur der Ordner geöffnet."""
    if sys.platform == "win32" and path.is_file():
        shell32 = _shell32()
        pidl = item_id(path)
        if pidl is not None:
            try:
                ctypes.windll.ole32.CoInitialize(None)  # in Qt meist schon geschehen
                if shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0) == 0:
                    return
            finally:
                shell32.ILFree(pidl)
        log.warning("Explorer konnte %s nicht markieren, öffne nur den Ordner", path)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
