"""Das fertige Windows-Paket (dist/Flashback, gebaut mit build.ps1).

Die Tests starten die echte exe in einer frischen PowerShell ohne venv und ohne Python im
PATH. Ohne gebautes Paket werden sie übersprungen."""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import TESTS
from mediacheck import assert_working_format, ffprobe

ROOT = TESTS.parent
DIST = ROOT / "dist" / "Flashback"
EXE = DIST / "Flashback.exe"
SIZE_LIMIT_MB = 500
# FFmpeg als „shared“-Build: ffmpeg.exe und ffprobe.exe teilen sich diese Bibliotheken
FFMPEG_DLLS = ("avcodec", "avdevice", "avfilter", "avformat", "avutil", "swresample",
               "swscale")

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(sys.platform != "win32", reason="nur unter Windows"),
    pytest.mark.skipif(not EXE.is_file(), reason="Paket nicht gebaut (build.ps1)"),
]

if sys.platform == "win32":
    from winproc import (
        alive,
        close_window,
        descendants,
        exit_code,
        invoke_button,
        powershell,
        ps_quote,
        visible_windows,
        wait_for,
    )


def _no_python(entry: str) -> bool:
    d = Path(entry)
    if ".venv" in d.parts or "python" in d.name.lower():
        return False
    return not any((d / exe).exists() for exe in ("python.exe", "python3.exe", "py.exe"))


def clean_env(tmp_path: Path) -> dict[str, str]:
    """Umgebung wie bei einem normalen Nutzer: kein venv, kein Python im PATH, eigener
    Cache-Ordner (LOCALAPPDATA) und eigene Instanz-Kennung."""
    drop = {"VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONHASHSEED",
            "QT_QPA_PLATFORM", "QT_PLUGIN_PATH", "QT_SCALE_FACTOR", "FLASHBACK_FFMPEG",
            "FLASHBACK_FFPROBE", "FLASHBACK_INSTANCE_KEY", "PYTEST_CURRENT_TEST"}
    env = {k: v for k, v in os.environ.items() if k.upper() not in drop}
    path = [p for p in env.get("PATH", "").split(os.pathsep) if p and _no_python(p)]
    env["PATH"] = os.pathsep.join(path)
    assert shutil.which("python", path=env["PATH"]) is None
    assert shutil.which("py", path=env["PATH"]) is None
    appdata = tmp_path / "appdata"
    appdata.mkdir(exist_ok=True)
    env["LOCALAPPDATA"] = str(appdata)
    env["FLASHBACK_INSTANCE_KEY"] = f"pkgtest-{os.getpid()}-{time.monotonic_ns()}"
    return env


def make_photos(folder: Path, n: int) -> list[Path]:
    """n verschiedene Fotos (keine Duplikate) mit Aufnahmedatum über mehrere Monate."""
    from PIL import Image

    folder.mkdir(parents=True)
    out = []
    for i in range(n):
        img = Image.new("RGB", (640, 480), ((i * 47) % 256, (i * 91) % 256, (i * 13) % 256))
        img.paste((255 - i * 5, i * 5, 128), (0, 0, 320, 240 - i * 4))
        exif = Image.Exif()
        exif.get_ifd(0x8769)[0x9003] = f"2026:{i % 12 + 1:02d}:{i % 28 + 1:02d} 12:00:00"
        p = folder / f"Foto {i + 1:02d} Größe.jpg"
        img.save(p, "JPEG", quality=85, exif=exif.tobytes())
        out.append(p)
    return out


def start_exe(env: dict[str, str], *args: str) -> int:
    """exe aus einer frischen PowerShell starten, liefert die Prozess-ID."""
    arglist = ", ".join(ps_quote(f'"{a}"') for a in args)
    script = (f"$p = Start-Process -FilePath {ps_quote(str(EXE))} "
              + (f"-ArgumentList {arglist} " if args else "") + "-PassThru; $p.Id")
    res = powershell(script, env=env, timeout=120)
    assert res.returncode == 0, res.stderr
    return int(res.stdout.strip().splitlines()[-1])


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)


