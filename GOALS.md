# Goals für Claude Code

Vorbereitung (einmalig, von Andreas):
1. Leeres Repo `jahresshow`, CLAUDE.md hineinlegen
2. FFmpeg full build von gyan.dev entpacken, `ffmpeg.exe` und `ffprobe.exe` nach `bin/`
3. 15–20 echte Beispieldateien nach `tests/media/real/` (HEIC, S23-Video hochkant, S23-Video mit HDR, ein Video mit Ton, ein Ordnername mit Umlaut, 2 eigene Musiktitel)
4. Branch pro Meilenstein: `git switch -c m1-render`

Jeweils in einer neuen Session starten. Nach jedem Goal: Diff reviewen, mergen, nächster Branch.

---

## M1 – Render-Kern (ohne GUI)

```
/goal Der Render-Kern laut CLAUDE.md ist umgesetzt (core/project.py, render/*, cli.py, tests/make_media.py). Erfüllt, wenn: (1) `ruff check .` ohne Fehler, (2) `pytest` grün, (3) `python -m slideshow render tests/demo.json -o out/demo.mp4` erzeugt eine MP4, bei der ffprobe 1920x1080, 30 fps, h264, yuv420p, aac und eine Dauer innerhalb ±0,5 s der berechneten Projektdauer meldet, (4) derselbe Aufruf mit `--encoder libx264` ebenfalls erfolgreich ist, (5) demo.json enthält Titelkarte, Querformat-Foto, Hochkant-Foto mit blur, Video mit Ton, Text-Overlay mit Umlauten, Crossfade und 2 Musiktitel, (6) ein zweiter Render-Lauf ohne Änderungen nutzt den Segment-Cache und ist mindestens 5× schneller. Prüfergebnisse als Ausgabe im Transcript zeigen.
```

## M2 – Import und Normalisierung

```
/goal Import laut CLAUDE.md ist umgesetzt (core/importer.py, normalize.py, chapters.py, thumbs.py). Erfüllt, wenn `pytest` grün ist und Tests nachweisen: (1) Sortierung nach EXIF-Datum, Fallback auf Dateidatum, (2) EXIF-Drehung korrekt, (3) HEIC wird gelesen, (4) VFR-Video wird zu 30 fps CFR normalisiert, Audio-Video-Versatz unter 50 ms laut ffprobe, (5) HDR-Video wird nach bt709 tonemapped (ffprobe color_transfer nicht smpte2084/arib), (6) Kapitel pro Monat mit korrekten Zwischentiteln, (7) Duplikate werden gemeldet, (8) Pfade mit Umlauten funktionieren. Tests für tests/media/real/ laufen mit, wenn der Ordner existiert, sonst skip.
```

## M3 – GUI

```
/goal PySide6-GUI laut CLAUDE.md ist umgesetzt: Import (Ordner/Dateien), Zeitleiste mit Lazy-Thumbnails und Drag & Drop, Eigenschaftenbereich (Dauer, Text, Formatmodus, Übergang), Titelkarte, Musik-Playlist, Projekt speichern/laden, Vorschau-Abschnitt 480p, Export mit Fortschritt und Abbrechen im Hintergrund-Thread, Größenwarnung über 4 GB. Erfüllt, wenn: (1) `pytest` grün inkl. pytest-qt-Tests für Laden/Speichern, Reihenfolge ändern und Export-Start/Abbruch, (2) ein mit der GUI gespeichertes Projekt über die CLI identisch rendert, (3) Fenster bei 1366x768 logischer Auflösung ohne abgeschnittene Bedienelemente startet (Screenshot-Test). Danach stoppen: Abnahme der Bedienbarkeit macht Andreas am T490s.
```

## M4 – Paket

```
/goal Windows-Paket per PyInstaller onedir ist erstellt. Erfüllt, wenn: (1) `build.ps1` reproduzierbar `dist/Jahresshow/Jahresshow.exe` erzeugt, inkl. ffmpeg.exe, ffprobe.exe und Schrift, (2) die exe auf Windows 10 und Windows 11 läuft (Build auf dem ältesten Zielsystem bzw. mit Python 3.12, das Win 10 unterstützt) und in einer frischen PowerShell ohne aktiviertes venv und mit PATH ohne Python startet, (3) `Jahresshow.exe --render tests/demo.json -o out/pkg.mp4` die gleiche ffprobe-Prüfung wie in M1 besteht, (4) Ordnergröße unter 400 MB, (5) README.txt auf Deutsch erklärt Start und Systemanforderungen.
```

---

## Abnahme durch Andreas (nicht automatisierbar)
- [ ] Am T490s ein echtes Monatsprojekt zusammenstellen: flüssig bedienbar?
- [ ] Renderzeit T490s für 10 min Show notieren (QSV und libx264)
- [ ] MP4 vom USB-Stick am Fernseher abspielen
- [ ] Blur-Fill bei Hochkant-Video am Fernseher anschauen
- [ ] Windows Defender meldet nichts bei der exe
