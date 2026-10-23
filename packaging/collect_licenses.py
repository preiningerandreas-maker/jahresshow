"""Lizenztexte aller mitgelieferten Teile in den Ordner „Lizenzen“ des Pakets legen.

Aufruf (aus build.ps1): python packaging/collect_licenses.py <dist/Flashback> <bin>

<bin> enthält den „shared“-Build von gyan.dev: ffmpeg.exe, ffprobe.exe, die DLLs sowie
LICENSE und README.txt aus dem Archiv.
"""

from __future__ import annotations

import re
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


def ffmpeg_license(configuration: str) -> str:
    """Lizenz der FFmpeg-Bibliotheken nach den Build-Optionen."""
    if "--enable-nonfree" in configuration:
        raise SystemExit("FFmpeg mit --enable-nonfree darf nicht weitergegeben werden.")
    gpl = "--enable-gpl" in configuration
    v3 = "--enable-version3" in configuration
    if gpl:
        return "GPL 3 oder später" if v3 else "GPL 2 oder später"
    return "LGPL 3 oder später" if v3 else "LGPL 2.1 oder später"


def ffmpeg_notice(dist: Path, ffmpeg: Path) -> str:
    def run(*args: str) -> str:
        return subprocess.run([str(ffmpeg), "-hide_banner", *args], capture_output=True,
                              encoding="utf-8", errors="replace", check=True).stdout

    version_text = run("-version")
    lines = version_text.splitlines()
    license_name = ffmpeg_license(version_text)
    # „libavcodec     63.  1.102 / 63.  1.102“ -> {"avcodec": "63.1.102"}
    versions = {}
    for line in lines:
        m = re.match(r"lib(\w+)\s+([\d.\s]+?)\s*/", line)
        if m:
            versions[m.group(1)] = re.sub(r"\s+", "", m.group(2))
    dll_lines = []
    for dll in sorted(dist.glob("*.dll")):
        name = dll.name.rsplit("-", 1)[0]
        if name not in versions:
            raise SystemExit(f"{dll.name} gehört nicht zu FFmpeg ({lines[0]})")
        dll_lines.append(f"{dll.name:<22}lib{name} {versions[name]:<12}{license_name}")
    return (
        f"{lines[0]}\n\n"
        "Mitgeliefert unverändert als „shared“-Build von gyan.dev: ffmpeg.exe, ffprobe.exe\n"
        "und diese Bibliotheken, die sich beide Programme teilen:\n\n"
        + "\n".join(dll_lines) + "\n\n"
        f"Lizenz: {license_name} (gebaut mit --enable-gpl --enable-version3), siehe\n"
        "GPL-3.0.txt und den Lizenztext aus dem Build in FFmpeg-LICENSE.txt.\n"
        "Die in die DLLs eingebauten Bibliotheken (u. a. x264, x265, zimg, libvpl, dav1d)\n"
        "stehen mit ihren Versionen in FFmpeg-Build-gyan.txt (README.txt des Builds).\n"
        "Quelltext: https://ffmpeg.org/download.html und https://www.gyan.dev/ffmpeg/builds/\n"
        "(dort zu jedem Build die Versionsangabe und die verwendeten Bibliotheken).\n\n"
        "Lizenzhinweis von ffmpeg -L:\n\n" + run("-L")
    )


def collect(target: Path, bin_dir: Path) -> list[Path]:
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
        "FFmpeg-LICENSE.txt": bin_dir / "LICENSE",
        "FFmpeg-Build-gyan.txt": bin_dir / "README.txt",
    }
    for name, src in copies.items():
        shutil.copyfile(src, out / name)
    (out / "FFmpeg.txt").write_text(ffmpeg_notice(target, bin_dir / "ffmpeg.exe"),
                                    encoding="utf-8")
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
        "Flashback (© 2026 Andreas Preininger) enthält folgende Fremdsoftware:\n\n"
        "- FFmpeg (ffmpeg.exe, ffprobe.exe und die DLLs av*.dll, sw*.dll im Programm-\n"
        "  ordner): GPL 3, siehe FFmpeg.txt, FFmpeg-LICENSE.txt und FFmpeg-Build-gyan.txt\n"
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