def test_package_contents() -> None:
    for name in ("Flashback.exe", "ffmpeg.exe", "ffprobe.exe", "README.txt"):
        assert (DIST / name).is_file(), name
    internal = DIST / "_internal" / "slideshow" / "assets"
    assert (internal / "fonts" / "NotoSans.ttf").is_file()
    assert (internal / "fonts" / "OFL.txt").is_file()
    icons = sorted(p.name for p in (ROOT / "slideshow" / "assets" / "icons").iterdir())
    assert sorted(p.name for p in (internal / "icons").iterdir()) == icons
    licenses = {p.name for p in (DIST / "Lizenzen").iterdir()}
    assert {"UEBERSICHT.txt", "FFmpeg.txt", "Qt-PySide6.txt", "Python.txt", "Pillow.txt",
            "pillow-heif.txt", "pillow-heif-Bibliotheken.txt", "PyInstaller.txt",
            "Noto-Sans-OFL.txt", "Fluent-UI-System-Icons-MIT.txt", "GPL-3.0.txt",
            "GPL-2.0.txt", "LGPL-3.0.txt"} <= licenses
    ffmpeg_notice = (DIST / "Lizenzen" / "FFmpeg.txt").read_text(encoding="utf-8")
    assert "ffmpeg version" in ffmpeg_notice and "GNU General Public License" in ffmpeg_notice
    # Jede mitgelieferte FFmpeg-DLL steht mit Version und Lizenz in FFmpeg.txt; dazu der
    # Lizenztext und die Bibliotheksliste des Builds unverändert
    dlls = sorted(p.name for p in DIST.glob("*.dll"))
    assert sorted(re.sub(r"-\d+\.dll$", "", n) for n in dlls) == sorted(FFMPEG_DLLS)
    for dll in dlls:
        assert re.search(rf"^{re.escape(dll)}\s+lib\w+ \d+\.\s*\d+\.\d+ .*GPL 3", ffmpeg_notice,
                         re.MULTILINE), dll
    assert {"FFmpeg-LICENSE.txt", "FFmpeg-Build-gyan.txt"} <= licenses
    assert "GNU GENERAL PUBLIC LICENSE" in (DIST / "Lizenzen" / "FFmpeg-LICENSE.txt").read_text(
        encoding="utf-8")
    build = (DIST / "Lizenzen" / "FFmpeg-Build-gyan.txt").read_text(encoding="utf-8")
    for lib in ("libx264", "libzimg", "libvpl", "libx265"):
        assert lib in build, lib
    assert "external libraries' versions" in build
    assert not (DIST / "ffplay.exe").exists()
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in (DIST / "Lizenzen" / "LGPL-3.0.txt").read_text(
        encoding="utf-8")


def test_packaged_ffmpeg_has_required_features(tmp_path: Path) -> None:
    """Das mitgelieferte FFmpeg (shared-Build mit den DLLs daneben) läuft ohne Hilfe von
    außen und kann alles, was Flashback braucht."""
    env = clean_env(tmp_path)
    env["PATH"] = os.pathsep.join(p for p in env["PATH"].split(os.pathsep)
                                  if not (Path(p) / "ffmpeg.exe").exists())

    def run(exe: str, *args: str) -> str:
        res = subprocess.run([str(DIST / exe), "-hide_banner", *args], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", env=env,
                             cwd=tmp_path, timeout=60, check=False)
        assert res.returncode == 0, res.stderr
        return res.stdout

    version = run("ffmpeg.exe", "-version")
    assert "--enable-shared" in version and "--enable-libzimg" in version
    assert "--enable-libx264" in version and "--enable-libvpl" in version
    assert "--enable-shared" in run("ffprobe.exe", "-version")

    def listed(kind: str) -> set[str]:
        return {line.split()[1] for line in run("ffmpeg.exe", f"-{kind}").splitlines()
                if len(line.split()) > 1 and line.startswith(" ")}

    filters = listed("filters")
    assert {"zscale", "tonemap", "xfade", "acrossfade", "overlay", "boxblur"} <= filters
    assert {"h264_qsv", "libx264", "h264_nvenc", "h264_amf", "aac", "flac"} <= listed("encoders")
    assert {"hevc", "h264", "mjpeg", "png", "aac"} <= listed("decoders")


