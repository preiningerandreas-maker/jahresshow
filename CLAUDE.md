# Jahresshow – Slideshow-Programm

Schlankes Windows-Programm, das aus Fotos und Videos eine Slideshow mit eigener Musik und Texten als MP4 erzeugt. Fokus: Jahresrückblick mit wenig Aufwand. Kein MAGIX-Klon.

## Zielgeräte
- Entwicklungs-PC: Win 11, i9-12900KF, RTX 4080 (NVENC)
- Laptop: ThinkPad T490s, Win 11 Pro, i5-8365U, Intel UHD 620 (QSV). Dort wird auch zusammengestellt, nicht nur gerendert.
- **Mindest-Hardware und Entwicklungsgerät für den Start:** ThinkPad T450s, **Windows 10 Pro**, 8 GB RAM (Broadwell, 2 Kerne/4 Threads, Intel HD 5500). QSV kann dort H.264 kodieren, aber **kein HEVC 10 Bit/HDR in Hardware dekodieren**. Die Normalisierung der S23-Videos läuft dort über die CPU und ist langsam: Fortschritt und Restzeit anzeigen, Normalisierung im Hintergrund, Ergebnis dauerhaft cachen. Alles muss auf diesem Gerät funktionieren, nur langsamer.
- Läuft standalone als .exe, ohne Python-Installation, ohne Internet, ohne VPS.

## Umfang Version 1
1. Ordner oder Dateien importieren: JPG, PNG, HEIC, MP4, MOV (inkl. Samsung-S23-HEVC, 10 Bit, HDR)
2. Automatisch nach EXIF-Aufnahmedatum sortieren, EXIF-Drehung anwenden
3. Automatische Kapitel pro Monat mit Zwischentitel (abschaltbar)
4. Zeitleiste mit Vorschaubildern, Reihenfolge per Drag & Drop
5. Pro Element: Anzeigedauer, Text-Overlay (Position, Schrift, Größe, Farbe, halbtransparenter Balken), Formatmodus
6. Formatmodus bei Hochkant oder abweichendem Seitenverhältnis: `blur` (Standard), `black`, `crop`
7. Titelkarte am Anfang: Text auf Farbfläche oder auf Foto
8. Übergänge: Crossfade (Standard) plus 3 Alternativen. Keine Animationen, kein Ken Burns in V1.
9. Musik: Playlist aus lokalen Dateien (MP3, M4A, WAV, FLAC), Fade-out am Ende, Schleife falls zu kurz, Musik auf 25 % absenken während Clips mit Originalton
10. Projekt speichern/laden als JSON, Vorlage (Schrift, Übergang, Musikordner) für nächstes Jahr wiederverwendbar
11. Vorschau eines gewählten Abschnitts in 480p
12. Export MP4

## Unterstützte Quellen (handyunabhängig)
Erkennung immer über Metadaten (ffprobe/EXIF), nie über Marke, Modell oder Dateinamen.
- H.264 8 Bit SDR (ältere Android, WhatsApp): direkt normalisieren
- HEVC 8/10 Bit SDR (aktuelle Handys ohne HDR): 10 Bit auf 8 Bit, bt709
- HEVC SDR mit bt2020-Primärfarben (manche Handys ohne HDR): per zscale Farbraum nach bt709 umrechnen, ohne Tonemapping
- HDR10/HDR10+ (PQ, smpte2084, Samsung): Tonemapping nach bt709
- HLG (arib-std-b67, Pixel, manche Android): Tonemapping nach bt709
- Dolby Vision Profil 8.4 (iPhone): als HLG behandeln, DV-Metadaten ignorieren
- Drehung nur in Metadaten (rotate/displaymatrix, alle Handys): korrekt drehen
- Hohe Bildraten 60/120/240 fps und VFR: auf 30 fps CFR
- MOV-Container (iPhone): wie MP4
- JPG, HEIC/HEIF, PNG: EXIF-Drehung und Datum
- Live Photos (iPhone HEIC+MOV) und Motion Photos (Samsung JPG mit Video): V1 nur Standbild
- Fehlende Farbmetadaten (WhatsApp, Bearbeitungs-Apps): als bt709 annehmen
Das Endvideo hat immer explizit gesetzte Farbmetadaten bt709 (color_primaries, color_transfer, color_space).

## Nicht-Ziele V1
Animationen, Ken Burns, Collagen, Rahmen, Beat-Sync, DVD, Cloud, Mehrsprachigkeit.

## Stack
- Python 3.12.10 (wie in `.venv`), PySide6, Pillow, pillow-heif, pytest, pytest-qt, ruff
- FFmpeg/ffprobe als externe Binaries in `bin/` (gyan.dev full build, nicht im Git)
- Verpackung: PyInstaller **onedir**, ffmpeg.exe und ffprobe.exe im Ausgabeordner

## Architektur
```
slideshow/
├── app/        PySide6-GUI
├── core/
│   ├── project.py   Dataclasses, JSON (versioniertes Schema, "schema_version")
│   ├── importer.py  Metadaten, EXIF, ffprobe
│   ├── normalize.py Videos einmalig normalisieren (siehe unten)
│   ├── chapters.py  Kapitel pro Monat
│   └── thumbs.py    Thumbnail-Cache
├── render/
│   ├── encoder.py   Encoder-Erkennung
│   ├── segment.py   Ein Segment pro Element
│   ├── text.py      Text als PNG via Pillow
│   ├── audio.py     Musikspur
│   └── assemble.py  Kapitelweise xfade, dann concat
└── cli.py      python -m slideshow render <projekt.json> [--encoder X] [--preview]
```

