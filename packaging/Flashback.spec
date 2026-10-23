# PyInstaller-Spezifikation für das Windows-Paket (onedir). Aufruf über build.ps1.
# ffmpeg.exe, ffprobe.exe, Lizenzen und README.txt legt build.ps1 danach in den Ausgabeordner.
import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

from slideshow import APP_NAME, AUTHOR, COPYRIGHT, __version__

ROOT = Path(SPECPATH).parent  # noqa: F821 (von PyInstaller gesetzt)

# Datei-Eigenschaften der exe (Rechtsklick → Eigenschaften → Details)
_numbers = tuple(int(n) for n in __version__.split(".")) + (0,)
VERSION = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_numbers[:4], prodvers=_numbers[:4]),
    kids=[
        StringFileInfo([StringTable("040704B0", [  # Deutsch (Deutschland), Unicode
            StringStruct("CompanyName", AUTHOR),
            StringStruct("FileDescription", f"{APP_NAME} – Slideshow aus Fotos und Videos"),
            StringStruct("FileVersion", __version__),
            StringStruct("InternalName", APP_NAME),
            StringStruct("LegalCopyright", COPYRIGHT),
            StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
            StringStruct("ProductName", APP_NAME),
            StringStruct("ProductVersion", __version__),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0407, 1200])]),
    ],
)

# Qt-Module, die das Programm nicht braucht. Es verwendet nur QtCore, QtGui, QtWidgets,
# QtNetwork (Named Pipe für die Einzelinstanz) und QtSvg (Symbole).
QT_EXCLUDES = [
    f"PySide6.{m}" for m in (
        "Qt3DAnimation", "Qt3DCore", "Qt3DExtras", "Qt3DInput", "Qt3DLogic", "Qt3DRender",
        "QtBluetooth", "QtCharts", "QtConcurrent", "QtDataVisualization", "QtDBus",
        "QtDesigner", "QtGraphs", "QtGraphsWidgets", "QtHelp", "QtHttpServer", "QtLocation",
        "QtMultimedia", "QtMultimediaWidgets", "QtNetworkAuth", "QtNfc", "QtOpenGL",
        "QtOpenGLWidgets", "QtPdf", "QtPdfWidgets", "QtPositioning", "QtPrintSupport",
        "QtQml", "QtQuick", "QtQuick3D", "QtQuickControls2", "QtQuickTest",
        "QtQuickWidgets", "QtRemoteObjects", "QtScxml", "QtSensors", "QtSerialBus",
        "QtSerialPort", "QtSpatialAudio", "QtSql", "QtStateMachine", "QtSvgWidgets",
        "QtTest", "QtTextToSpeech", "QtUiTools", "QtWebChannel", "QtWebEngineCore",
        "QtWebEngineQuick", "QtWebEngineWidgets", "QtWebSockets", "QtWebView", "QtXml",
        "QtAxContainer",
    )
]
# Nur für Entwicklung und Tests; AVIF liest das Programm nicht, SSL braucht es nicht (offline)
DEV_EXCLUDES = ["pytest", "pytestqt", "_pytest", "ruff", "tkinter", "unittest", "pydoc",
                "PIL._avif", "PIL.AvifImagePlugin", "ssl", "_ssl"]

# Dateien, die über Qt-Plugins hereinkommen, aber nicht gebraucht werden: virtuelle Tastatur
# (zieht Qt Quick und QML nach), PDF-Bildformat (Qt Pdf), Netzwerk-Erkennung, TLS,
# Direct2D, Touch-Eingabe (TUIO), Software-OpenGL. Dazu alle Übersetzungen außer Deutsch.
DROP = re.compile(
    r"^PySide6[\\/]("
    r"plugins[\\/](platforminputcontexts|networkinformation|tls|generic)[\\/]"
    r"|plugins[\\/]imageformats[\\/]q(pdf|tiff|webp|icns|tga|wbmp)\.dll$"
    r"|plugins[\\/]platforms[\\/]qdirect2d\.dll$"
    r"|Qt6(Quick|Qml|Pdf|OpenGL|VirtualKeyboard)\w*\.dll$"
    r"|opengl32sw\.dll$"
    r"|translations[\\/](?!qt(base)?_de\.qm$)"
    r")",
    re.IGNORECASE,
)


def keep(toc: list) -> list:
    return [entry for entry in toc if not DROP.match(entry[0])]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=[
        (str(ROOT / "slideshow" / "assets" / "fonts"), "slideshow/assets/fonts"),
        (str(ROOT / "slideshow" / "assets" / "icons"), "slideshow/assets/icons"),
    ],
    hiddenimports=["slideshow.app.main"],  # wird erst beim Start der Oberfläche geladen
    excludes=QT_EXCLUDES + DEV_EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    version=VERSION,
    console=False,  # Fensterprogramm; die Kommandozeile hängt sich an die Konsole an
    upx=False,  # UPX-gepackte Programme lösen oft Fehlalarme von Virenscannern aus
)
coll = COLLECT(  # noqa: F821
    exe,
    keep(a.binaries),
    keep(a.datas),
    upx=False,
    name=APP_NAME,
)