def test_package_size_and_qt_modules() -> None:
    files = [p for p in DIST.rglob("*") if p.is_file()]
    size_mb = sum(p.stat().st_size for p in files) / 1024**2
    names = {p.relative_to(DIST).as_posix() for p in files}
    qt = "_internal/PySide6/"
    for needed in ("Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6Network.dll",
                   "Qt6Svg.dll", "plugins/platforms/qwindows.dll",
                   "plugins/iconengines/qsvgicon.dll", "plugins/imageformats/qjpeg.dll",
                   "translations/qtbase_de.qm"):
        assert qt + needed in names, needed
    unwanted = re.compile(r"Qt6(Quick|Qml|Pdf|OpenGL|VirtualKeyboard|WebEngine|Multimedia|3D)"
                          r"|opengl32sw|qdirect2d|platforminputcontexts|/tls/", re.IGNORECASE)
    assert not [n for n in names if unwanted.search(n)]
    translations = [n for n in names if "/translations/" in n]
    assert all(re.search(r"/qt(base)?_de\.qm$", n) for n in translations), translations
    assert size_mb < SIZE_LIMIT_MB, f"Paket {size_mb:.1f} MB, Grenze {SIZE_LIMIT_MB} MB"


def test_readme_for_end_users() -> None:
    text = (DIST / "README.txt").read_text(encoding="utf-8")
    # read_text liest die CRLF-Datei mit \n als Zeilenende
    assert text.startswith("Flashback\n=========\n\nVersion 2.0, November 2026\n"
                           "© 2026 Andreas Preininger\n")
    for needed in ("Flashback.exe", "Windows 10", "Windows 11", "8 GB Arbeitsspeicher",
                   "SmartScreen", "Weitere Informationen", "Trotzdem ausführen", "Lizenzen"):
        assert needed in text, needed
    assert text == (ROOT / "packaging" / "README.txt").read_text(encoding="utf-8")


def file_version_strings(path: Path) -> dict[str, str]:
    """Datei-Eigenschaften (Details) einer exe, wie der Explorer sie zeigt."""
    import ctypes

    version = ctypes.windll.version
    size = version.GetFileVersionInfoSizeW(str(path), None)
    assert size, "keine Versionsangaben in der exe"
    data = ctypes.create_string_buffer(size)
    assert version.GetFileVersionInfoW(str(path), 0, size, data)
    out = {}
    for key in ("ProductName", "FileDescription", "LegalCopyright", "CompanyName",
                "ProductVersion", "OriginalFilename"):
        ptr, length = ctypes.c_wchar_p(), ctypes.c_uint()
        if version.VerQueryValueW(data, f"\\StringFileInfo\\040704B0\\{key}",
                                  ctypes.byref(ptr), ctypes.byref(length)):
            out[key] = ptr.value
    return out


def test_exe_properties_show_name_and_copyright() -> None:
    from slideshow import __version__

    props = file_version_strings(EXE)
    assert props == {
        "ProductName": "Flashback",
        "FileDescription": "Flashback – Slideshow aus Fotos und Videos",
        "LegalCopyright": "© 2026 Andreas Preininger",
        "CompanyName": "Andreas Preininger",
        "ProductVersion": f"{__version__} (November 2026)",
        "OriginalFilename": "Flashback.exe",
    }
    assert "© 2026 Andreas Preininger" in (DIST / "Lizenzen" / "UEBERSICHT.txt").read_text(
        encoding="utf-8")


def test_launcher_calls_freeze_support_first() -> None:
    """Ohne freeze_support würde jeder Hilfsprozess ein weiteres Hauptfenster öffnen."""
    tree = ast.parse((ROOT / "packaging" / "launcher.py").read_text(encoding="utf-8"))
    main_block = next(n for n in tree.body if isinstance(n, ast.If))
    first = main_block.body[0]
    assert isinstance(first, ast.Expr) and ast.unparse(first) == \
        "multiprocessing.freeze_support()"


