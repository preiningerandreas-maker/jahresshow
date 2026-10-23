"""Lizenztexte aller mitgelieferten Teile in den Ordner „Lizenzen“ des Pakets legen.

Aufruf (aus build.ps1): python packaging/collect_licenses.py <dist/Jahresshow> <ffmpeg.exe>
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent


def dist_file(package: str, name: str) -> Path:
    """Lizenzdatei aus den Paket-Metadaten (dist-info) eines installierten Pakets."""
    files = metadata.distribution(package).files or []
    for f in files:
        if f.name == name:
            return Path(str(f.locate()))
    raise FileNotFoundError(f"{name} fehlt in den Metadaten von {package}")


def ffmpeg_notice(ffmpeg: Path) -> str:
    def run(*args: str) -> str:
        return subprocess.run([str(ffmpeg), "-hide_banner", *args], capture_output=True,
                              encoding="utf-8", errors="replace", check=True).stdout

    version = run("-version").splitlines()[0]
    return (
        f"{version}\n\n"
        "Mitgeliefert als ffmpeg.exe und ffprobe.exe (unverändert, Build von gyan.dev).\n"
        "Lizenz: GPL Version 3 oder später, siehe GPL-3.0.txt.\n"
        "Quelltext: https://ffmpeg.org/download.html und https://www.gyan.dev/ffmpeg/builds/\n"
        "(dort zu jedem Build die Versionsangabe und die verwendeten Bibliotheken).\n\n"
        "Lizenzhinweis von ffmpeg -L:\n\n" + run("-L")
    )


def collect(target: Path, ffmpeg: Path) -> list[Path]:
    out = target / "Lizenzen"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    copies = {
        "GPL-3.0.txt": HERE / "licenses" / "GPL-3.0.txt",
        "GPL-2.0.txt": HERE / "licenses" / "GPL-2.0.txt",
        "LGPL-3.0.txt": HERE / "licenses" / "LGPL-3.0.txt",
        "Python.txt": Path(sys.base_prefix) / "LICENSE.txt",
        "Pillow.txt": dist_file("pillow", "LICENSE"),
        "pillow-heif.txt": dist_file("pillow-heif", "LICENSE.txt"),
        "pillow-heif-Bibliotheken.txt": dist_file("pillow-heif", "LICENSES_bundled.txt"),
        "PyInstaller.txt": dist_file("pyinstaller", "COPYING.txt"),
        "Noto-Sans-OFL.txt": ROOT / "slideshow" / "assets" / "fonts" / "OFL.txt",
        "Fluent-UI-System-Icons-MIT.txt": ROOT / "slideshow" / "assets" / "icons" / "LICENSE.txt",
    }
    for name, src in copies.items():
        shutil.copyfile(src, out / name)
    (out / "FFmpeg.txt").write_text(ffmpeg_notice(ffmpeg), encoding="utf-8")
    pyside = metadata.version("PySide6")
    (out / "Qt-PySide6.txt").write_text(
        f"Qt for Python (PySide6, shiboken6) {pyside} mit den Qt-Bibliotheken.\n"
        "Lizenz: LGPL Version 3, siehe LGPL-3.0.txt und GPL-3.0.txt.\n"
        "Die Qt-Bibliotheken liegen als einzelne DLL-Dateien im Ordner _internal und können\n"
        "durch andere Versionen ersetzt werden.\n"
        f"Quelltext: https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v{pyside}\n"
        "und https://download.qt.io/official_releases/qt/\n",
        encoding="utf-8")
    (out / "UEBERSICHT.txt").write_text(
        "Jahresshow enthält folgende Fremdsoftware:\n\n"
        "- FFmpeg (ffmpeg.exe, ffprobe.exe): GPL 3, siehe FFmpeg.txt\n"
        "- Qt for Python / Qt: LGPL 3, siehe Qt-PySide6.txt\n"
        "- Python: PSF-Lizenz, siehe Python.txt\n"
        "- Pillow: MIT-CMU, siehe Pillow.txt\n"
        "- pillow-heif mit libheif, libde265 (LGPL 3) und x265 (GPL 2), siehe\n"
        "  pillow-heif.txt und pillow-heif-Bibliotheken.txt\n"
        "- PyInstaller-Startprogramm: GPL 2 mit Ausnahme für erzeugte Programme, siehe\n"
        "  PyInstaller.txt\n"
        "- Schrift Noto Sans: SIL Open Font License 1.1, siehe Noto-Sans-OFL.txt\n"
        "- Symbole Fluent UI System Icons (Microsoft): MIT, siehe\n"
        "  Fluent-UI-System-Icons-MIT.txt\n",
        encoding="utf-8")
    return sorted(out.iterdir())


if __name__ == "__main__":
    for p in collect(Path(sys.argv[1]), Path(sys.argv[2])):
        print(p.name)