## Render-Regeln (verbindlich)
- Ausgabe: 1920×1080, 30 fps konstant, yuv420p, H.264 High Level 4.1, AAC 48 kHz 192 kbit/s, `-movflags +faststart`
- Videobitrate max. 8 Mbit/s, damit 1 h Show unter 4 GB bleibt (FAT32-USB-Sticks). Vor dem Export geschätzte Dateigröße anzeigen, über 4 GB warnen.
- Optionaler 4K-Export, nicht Standard
- **Nie** einen einzigen Filtergraph für die ganze Show. Pro Element ein Segment, pro Kapitel xfade in Blöcken von max. 20 Segmenten, dann concat.
- Segmente cachen, Schlüssel = Hash aus Quelldatei (Pfad, Größe, mtime) plus Elementeinstellungen. Geänderte Elemente neu, Rest aus Cache. Abbruch und Fortsetzen möglich.

### Encoder-Erkennung
Beim Start Probe-Encode (4 s wechselnde Inhalte, gleiche Parameter wie Segmente und Endvideo) in dieser Reihenfolge: `h264_nvenc`, `h264_qsv`, `h264_amf`, `libx264`. Erster funktionierender gewinnt, Ergebnis cachen. `--encoder` erzwingt einen bestimmten. libx264 muss immer funktionieren.
Versagt ein Hardware-Kodierer beim echten Export, wird der Schritt mit libx264 wiederholt und der Rest des Laufs bleibt bei libx264; Abbruch nur, wenn auch libx264 scheitert. Die Panne wird pro Gerät gemerkt (`grafikkarte.json` im Cache), der nächste Export nimmt gleich libx264 und sagt das. Einstellung „Grafikkarte verwenden: automatisch / nie“ unter Extras.

### Video-Normalisierung beim Import
Jedes Video einmalig in Arbeitsformat umwandeln: 1080p (Höhe max. 1080), 30 fps CFR (VFR-Handyvideos sonst asynchron), SDR yuv420p H.264, Audio AAC 48 kHz. HDR (bt2020/smpte2084/arib-std-b67) per zscale + tonemap=hable nach bt709. Hardware-Dekodierung wenn verfügbar, sonst CPU. Fortschritt in der GUI anzeigen.

### Blur-Fill
Hintergrund: Quelle auf 1/16 verkleinern, boxblur, auf 1920×1080 füllend skalieren und beschneiden, Helligkeit -0.15. Vordergrund: Original auf Höhe 1080, zentriert darüber. Gilt für Fotos und Videos.

### Text
Text mit Pillow als transparentes PNG rendern, per overlay einblenden. Kein drawtext (Font-Pfade unter Windows). Schrift mitliefern (Noto Sans oder Inter, OFL-Lizenz), Umlaute und ß müssen funktionieren.

## Robustheit
- Pfade mit Umlauten und Leerzeichen ("Urlaub Wörthersee 2026") überall testen
- subprocess nur mit Argumentlisten, nie `shell=True`
- Vor dem Rendern freien Speicherplatz im Temp-Ordner prüfen (Faustregel: 3× Quellgröße), sonst klare Fehlermeldung
- Samsung Motion Photos: in V1 nur das Standbild verwenden
- Duplikate (gleicher Hash) beim Import melden, nicht stillschweigend entfernen
- Rendern läuft im Hintergrund-Thread, abbrechbar, GUI bleibt bedienbar

## GUI-Regeln
- Muss bei 1920×1080 mit 150 % Windows-Skalierung voll bedienbar sein (T490s)
- Thumbnails lazy laden, Zeitleiste flüssig bei 1000 Elementen
- Einfache Sprache, deutsche Oberfläche, keine Fachbegriffe wie "Encoder" ohne Erklärung

## Testdaten
- `tests/media/synthetic/`: wird von `tests/make_media.py` per FFmpeg erzeugt (Quer- und Hochformat, Video mit Ton, VFR-Video, Dateinamen mit Umlauten). Im Git.
- `tests/media/real/`: echte Beispieldateien von Andreas (HEIC, S23-HEVC-HDR, Hochkant). **Nicht im Git** (.gitignore). Tests dafür werden übersprungen, wenn der Ordner fehlt.

## Arbeitsweise
- Code-Bezeichner Englisch, Oberfläche und Kommentare für den Nutzer Deutsch
- Type Hints, `ruff check` sauber, `pytest` grün vor jedem Commit
- Arbeiten auf Branch pro Meilenstein, kleine Commits
- Keine neuen Abhängigkeiten ohne Begründung im Commit
- Tests nie abschwächen, damit sie bestehen. Schlägt ein Test fehl, wird der Code repariert. Nur wenn der Test nachweislich falsch ist, darf er geändert werden, mit Begründung im Commit.
- Antworten an Andreas immer auf Deutsch und per Du.