def test_imports_50_photos_one_window_helpers_end(tmp_path: Path) -> None:
    """Gepackte exe in frischer PowerShell starten, 50 Fotos importieren: genau ein
    Hauptfenster, Vorschaubilder entstehen in Hilfsprozessen, nach dem Beenden sind diese
    weg."""
    env = clean_env(tmp_path)
    folder = tmp_path / "Urlaub Wörthersee 2026"
    make_photos(folder, 50)
    thumbs = Path(env["LOCALAPPDATA"]) / "Flashback" / "cache" / "thumbs"
    pid = start_exe(env, str(folder))
    seen: set = set()
    window_counts: list[int] = []
    try:
        def sample() -> list:
            procs = descendants(pid)
            seen.update(procs)
            wins = [w for w in visible_windows({pid} | {p.pid for p in procs}) if w.title]
            window_counts.append(len(wins))
            return wins

        def ready() -> bool:
            wins = sample()
            helpers = [p for p in seen if p.name.lower() == "flashback.exe"]
            n_thumbs = len(list(thumbs.glob("*.jpg"))) if thumbs.is_dir() else 0
            return (len(wins) == 1 and wins[0].title.startswith("*") and n_thumbs >= 5
                    and bool(helpers))

        assert wait_for(ready, 240), (window_counts[-5:], seen)
        for _ in range(12):  # noch 3 s beobachten: es darf kein weiteres Fenster aufgehen
            sample()
            time.sleep(0.25)
        wins = sample()
        assert len(wins) == 1 and wins[0].pid == pid and "Flashback" in wins[0].title
        assert max(window_counts) == 1, window_counts
        # Beide Hilfsprozesse laufen dauerhaft. Ohne freeze_support würden sie sofort mit
        # einem Fehler enden und die Vorschaubilder entstünden ersatzweise in Threads.
        helpers = [p for p in seen if p.name.lower() == "flashback.exe" and alive(p)]
        assert len(helpers) == 2, seen

        close_window(wins[0].hwnd)
        assert invoke_button(pid, "Nicht speichern"), "Rückfrage beim Beenden nicht gefunden"
        assert wait_for(lambda: exit_code(pid) is not None, 60), "Programm endet nicht"
        assert exit_code(pid) == 0
        assert wait_for(lambda: not any(alive(p) for p in seen), 20), \
            [p for p in seen if alive(p)]
    finally:
        kill_tree(pid)


def test_helpers_end_when_main_process_is_killed(tmp_path: Path) -> None:
    """Wie Beenden im Task-Manager: Die Hilfsprozesse folgen dem Hauptprogramm."""
    env = clean_env(tmp_path)
    pid = start_exe(env)
    try:
        assert wait_for(lambda: any(p.name.lower() == "flashback.exe"
                                    for p in descendants(pid)), 120)
        helpers = descendants(pid)
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=True)
        assert wait_for(lambda: not any(alive(p) for p in helpers), 20), \
            [p for p in helpers if alive(p)]
    finally:
        kill_tree(pid)


def test_render_demo_passes_m1_check(synthetic: Path, tmp_path: Path) -> None:
    """Flashback.exe --render tests/demo.json -o …/pkg.mp4 in frischer PowerShell: dieselbe
    ffprobe-Prüfung wie in M1, mit bt709-Farbangaben."""
    from slideshow.core.importer import clip_length
    from slideshow.core.project import FPS, Project

    env = clean_env(tmp_path)
    out = tmp_path / "out" / "pkg.mp4"
    out.parent.mkdir()
    demo = TESTS / "demo.json"
    script = (f"& {ps_quote(str(EXE))} --render {ps_quote(str(demo))} -o {ps_quote(str(out))}"
              " | Out-Host; exit $LASTEXITCODE")
    res = powershell(script, env=env, timeout=1800)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "Fertig:" in res.stdout  # Ausgabe kommt in der PowerShell an

    v, a = assert_working_format(out)  # h264, yuv420p, 30 fps CFR, bt709 gesetzt, AAC 48 kHz
    assert (int(v["width"]), int(v["height"])) == (1920, 1080)
    assert a is not None and a["codec_name"] == "aac"
    expected = Project.load(demo).total_frames(clip_length) / FPS
    duration = float(ffprobe(out)["format"]["duration"])
    assert abs(duration - expected) <= 0.5, (duration, expected)
