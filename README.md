# Jahresshow

Slideshow aus Fotos und Videos mit eigener Musik und Texten als MP4. Für Entwicklung und
Tests unter Windows 10/11. Das fertige Paket (`Jahresshow.exe`, ohne Python) entsteht in
Meilenstein 4.

## Voraussetzungen

- **Python 3.12.10** von python.org (64 Bit, mit dem Python-Launcher `py`); damit ist
  `.venv` angelegt und alles getestet. Prüfen in PowerShell: `py -3.12 --version` muss
  `Python 3.12.10` melden.
- **FFmpeg**: Full Build von gyan.dev. `ffmpeg.exe` und `ffprobe.exe` kommen in den Ordner
  `bin\` im Projektordner (nicht im Git).
- **Kurzer Projektpfad**, z. B. `C:\dev\jahresshow`. PySide6 enthält sehr tief
  verschachtelte Dateien. In einem langen Pfad bricht die Installation mit
  „No such file or directory … Long Path support“ ab, solange Windows lange Pfade nicht
  erlaubt. Alternative: lange Pfade einmalig erlauben (PowerShell als Administrator):

  ```powershell
  New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
      -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force
  ```

## Einmalig einrichten

In einer normalen PowerShell im Projektordner:

```powershell
cd C:\dev\jahresshow
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Das legt die virtuelle Umgebung `.venv` an und installiert alle Abhängigkeiten aus
`pyproject.toml`: Pillow, pillow-heif, PySide6 sowie für die Entwicklung pytest, pytest-qt
und ruff. Dauer: einige Minuten; PySide6 belegt installiert rund 650 MB. Mit `-e` wirken
Änderungen am Code sofort, ohne neu zu installieren.

## Starten

```powershell
.venv\Scripts\python.exe -m slideshow
```

Oder kürzer: `.venv\Scripts\jahresshow.exe`. Mit einer Projektdatei oder Fotos/Videos:
`.venv\Scripts\python.exe -m slideshow gui "D:\Fotos\Projekt 2026.json"`.

Es läuft immer nur ein Fenster. Ein zweiter Start holt das vorhandene Fenster nach vorne und
öffnet die mitgegebenen Dateien dort.

### „No module named PIL“?

Dann wurde das System-Python gestartet (`python -m slideshow`) statt dem aus `.venv`. Die
Pakete sind nur in `.venv` installiert. Abhilfe: wie oben mit `.venv\Scripts\python.exe`
starten.

Die Umgebung zu „aktivieren“ (`.venv\Scripts\Activate.ps1`) ist nicht nötig. In einer
normalen PowerShell verhindert die Windows-Voreinstellung (Ausführungsrichtlinie
„Restricted“) das meist ohnehin. Wer es trotzdem will, erlaubt es einmalig für den eigenen
Benutzer: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## Kommandozeile

```powershell
# Projekt als MP4 rendern (Kodierer wird automatisch erkannt)
.venv\Scripts\python.exe -m slideshow render tests\demo.json -o out\demo.mp4
# schnelle Vorschau in 480p, nur Elemente 3 bis 8
.venv\Scripts\python.exe -m slideshow render projekt.json --preview --from 3 --to 8
# welche Kodierer (Grafikkarte/Prozessor) funktionieren hier?
.venv\Scripts\python.exe -m slideshow encoders
```

Zwischendateien (umgewandelte Videos, Segmente, Vorschaubilder) liegen in
`%LOCALAPPDATA%\Jahresshow\cache` und können jederzeit gelöscht werden, dann wird neu
berechnet.

## Windows-Paket bauen

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev,build]"   # einmalig: PyInstaller
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

Ergebnis ist der Ordner `dist\Jahresshow` (PyInstaller onedir) mit `Jahresshow.exe`,
`ffmpeg.exe`, `ffprobe.exe`, `README.txt` für Endnutzer und dem Ordner `Lizenzen`. Weitergeben
wird der ganze Ordner, zum Beispiel als ZIP. Jeder Lauf baut von vorn; die Spezifikation steht
in `packaging\Jahresshow.spec`, die Endnutzer-README in `packaging\README.txt`.

Die gepackte exe ist ein Fensterprogramm. Auf der Kommandozeile rendert sie mit
`Jahresshow.exe --render projekt.json -o video.mp4`. PowerShell wartet bei Fensterprogrammen
nur, wenn die Ausgabe weitergeleitet wird, also mit `| Out-Host` am Ende.

Die Tests in `tests\test_package.py` prüfen das gebaute Paket (frische PowerShell ohne
Python, 50 Fotos, Hilfsprozesse, Render mit ffprobe-Prüfung). Ohne `dist\Jahresshow` werden
sie übersprungen.

## Prüfen vor jedem Commit

```powershell
.venv\Scripts\ruff.exe check .
.venv\Scripts\python.exe -m pytest
```

- Nur die schnellen Tests (ohne echte Render-Läufe): `-m "not slow"`
- Testmedien in `tests\media\synthetic\` entstehen beim ersten Lauf automatisch per FFmpeg.
- Tests für echte Beispieldateien laufen nur, wenn `tests\media\real\` existiert.
- Die Screenshot-Tests legen Bilder des Fensters (1366×768 und 150 % Skalierung, Windows-10-
  und Windows-11-Stil) in `out\screens\` ab.
